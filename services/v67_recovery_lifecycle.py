"""V67.15 — truthful lifecycle for autonomous recovery plans.

Recovery planning is control-plane state, while Action Outbox/Action Kernel is
execution truth.  A recovery plan must not remain permanently "planned" after
its released intent has actually succeeded, and a failed later attempt must
not rewrite the historical exception into a false success.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RecoveryLifecycleDecision:
    allowed: bool
    state: str
    reason: str
    authority: str = "none"


def certify_recovery_success(
    *,
    plan_id: str,
    intent_id: str,
    workspace_id: str,
    outbox_status: str,
    recovery_plan_id: str | None,
    execution_id: str | None = None,
) -> RecoveryLifecycleDecision:
    """Pure fail-closed guard for closing a recovery plan as recovered.

    Only an authoritative terminal success on the same released intent can
    close the plan.  No AI output, approval flag, or timing inference can do so.
    """
    if not plan_id.strip() or not intent_id.strip() or not workspace_id.strip():
        return RecoveryLifecycleDecision(False, "open", "missing recovery identity")
    if outbox_status.strip().lower() != "succeeded":
        return RecoveryLifecycleDecision(False, "open", "only authoritative outbox success may close recovery")
    if recovery_plan_id != plan_id:
        return RecoveryLifecycleDecision(False, "open", "recovery plan lineage mismatch")
    if execution_id is not None and not str(execution_id).strip():
        return RecoveryLifecycleDecision(False, "open", "invalid execution lineage")
    return RecoveryLifecycleDecision(True, "recovered", "recovery closed from authoritative successful execution")


def recovery_success_rpc_payload(*, plan_id: str, intent_id: str, workspace_id: str, execution_id: str | None = None) -> dict[str, Any]:
    """Build the narrow DB RPC payload; never executes the RPC itself."""
    payload: dict[str, Any] = {
        "p_plan_id": plan_id,
        "p_intent_id": intent_id,
        "p_workspace_id": workspace_id,
    }
    if execution_id:
        payload["p_execution_id"] = execution_id
    return payload
