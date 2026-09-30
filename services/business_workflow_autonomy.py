"""V68.4 governed business-workflow autonomy admission.

This module is the bridge between a declarative business autonomy envelope and
real customer-facing workflow templates. It is an admission gate only: it does
not grant authority and never calls a provider directly. Consequential work is
still delegated to BusinessAutomationEngine and its existing Action Kernel /
outbox boundaries.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Optional

from services.business_autonomy_envelope import (
    AutonomyMode,
    BusinessAutonomyEnvelope,
    evaluate_envelope_for_action,
)


class WorkflowAdmission(str, Enum):
    ADMITTED = "admitted"
    SUGGESTED = "suggested"
    OBSERVED = "observed"
    REVIEW_REQUIRED = "review_required"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class WorkflowAdmissionDecision:
    workflow_id: str
    workspace_id: str
    action: str
    risk: str
    admission: WorkflowAdmission
    reasons: tuple[str, ...] = ()
    envelope_id: Optional[str] = None
    authority: str = "none"
    execution_effect: str = "none"
    approval_effect: str = "none"
    policy_effect: str = "none"

    def __post_init__(self) -> None:
        if any(v != "none" for v in (self.authority, self.execution_effect, self.approval_effect, self.policy_effect)):
            raise ValueError("workflow autonomy admission cannot grant authority or effects")

    @property
    def allowed_to_invoke_engine(self) -> bool:
        return self.admission == WorkflowAdmission.ADMITTED

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "workspace_id": self.workspace_id,
            "action": self.action,
            "risk": self.risk,
            "admission": self.admission.value,
            "reasons": list(self.reasons),
            "envelope_id": self.envelope_id,
            "authority": "none",
            "execution_effect": "none",
            "approval_effect": "none",
            "policy_effect": "none",
        }


def evaluate_workflow_admission(
    envelope: BusinessAutonomyEnvelope,
    workflow: Mapping[str, Any],
    *,
    workspace_id: str,
    now: Optional[datetime] = None,
) -> WorkflowAdmissionDecision:
    """Evaluate a catalog workflow against an envelope without executing it."""
    workflow_id = str(workflow.get("id") or "").strip()
    action = str(workflow.get("action") or "").strip()
    risk = str(workflow.get("risk") or "low").strip().lower()
    reasons: list[str] = []

    if not workflow_id:
        reasons.append("workflow_id_missing")
    if not action:
        reasons.append("workflow_action_missing")
    if workflow_id and envelope.scope.get("workflow_ids"):
        declared = {str(x) for x in envelope.scope.get("workflow_ids", ())}
        if workflow_id not in declared:
            reasons.append("workflow_not_in_scope")

    check = evaluate_envelope_for_action(
        envelope,
        workspace_id=workspace_id,
        action_type=action,
        risk=risk,
        now=now,
    )
    check_reasons = [str(x) for x in check.get("reasons", ())]
    approval_reason = "approval_required_by_envelope"
    hard_reasons = [r for r in check_reasons if r != approval_reason]
    reasons.extend(hard_reasons)

    if reasons:
        return WorkflowAdmissionDecision(
            workflow_id=workflow_id or "unknown",
            workspace_id=workspace_id,
            action=action or "unknown",
            risk=risk,
            admission=WorkflowAdmission.BLOCKED,
            reasons=tuple(dict.fromkeys(reasons)),
            envelope_id=envelope.envelope_id,
        )

    if envelope.mode == AutonomyMode.OBSERVE:
        return WorkflowAdmissionDecision(workflow_id, workspace_id, action, risk, WorkflowAdmission.OBSERVED, envelope_id=envelope.envelope_id)
    if envelope.mode == AutonomyMode.SUGGEST:
        return WorkflowAdmissionDecision(workflow_id, workspace_id, action, risk, WorkflowAdmission.SUGGESTED, envelope_id=envelope.envelope_id)

    # Execute mode means "eligible to enter the existing governed engine", not
    # "approved to perform the side effect". High/critical still require the
    # existing human/Council path and are never silently auto-approved here.
    risk_order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    if risk_order.get(risk, 99) >= 2 or approval_reason in check_reasons:
        return WorkflowAdmissionDecision(
            workflow_id, workspace_id, action, risk,
            WorkflowAdmission.REVIEW_REQUIRED,
            reasons=tuple(dict.fromkeys(("high_impact_workflow_requires_existing_governance",) + tuple(
                r for r in check_reasons if r == approval_reason
            ))),
            envelope_id=envelope.envelope_id,
        )

    return WorkflowAdmissionDecision(
        workflow_id, workspace_id, action, risk, WorkflowAdmission.ADMITTED,
        envelope_id=envelope.envelope_id,
    )


async def run_catalog_workflow(
    envelope: BusinessAutonomyEnvelope,
    workflow: Mapping[str, Any],
    *,
    engine: Any,
    event: Mapping[str, Any],
    workspace_id: str,
    approved: bool = False,
    now: Optional[datetime] = None,
) -> tuple[WorkflowAdmissionDecision, Any]:
    """Admit then invoke the existing BusinessAutomationEngine only.

    The function deliberately has no provider/connector execution surface.
    """
    decision = evaluate_workflow_admission(envelope, workflow, workspace_id=workspace_id, now=now)
    if decision.admission != WorkflowAdmission.ADMITTED:
        return decision, None

    result = await engine.run_event(
        event_name=str(workflow.get("trigger") or ""),
        event=dict(event),
        workspace_id=workspace_id,
        approved=approved,
    )
    return decision, result


def assert_no_parallel_execution_path(metadata: Mapping[str, Any]) -> None:
    """Reject metadata that attempts to turn this admission layer into an executor."""
    forbidden = {
        "provider_execute", "direct_provider_call", "connector_execute",
        "external_api_call", "bypass_action_kernel", "bypass_decision_gate",
        "bypass_council", "shell_command", "raw_url_execution",
    }
    found = sorted(k for k in metadata if str(k).lower() in forbidden)
    if found:
        raise ValueError(f"parallel execution path rejected: {', '.join(found)}")
