"""V69.14 — governed integration recovery and reconciliation plane.

This module resolves ambiguous external state before any retry.  It is an
authority-free control-plane component: it may classify, reconcile and produce
an executable recovery recommendation, but it never invokes provider mutations.
Actual retries remain behind the existing governed connector/Action Kernel.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Protocol

from services.autonomous_exception_recovery import (
    ExceptionObservation, RecoveryGovernance, RecoveryDisposition, RecoveryAction,
)


class RecoveryBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    MISSING_RECONCILER = "missing_reconciler"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    RECONCILIATION_INCONCLUSIVE = "reconciliation_inconclusive"
    RETRY_UNSAFE = "retry_unsafe"
    RETRY_BUDGET_EXHAUSTED = "retry_budget_exhausted"
    REVIEW_REQUIRED = "review_required"


class ReconciliationState(str, Enum):
    CONFIRMED_SUCCESS = "confirmed_success"
    CONFIRMED_ABSENT = "confirmed_absent"
    INCONCLUSIVE = "inconclusive"
    ERROR = "error"


@dataclass(frozen=True)
class ReconciliationObservation:
    workspace_id: str
    execution_id: str
    state: ReconciliationState
    provider_object_id: str | None = None
    provider_request_id: str | None = None
    evidence: str = ""
    attempts: int = 0
    authoritative: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.workspace_id or not self.execution_id:
            raise ValueError("workspace_id and execution_id are required")
        if self.state in {ReconciliationState.CONFIRMED_SUCCESS, ReconciliationState.CONFIRMED_ABSENT} and not self.authoritative:
            raise ValueError("terminal reconciliation state requires authoritative evidence")


class Reconciler(Protocol):
    def reconcile(self, *, idempotency_key: str, correlation_id: str | None = None) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class RecoveryDecision:
    workspace_id: str
    execution_id: str
    disposition: RecoveryDisposition
    action: RecoveryAction
    allowed: bool
    reason_codes: tuple[str, ...] = ()
    steps: tuple[str, ...] = ()
    next_delay_seconds: int = 0
    requires_human_approval: bool = False
    reconciliation: ReconciliationObservation | None = None


class IntegrationRecoveryCoordinator:
    """Single governed decision point for ambiguous integration outcomes."""

    def __init__(self, governance: RecoveryGovernance | None = None):
        self.governance = governance or RecoveryGovernance()

    def classify(self, observation: ExceptionObservation, *, idempotent: bool,
                 irreversible: bool = False, requires_approval: bool = False,
                 has_reconciler: bool = False, prior_failures: int = 0):
        return self.governance.plan(
            observation, idempotent=idempotent, irreversible=irreversible,
            requires_approval=requires_approval, has_reconciler=has_reconciler,
            prior_failures=prior_failures,
        )

    def reconcile(self, *, workspace_id: str, execution_id: str,
                  idempotency_key: str, correlation_id: str | None,
                  reconciler: Reconciler | None) -> ReconciliationObservation:
        if not reconciler:
            return ReconciliationObservation(workspace_id, execution_id, ReconciliationState.INCONCLUSIVE,
                                             evidence=RecoveryBlock.MISSING_RECONCILER.value)
        try:
            result = dict(reconciler.reconcile(idempotency_key=idempotency_key, correlation_id=correlation_id))
        except Exception as exc:
            return ReconciliationObservation(workspace_id, execution_id, ReconciliationState.ERROR,
                                             evidence=str(exc)[:300])
        state = str(result.get("state") or result.get("status") or "inconclusive").lower()
        if state in {"succeeded", "published", "confirmed_success", "exists"}:
            rs = ReconciliationState.CONFIRMED_SUCCESS
            authoritative = bool(result.get("authoritative", True))
        elif state in {"not_found", "absent", "confirmed_absent"}:
            rs = ReconciliationState.CONFIRMED_ABSENT
            authoritative = bool(result.get("authoritative", True))
        else:
            rs = ReconciliationState.INCONCLUSIVE
            authoritative = False
        return ReconciliationObservation(
            workspace_id, execution_id, rs,
            provider_object_id=(str(result.get("provider_object_id") or result.get("post_id")) if (result.get("provider_object_id") or result.get("post_id")) else None),
            provider_request_id=(str(result.get("provider_request_id")) if result.get("provider_request_id") else None),
            evidence=str(result.get("evidence") or result.get("reason") or "")[:500],
            attempts=int(result.get("attempts") or 0), authoritative=authoritative,
            metadata={k: v for k, v in result.items() if k not in {"token", "access_token", "refresh_token", "api_key", "secret"}},
        )

    def decide_after_reconciliation(self, *, observation: ExceptionObservation,
                                    reconciliation: ReconciliationObservation | None,
                                    idempotent: bool, irreversible: bool = False,
                                    requires_approval: bool = False,
                                    prior_failures: int = 0) -> RecoveryDecision:
        if observation.workspace_id != (reconciliation.workspace_id if reconciliation else observation.workspace_id):
            return RecoveryDecision(observation.workspace_id, observation.execution_id,
                                    RecoveryDisposition.BLOCKED, RecoveryAction.NONE, False,
                                    (RecoveryBlock.TENANT_MISMATCH.value,))
        if observation.external_state_uncertain or observation.partial_execution or observation.observed_status in {"uncertain", "reconciliation_required"}:
            if reconciliation is None:
                return RecoveryDecision(observation.workspace_id, observation.execution_id,
                                        RecoveryDisposition.RECONCILE_FIRST, RecoveryAction.RECONCILE_EXTERNAL_STATE,
                                        False, (RecoveryBlock.RECONCILIATION_REQUIRED.value,),
                                        ("freeze duplicate retry", "obtain provider-authoritative state"),
                                        requires_human_approval=True, reconciliation=None)
            if reconciliation.state == ReconciliationState.CONFIRMED_SUCCESS and reconciliation.authoritative:
                return RecoveryDecision(observation.workspace_id, observation.execution_id,
                                        RecoveryDisposition.AUTO_RECOVER, RecoveryAction.NONE, True,
                                        steps=("record authoritative success", "close recovery debt"), reconciliation=reconciliation)
            if reconciliation.state == ReconciliationState.CONFIRMED_ABSENT and reconciliation.authoritative:
                if irreversible or requires_approval or not idempotent:
                    return RecoveryDecision(observation.workspace_id, observation.execution_id,
                                            RecoveryDisposition.REVIEW_REQUIRED, RecoveryAction.RETRY_EXISTING_INTENT,
                                            False, (RecoveryBlock.RETRY_UNSAFE.value,),
                                            ("preserve original intent", "obtain governed approval", "retry only through Action Kernel"),
                                            requires_human_approval=True, reconciliation=reconciliation)
                # The provider has now authoritatively established that the
                # ambiguous object is absent. Reclassify the original transport
                # failure as an ordinary retry candidate; otherwise the original
                # `uncertain` status would recursively force reconciliation forever.
                retry_observation = ExceptionObservation(
                    workspace_id=observation.workspace_id, execution_id=observation.execution_id,
                    action_type=observation.action_type, observed_status="failed",
                    error=observation.error, error_code=observation.error_code,
                    provider=observation.provider, attempt=observation.attempt,
                    intent_id=observation.intent_id, correlation_id=observation.correlation_id,
                    external_state_uncertain=False, partial_execution=False,
                    metadata=dict(observation.metadata),
                )
                plan = self.classify(retry_observation, idempotent=idempotent, irreversible=irreversible,
                                     requires_approval=requires_approval, has_reconciler=True,
                                     prior_failures=prior_failures)
                if plan.disposition == RecoveryDisposition.AUTO_RECOVER:
                    return RecoveryDecision(observation.workspace_id, observation.execution_id,
                                            plan.disposition, plan.action, True, steps=plan.steps,
                                            next_delay_seconds=plan.next_delay_seconds,
                                            requires_human_approval=plan.requires_human_approval,
                                            reconciliation=reconciliation)
                return RecoveryDecision(observation.workspace_id, observation.execution_id,
                                        plan.disposition, plan.action, False,
                                        (RecoveryBlock.RETRY_BUDGET_EXHAUSTED.value,), plan.steps,
                                        plan.next_delay_seconds, plan.requires_human_approval, reconciliation)
            return RecoveryDecision(observation.workspace_id, observation.execution_id,
                                    RecoveryDisposition.RECONCILE_FIRST, RecoveryAction.RECONCILE_EXTERNAL_STATE,
                                    False, (RecoveryBlock.RECONCILIATION_INCONCLUSIVE.value,),
                                    ("freeze duplicate retry", "repeat authoritative reconciliation or escalate"),
                                    requires_human_approval=True, reconciliation=reconciliation)

        plan = self.classify(observation, idempotent=idempotent, irreversible=irreversible,
                             requires_approval=requires_approval, has_reconciler=False,
                             prior_failures=prior_failures)
        allowed = plan.disposition == RecoveryDisposition.AUTO_RECOVER
        return RecoveryDecision(observation.workspace_id, observation.execution_id, plan.disposition,
                                plan.action, allowed, steps=plan.steps,
                                next_delay_seconds=plan.next_delay_seconds,
                                requires_human_approval=plan.requires_human_approval)


__all__ = ["RecoveryBlock", "ReconciliationState", "ReconciliationObservation", "Reconciler",
           "RecoveryDecision", "IntegrationRecoveryCoordinator"]
