"""V67.14 — autonomous recovery safety-boundary certification.

This module is a deliberately small, authority-free guard around recovery
release decisions. It proves that an autonomous recovery plan cannot become a
second side-effect path: recovery may only re-enter the existing Action
Outbox/Action Kernel path, and only for the same intent lineage.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.autonomous_exception_recovery import (
    RecoveryAction,
    RecoveryDisposition,
    RecoveryPlan,
    build_recovery_release,
)


@dataclass(frozen=True)
class RecoverySafetyDecision:
    allowed: bool
    reason: str
    authority: str = "none"
    execution_path: str = "action_outbox->action_kernel"


def certify_recovery_release(
    plan: RecoveryPlan,
    *,
    intent_id: str,
    original_intent_id: str,
    current_action_type: str,
    original_action_type: str,
    current_workspace_id: str,
    approval_present: bool = False,
) -> RecoverySafetyDecision:
    """Fail-closed certification for an autonomous recovery release.

    This function never executes anything. It rejects lineage drift, approval
    mismatches, non-auto plans, and any attempt to turn reconciliation/review
    work into an autonomous retry.
    """
    if plan.authority != "none":
        return RecoverySafetyDecision(False, "recovery plan has non-empty authority")
    if plan.disposition is not RecoveryDisposition.AUTO_RECOVER:
        return RecoverySafetyDecision(False, "only AUTO_RECOVER plans may enter autonomous release")
    if plan.action is not RecoveryAction.RETRY_EXISTING_INTENT:
        return RecoverySafetyDecision(False, "recovery action is not an existing-intent retry")
    if plan.requires_human_approval or plan.requires_reconciliation:
        return RecoverySafetyDecision(False, "plan still requires governance or reconciliation")
    if approval_present:
        return RecoverySafetyDecision(False, "autonomous recovery must not manufacture or consume approval")
    if not intent_id.strip() or intent_id != original_intent_id:
        return RecoverySafetyDecision(False, "recovery intent lineage does not match original intent")
    if plan.workspace_id != current_workspace_id:
        return RecoverySafetyDecision(False, "workspace lineage mismatch")
    if plan.action_type != original_action_type or current_action_type != original_action_type:
        return RecoverySafetyDecision(False, "action lineage drift detected")
    try:
        build_recovery_release(plan, intent_id=intent_id)
    except Exception as exc:
        return RecoverySafetyDecision(False, f"release construction rejected: {exc}")
    return RecoverySafetyDecision(True, "bounded retry may re-enter the existing governed execution path")


def assert_no_parallel_execution_path(metadata: dict[str, Any] | None) -> None:
    """Reject recovery metadata that attempts to name a direct provider path."""
    data = metadata or {}
    forbidden = {
        "provider_execute", "direct_provider_call", "connector_execute",
        "external_api_call", "bypass_action_kernel", "bypass_decision_gate",
        "bypass_council",
    }
    if any(k in data for k in forbidden):
        raise ValueError("recovery metadata attempts to create a parallel execution path")
