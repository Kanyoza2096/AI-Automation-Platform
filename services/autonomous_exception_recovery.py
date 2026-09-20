"""V67 — bounded autonomous exception detection and recovery governance.

This module is intentionally an *authority-free* recovery brain. It can classify
execution failures and construct a bounded recovery plan, but it cannot authorize
arbitrary side effects. Any approved retry/reconciliation must still pass through
DecisionGate/Council where required and the Action Kernel for execution.

Safety law:
    uncertain external state => reconcile first, never blind-retry
    destructive/irreversible recovery => never autonomous
    repeated failure => quarantine/review, never infinite retry
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class ExceptionClass(str, Enum):
    TRANSIENT_PROVIDER = "transient_provider"
    RATE_LIMIT = "rate_limit"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    AUTHENTICATION = "authentication"
    VALIDATION = "validation"
    POLICY_BLOCK = "policy_block"
    UNCERTAIN_EXTERNAL = "uncertain_external"
    PARTIAL_EXECUTION = "partial_execution"
    UNKNOWN_FAILURE = "unknown_failure"


class RecoveryDisposition(str, Enum):
    AUTO_RECOVER = "auto_recover"
    RECONCILE_FIRST = "reconcile_first"
    REVIEW_REQUIRED = "review_required"
    BLOCKED = "blocked"
    QUARANTINED = "quarantined"


class RecoveryAction(str, Enum):
    RETRY_EXISTING_INTENT = "retry_existing_intent"
    RECONCILE_EXTERNAL_STATE = "reconcile_external_state"
    REFRESH_DEPENDENCY_AUTH = "refresh_dependency_auth"
    NONE = "none"


@dataclass(frozen=True)
class ExceptionObservation:
    workspace_id: str
    execution_id: str
    action_type: str
    observed_status: str
    error: str = ""
    error_code: Optional[str] = None
    provider: Optional[str] = None
    attempt: int = 0
    intent_id: Optional[str] = None
    correlation_id: Optional[str] = None
    external_state_uncertain: bool = False
    partial_execution: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def classification(self) -> ExceptionClass:
        status = self.observed_status.lower().strip()
        code = str(self.error_code or "").lower().strip()
        text = self.error.lower()
        if self.partial_execution or "partial" in text:
            return ExceptionClass.PARTIAL_EXECUTION
        if self.external_state_uncertain or status in {"uncertain", "reconciliation_required"}:
            return ExceptionClass.UNCERTAIN_EXTERNAL
        if code in {"401", "403", "auth", "authentication", "invalid_token", "token_expired"} or "unauthorized" in text or "authentication" in text:
            return ExceptionClass.AUTHENTICATION
        if code in {"429", "rate_limit", "too_many_requests"} or "rate limit" in text or "too many requests" in text:
            return ExceptionClass.RATE_LIMIT
        if code in {"validation", "invalid_parameter", "bad_request", "400"} or "validation" in text or "invalid parameter" in text:
            return ExceptionClass.VALIDATION
        if code in {"policy", "policy_block", "forbidden_action"} or "policy" in text:
            return ExceptionClass.POLICY_BLOCK
        if code in {"timeout", "network", "connection", "503", "502", "504"} or any(x in text for x in ("timeout", "timed out", "connection reset", "temporarily unavailable", "service unavailable")):
            return ExceptionClass.TRANSIENT_PROVIDER
        if code in {"dependency_unavailable", "dependency"}:
            return ExceptionClass.DEPENDENCY_UNAVAILABLE
        if status == "failed":
            return ExceptionClass.UNKNOWN_FAILURE
        return ExceptionClass.UNKNOWN_FAILURE

    @property
    def fingerprint(self) -> str:
        material = "|".join([
            self.workspace_id, self.action_type, self.classification.value,
            str(self.error_code or ""), (self.provider or ""),
            self.error[:300].strip().lower(),
        ])
        return "exf_" + hashlib.sha256(material.encode()).hexdigest()[:32]

    @property
    def exception_id(self) -> str:
        material = f"{self.workspace_id}|{self.execution_id}|{self.fingerprint}"
        return "exc_" + hashlib.sha256(material.encode()).hexdigest()[:24]


@dataclass(frozen=True)
class RecoveryPlan:
    plan_id: str
    exception_id: str
    workspace_id: str
    action_type: str
    disposition: RecoveryDisposition
    action: RecoveryAction
    reason: str
    max_attempts: int
    next_delay_seconds: int
    requires_human_approval: bool
    requires_reconciliation: bool
    authority: str = "none"
    steps: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "exception_id": self.exception_id,
            "workspace_id": self.workspace_id,
            "action_type": self.action_type,
            "disposition": self.disposition.value,
            "action": self.action.value,
            "reason": self.reason,
            "max_attempts": self.max_attempts,
            "next_delay_seconds": self.next_delay_seconds,
            "requires_human_approval": self.requires_human_approval,
            "requires_reconciliation": self.requires_reconciliation,
            "authority": self.authority,
            "steps": list(self.steps),
            "metadata": self.metadata,
        }


class RecoveryGovernance:
    """Deterministic, fail-closed policy for bounded recovery."""

    MAX_AUTO_ATTEMPTS = 3
    MAX_DELAY_SECONDS = 3600

    def plan(
        self,
        observation: ExceptionObservation,
        *,
        idempotent: bool,
        irreversible: bool = False,
        requires_approval: bool = False,
        has_reconciler: bool = False,
        prior_failures: int = 0,
    ) -> RecoveryPlan:
        cls = observation.classification
        attempts = max(0, int(prior_failures or observation.attempt or 0))
        delay = min(self.MAX_DELAY_SECONDS, 30 * (2 ** min(attempts, 6)))
        common = {
            "exception_id": observation.exception_id,
            "workspace_id": observation.workspace_id,
            "action_type": observation.action_type,
        }

        def make(disposition, action, reason, *, max_attempts=0, approval=False, reconcile=False, steps=()):
            material = json.dumps({**common, "d": disposition.value, "a": action.value, "attempts": attempts}, sort_keys=True)
            pid = "rec_" + hashlib.sha256(material.encode()).hexdigest()[:24]
            return RecoveryPlan(pid, observation.exception_id, observation.workspace_id, observation.action_type,
                                disposition, action, reason, max_attempts, delay,
                                approval, reconcile, "none", tuple(steps),
                                {"classification": cls.value, "attempt": attempts, "fingerprint": observation.fingerprint})

        # The most important V67 invariant: uncertain external state is never
        # treated as a normal retryable failure.
        if cls in {ExceptionClass.UNCERTAIN_EXTERNAL, ExceptionClass.PARTIAL_EXECUTION}:
            if not has_reconciler:
                return make(RecoveryDisposition.REVIEW_REQUIRED, RecoveryAction.RECONCILE_EXTERNAL_STATE,
                            "External state is uncertain and no trusted reconciler is registered.", approval=True, reconcile=True,
                            steps=("freeze duplicate retry", "reconcile provider state", "record authoritative outcome"))
            return make(RecoveryDisposition.RECONCILE_FIRST, RecoveryAction.RECONCILE_EXTERNAL_STATE,
                        "External state must be reconciled before any retry.", reconcile=True,
                        steps=("freeze duplicate retry", "reconcile provider state", "only retry after authoritative reconciliation"))

        if irreversible or requires_approval:
            return make(RecoveryDisposition.REVIEW_REQUIRED, RecoveryAction.RETRY_EXISTING_INTENT,
                        "Recovery touches a governed or irreversible action; autonomous recovery is not permitted.",
                        approval=True, steps=("preserve failed intent", "request governed approval", "execute only through Action Kernel"))

        if cls in {ExceptionClass.VALIDATION, ExceptionClass.POLICY_BLOCK}:
            return make(RecoveryDisposition.BLOCKED, RecoveryAction.NONE,
                        "The failure indicates invalid input or policy rejection; retrying unchanged would be unsafe.",
                        steps=("retain evidence", "block automatic retry", "route for correction or review"))

        if cls == ExceptionClass.AUTHENTICATION:
            return make(RecoveryDisposition.REVIEW_REQUIRED, RecoveryAction.REFRESH_DEPENDENCY_AUTH,
                        "Authentication failures require credential/provider recovery outside autonomous side-effect authority.",
                        approval=True, steps=("pause retries", "repair provider authorization", "revalidate connection", "resume through Action Kernel"))

        if cls in {ExceptionClass.TRANSIENT_PROVIDER, ExceptionClass.RATE_LIMIT, ExceptionClass.DEPENDENCY_UNAVAILABLE}:
            if not idempotent:
                return make(RecoveryDisposition.REVIEW_REQUIRED, RecoveryAction.RETRY_EXISTING_INTENT,
                            "Transient failure is retryable only when the original action is idempotent.", approval=True,
                            steps=("preserve intent", "verify idempotency", "retry only after approval"))
            if attempts >= self.MAX_AUTO_ATTEMPTS:
                return make(RecoveryDisposition.QUARANTINED, RecoveryAction.NONE,
                            "Automatic retry budget exhausted; repeated failure requires review.",
                            steps=("stop automatic retries", "quarantine intent", "request operator review"))
            return make(RecoveryDisposition.AUTO_RECOVER, RecoveryAction.RETRY_EXISTING_INTENT,
                        "Bounded idempotent retry is permitted for a transient dependency failure.",
                        max_attempts=self.MAX_AUTO_ATTEMPTS, steps=("wait bounded backoff", "retry existing intent", "record authoritative outcome"))

        if attempts >= self.MAX_AUTO_ATTEMPTS:
            return make(RecoveryDisposition.QUARANTINED, RecoveryAction.NONE,
                        "Unknown failure repeated beyond the autonomous retry budget.",
                        steps=("stop automatic retries", "quarantine intent", "request review"))
        return make(RecoveryDisposition.REVIEW_REQUIRED, RecoveryAction.NONE,
                    "Unknown failures are not safe for autonomous recovery without a specific deterministic policy.",
                    approval=True, steps=("preserve evidence", "request review", "do not invent a recovery action"))


class AutonomousExceptionRecovery:
    """Thin orchestration facade; it plans, but never executes side effects."""

    def __init__(self, governance: Optional[RecoveryGovernance] = None):
        self.governance = governance or RecoveryGovernance()

    def observe(self, **kwargs: Any) -> ExceptionObservation:
        return ExceptionObservation(**kwargs)

    def evaluate(self, observation: ExceptionObservation, **kwargs: Any) -> RecoveryPlan:
        return self.governance.plan(observation, **kwargs)

@dataclass(frozen=True)
class RecoveryRelease:
    """Control-plane handoff to the Action Outbox worker; not an execution receipt."""
    plan_id: str
    intent_id: str
    workspace_id: str
    available_at_delay_seconds: int
    approved: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "intent_id": self.intent_id,
            "workspace_id": self.workspace_id,
            "available_at_delay_seconds": self.available_at_delay_seconds,
            "approved": self.approved,
            "execution_authority": "action_kernel",
        }


def build_recovery_release(plan: RecoveryPlan, *, intent_id: str) -> RecoveryRelease:
    """Create a bounded retry handoff only for an AUTO_RECOVER plan.

    This function deliberately does not call a connector, provider, Action Kernel
    handler, or external API. A worker must consume the handoff through the durable
    Action Outbox and re-enter the existing Action Kernel execution boundary.
    """
    if plan.disposition is not RecoveryDisposition.AUTO_RECOVER:
        raise ValueError("recovery plan is not authorized for autonomous release")
    if plan.action is not RecoveryAction.RETRY_EXISTING_INTENT:
        raise ValueError("recovery plan does not permit an intent retry")
    if plan.requires_human_approval or plan.requires_reconciliation:
        raise ValueError("recovery plan still requires governance before release")
    if not intent_id.strip():
        raise ValueError("intent_id is required")
    return RecoveryRelease(
        plan_id=plan.plan_id,
        intent_id=intent_id,
        workspace_id=plan.workspace_id,
        available_at_delay_seconds=min(max(0, plan.next_delay_seconds), RecoveryGovernance.MAX_DELAY_SECONDS),
        approved=True,
    )


class RecoveryCoordinator:
    """Durable V67 coordinator between failed outbox work and bounded recovery.

    The coordinator owns control-plane persistence only. AUTO_RECOVER is released
    through the database function which atomically requires the intent to be in
    terminal ``failed`` state; execution remains exclusively in ActionOutboxWorker
    -> ActionKernel.
    """

    def __init__(self, db=None, recovery: Optional[AutonomousExceptionRecovery] = None):
        self.db = db
        self.recovery = recovery or AutonomousExceptionRecovery()

    async def handle_failure(
        self,
        *,
        row: dict[str, Any],
        result: dict[str, Any],
        idempotent: bool,
        irreversible: bool = False,
        requires_approval: bool = False,
        has_reconciler: bool = False,
    ) -> dict[str, Any]:
        import asyncio

        workspace_id = str(row.get("workspace_id") or "").strip()
        execution_id = str(result.get("execution_id") or row.get("execution_id") or "").strip()
        intent_id = str(row.get("intent_id") or "").strip()
        action_type = str(row.get("action_type") or "").strip()
        if not workspace_id or not intent_id or not action_type:
            return {"ok": False, "status": "not_recoverable", "reason": "missing recovery identity"}

        error = str(result.get("error") or (result.get("output") or {}).get("error") or row.get("error") or "")
        status = str(result.get("status") or row.get("status") or "failed")
        observation = self.recovery.observe(
            workspace_id=workspace_id, execution_id=execution_id or intent_id,
            action_type=action_type, observed_status=status, error=error,
            error_code=(result.get("error_code") or row.get("error_code")),
            provider=str(row.get("provider") or "") or None,
            attempt=int(row.get("attempts") or row.get("attempt") or 0),
            intent_id=intent_id, correlation_id=row.get("correlation_id"),
            external_state_uncertain=bool(result.get("reconciliation_required")) or status == "uncertain",
            partial_execution=bool(result.get("partial_execution")) or str((result.get("output") or {}).get("delivery_status") or "").lower() == "partial",
            metadata={"source": "action_outbox_worker", "idempotent": bool(idempotent)},
        )
        plan = self.recovery.evaluate(
            observation, idempotent=idempotent, irreversible=irreversible,
            requires_approval=requires_approval, has_reconciler=has_reconciler,
            prior_failures=int(row.get("attempts") or 0),
        )

        if self.db is not None:
            await asyncio.to_thread(self._persist, observation, plan)

        if plan.disposition is RecoveryDisposition.AUTO_RECOVER:
            release = build_recovery_release(plan, intent_id=intent_id)
            if self.db is None:
                return {"ok": True, "status": "auto_recover_planned", "plan": plan.to_dict(), "release": release.to_dict()}
            rpc = await asyncio.to_thread(lambda: self.db.rpc("release_action_outbox_for_recovery", {
                "p_intent_id": release.intent_id,
                "p_workspace_id": release.workspace_id,
                "p_plan_id": release.plan_id,
                "p_delay_seconds": release.available_at_delay_seconds,
            }).execute())
            released = bool(getattr(rpc, "data", False))
            # The release RPC owns the exception-state transition atomically with
            # failed -> pending. Do not issue a second best-effort UPDATE here:
            # a crash or race after the RPC must not leave an exception claiming
            # recovery_planned when the outbox release did not actually win.
            return {"ok": released, "status": "released" if released else "release_conflict",
                    "plan": plan.to_dict(), "release": release.to_dict()}

        state = {
            RecoveryDisposition.RECONCILE_FIRST: "reconciling",
            RecoveryDisposition.REVIEW_REQUIRED: "review_required",
            RecoveryDisposition.BLOCKED: "blocked",
            RecoveryDisposition.QUARANTINED: "quarantined",
        }.get(plan.disposition, "open")
        if self.db is not None:
            await asyncio.to_thread(lambda: self.db.table("autonomous_exceptions").update({"state": state}).eq("exception_id", observation.exception_id).execute())
        return {"ok": True, "status": state, "plan": plan.to_dict()}

    def _persist(self, observation: ExceptionObservation, plan: RecoveryPlan) -> None:
        self.db.table("autonomous_exceptions").upsert({
            "exception_id": observation.exception_id,
            "workspace_id": observation.workspace_id,
            "execution_id": observation.execution_id,
            "intent_id": observation.intent_id,
            "action_type": observation.action_type,
            "classification": observation.classification.value,
            "fingerprint": observation.fingerprint,
            "observed_status": observation.observed_status,
            "error": observation.error[:2000],
            "attempt": observation.attempt,
            "external_state_uncertain": observation.external_state_uncertain,
            "partial_execution": observation.partial_execution,
            # For AUTO_RECOVER, the plan is durable evidence but it is not yet a
            # successful release. Keep the exception open until the transactional
            # release RPC atomically moves the outbox failed -> pending and marks
            # the exception recovery_planned. This makes crash/retry semantics
            # truthful: a crash between persistence and release is recoverable,
            # while a release race cannot falsely report that recovery was queued.
            "state": "open",
            "metadata": observation.metadata,
        }).execute()
        self.db.table("autonomous_recovery_plans").upsert({
            "plan_id": plan.plan_id,
            "exception_id": plan.exception_id,
            "workspace_id": plan.workspace_id,
            "action_type": plan.action_type,
            "disposition": plan.disposition.value,
            "recovery_action": plan.action.value,
            "reason": plan.reason,
            "max_attempts": plan.max_attempts,
            "next_delay_seconds": plan.next_delay_seconds,
            "requires_human_approval": plan.requires_human_approval,
            "requires_reconciliation": plan.requires_reconciliation,
            "authority": plan.authority,
            "steps": list(plan.steps),
            "metadata": plan.metadata,
        }).execute()
