"""Governed autonomous recovery for KDO.

Recovery is deliberately conservative: diagnosis may recommend an action, but
only this service may turn it into an execution.  High-blast-radius operations
require explicit approval; low-risk isolated restarts may be auto-authorized.
Every recovery is idempotent and records a durable DecisionGate decision before
execution. Verification is mandatory before reporting recovery success.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from core.decision_gate import DecisionGate, DecisionSource
from core.plan_authority import AuthorityLevel, authority_from_workspace

logger = logging.getLogger("kanyoza.recovery_guardian")


@dataclass(frozen=True)
class RecoveryPolicy:
    risk: str
    auto_levels: frozenset[str]
    cooldown_seconds: int
    max_attempts: int
    requires_verification: bool = True


POLICIES: dict[str, RecoveryPolicy] = {
    "restart_failed_service": RecoveryPolicy("medium", frozenset({"AUTO_LOW", "AUTO_MEDIUM", "AUTO_HIGH", "SPECIES"}), 300, 2),
    "restart_isolated_engine": RecoveryPolicy("medium", frozenset({"AUTO_MEDIUM", "AUTO_HIGH", "SPECIES"}), 300, 2),
    "refresh_health_state": RecoveryPolicy("low", frozenset({"AUTO_LOW", "AUTO_MEDIUM", "AUTO_HIGH", "SPECIES"}), 60, 3),
    "clear_stale_condition": RecoveryPolicy("low", frozenset({"AUTO_LOW", "AUTO_MEDIUM", "AUTO_HIGH", "SPECIES"}), 60, 3),
    "restart_scheduler": RecoveryPolicy("medium", frozenset({"AUTO_MEDIUM", "AUTO_HIGH", "SPECIES"}), 300, 2),
}


class RecoveryBlocked(RuntimeError):
    pass


class GuardianRecovery:
    def __init__(self, *, decision_gate: Optional[DecisionGate] = None, clock: Callable[[], float] = time.monotonic):
        self.gate = decision_gate or DecisionGate()
        self._clock = clock
        self._attempts: dict[str, int] = {}
        self._last_attempt: dict[str, float] = {}

    @staticmethod
    def key(workspace_id: str, action: str, target: str, correlation_id: str) -> str:
        raw = "|".join([workspace_id, action, target, correlation_id])
        return "recovery_" + hashlib.sha256(raw.encode()).hexdigest()

    def classify(self, action: str, *, blast_radius: int = 1, critical: bool = False) -> str:
        policy = POLICIES.get(action)
        if policy is None:
            return "high"
        if critical or blast_radius >= 5:
            return "high"
        if blast_radius >= 3:
            return "medium"
        return policy.risk

    def _allowed(self, workspace_id: str, risk: str) -> tuple[bool, str]:
        authority = authority_from_workspace(workspace_id)
        if risk == "high":
            return False, "high_blast_radius_requires_human_approval"
        allowed = authority.value in {"AUTO_LOW", "AUTO_MEDIUM", "AUTO_HIGH", "SPECIES"}
        return allowed, authority.value

    async def recover(
        self,
        *,
        workspace_id: str,
        action: str,
        target: str,
        reason: str,
        correlation_id: str,
        executor: Callable[[dict[str, Any]], Any],
        verifier: Optional[Callable[[dict[str, Any]], Any]] = None,
        blast_radius: int = 1,
        critical: bool = False,
        approval_decision_id: Optional[str] = None,
    ) -> dict[str, Any]:
        if not workspace_id:
            raise RecoveryBlocked("workspace_id is required")
        if not target:
            raise RecoveryBlocked("recovery target is required")
        policy = POLICIES.get(action)
        if policy is None:
            raise RecoveryBlocked(f"unsupported recovery action: {action}")

        risk = self.classify(action, blast_radius=blast_radius, critical=critical)
        allowed, authority = self._allowed(workspace_id, risk)
        if not allowed and approval_decision_id:
            approved = self.gate.get(approval_decision_id)
            if approved and approved.approved and approved.workspace_id in (workspace_id, "platform") and approved.action_name == action:
                allowed, authority = True, "human_approved"
        if not allowed:
            return {"status": "approval_required", "action": action, "target": target, "risk": risk, "reason": authority}

        k = self.key(workspace_id, action, target, correlation_id)
        now = self._clock()
        attempts = self._attempts.get(k, 0)
        if attempts >= policy.max_attempts:
            return {"status": "quarantined", "reason": "recovery_attempt_limit", "attempts": attempts, "idempotency_key": k}
        previous = self._last_attempt.get(k)
        if previous is not None and now - previous < policy.cooldown_seconds:
            return {"status": "cooldown", "retry_after_seconds": round(policy.cooldown_seconds - (now - previous), 1), "idempotency_key": k}

        decision = self.gate.issue(
            source=DecisionSource.SPECIES_OVERRIDE if authority == "SPECIES" else DecisionSource.DIRECTOR_AUTO,
            workspace_id=workspace_id,
            action_name=action,
            approved=True,
            correlation_id=correlation_id,
            risk_level=risk,
            reason=f"governed recovery: {reason}"[:500],
            metadata={"target": target, "authority": authority, "blast_radius": blast_radius},
        )

        self._attempts[k] = attempts + 1
        self._last_attempt[k] = now
        payload = {
            "workspace_id": workspace_id,
            "action": action,
            "target": target,
            "reason": reason,
            "correlation_id": correlation_id,
            "decision_id": decision.decision_id,
            "idempotency_key": k,
            "risk": risk,
        }
        try:
            result = executor(payload)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            self.gate.record_outcome(decision.decision_id, outcome="failure", source="recovery", metadata={"error": str(exc)[:500]})
            return {"status": "failed", "decision_id": decision.decision_id, "error": str(exc)[:500]}

        if not isinstance(result, dict) or not result.get("ok", True):
            self.gate.record_outcome(decision.decision_id, outcome="failure", source="recovery", metadata={"result": str(result)[:500]})
            return {"status": "failed", "decision_id": decision.decision_id, "result": result}

        if verifier is None:
            self.gate.record_outcome(decision.decision_id, outcome="unknown", source="recovery", metadata={"verification": "missing"})
            return {"status": "verification_required", "decision_id": decision.decision_id, "result": result}

        try:
            verified = verifier({**payload, "result": result})
            if inspect.isawaitable(verified):
                verified = await verified
        except Exception as exc:
            self.gate.record_outcome(decision.decision_id, outcome="unknown", source="recovery", metadata={"verification_error": str(exc)[:500]})
            return {"status": "verification_failed", "decision_id": decision.decision_id, "error": str(exc)[:500]}

        if not bool(verified):
            self.gate.record_outcome(decision.decision_id, outcome="failure", source="recovery", metadata={"verification": False})
            return {"status": "not_recovered", "decision_id": decision.decision_id, "result": result}

        self.gate.record_outcome(decision.decision_id, outcome="success", source="recovery", metadata={"verification": True})
        return {"status": "recovered", "decision_id": decision.decision_id, "result": result}


def build_internal_recovery_executor(container=None) -> GuardianRecovery:
    """Build the recovery coordinator; actual actions are resolved at execution time."""
    guardian = GuardianRecovery()

    async def execute(params: dict[str, Any]) -> dict[str, Any]:
        action = params["action"]
        target = params["target"]
        if action == "refresh_health_state":
            return {"ok": True, "action": action, "target": target}
        if action == "clear_stale_condition":
            try:
                from services.condition_engine import AutonomousConditionEngine
                engine = container.try_resolve(AutonomousConditionEngine) if container else None
                if engine is not None and hasattr(engine, "reset"):
                    result = engine.reset(target)
                    if inspect.isawaitable(result):
                        result = await result
                    return {"ok": True, "result": result}
                return {"ok": False, "error": "condition reset hook unavailable"}
            except Exception as exc:
                return {"ok": False, "error": str(exc)[:500]}
        if action in {"restart_failed_service", "restart_isolated_engine", "restart_scheduler"}:
            try:
                from services import meta_engine as module
                meta = getattr(module, "meta_engine", None)
                if meta is not None:
                    for name in ("request_recovery", "recover", "trigger_recovery"):
                        fn = getattr(meta, name, None)
                        if callable(fn):
                            value = fn(target)
                            if inspect.isawaitable(value):
                                value = await value
                            return {"ok": bool(value.get("ok", True)) if isinstance(value, dict) else True, "result": value}
                return {"ok": False, "error": "no recovery handler registered"}
            except Exception as exc:
                return {"ok": False, "error": str(exc)[:500]}
        return {"ok": False, "error": "unsupported recovery action"}

    return guardian
