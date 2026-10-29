"""V68.6 — business capability readiness and operating-surface admission.

This module bridges the declarative business-workflow catalog to the real runtime
capabilities without creating a second executor. It answers a narrow question:
"Can this workflow be operated in this workspace right now?"

It never performs provider calls, grants authority, approves work, or mutates
workflow state. Runtime execution remains behind Business Automation, Decision
Policy, Action Kernel, outbox, and the existing human approval surfaces.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional

from services.business_automation_catalog import get_template
from services.business_autonomy_envelope import BusinessAutonomyEnvelope
from services.business_workflow_autonomy import WorkflowAdmission, evaluate_workflow_admission


class CapabilityState(str, Enum):
    READY = "ready"
    DEGRADED = "degraded"
    MISSING = "missing"
    UNKNOWN = "unknown"


class Readiness(str, Enum):
    READY = "ready"
    REVIEW_REQUIRED = "review_required"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CapabilityCheck:
    requirement: str
    capability: str
    state: CapabilityState
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement": self.requirement,
            "capability": self.capability,
            "state": self.state.value,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class BusinessCapabilityReadiness:
    workspace_id: str
    workflow_id: str
    action: str
    readiness: Readiness
    admission: str
    capability_checks: tuple[CapabilityCheck, ...]
    reasons: tuple[str, ...] = ()
    human_review_required: bool = False
    authority: str = "none"
    execution_effect: str = "none"
    approval_effect: str = "none"
    policy_effect: str = "none"

    def __post_init__(self) -> None:
        if any(v != "none" for v in (self.authority, self.execution_effect, self.approval_effect, self.policy_effect)):
            raise ValueError("capability readiness cannot grant authority or effects")

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "workflow_id": self.workflow_id,
            "action": self.action,
            "readiness": self.readiness.value,
            "admission": self.admission,
            "capability_checks": [c.to_dict() for c in self.capability_checks],
            "reasons": list(self.reasons),
            "human_review_required": self.human_review_required,
            "authority": "none",
            "execution_effect": "none",
            "approval_effect": "none",
            "policy_effect": "none",
        }


# Catalog requirements are deliberately normalized to stable capability names.
# This is a readiness vocabulary, not a provider registry.
_CAPABILITY_ALIASES = {
    "crm connection": "crm",
    "crm or notification capability": "crm_or_notification",
    "whatsapp business connection": "whatsapp",
    "email or whatsapp connection": "email_or_whatsapp",
    "notification provider": "notification",
    "workspace reporting data": "workspace_reporting",
    "customer.intent.detected event source": "customer_intent_events",
    "customer.message.received event source": "customer_message_events",
    "lead.created event source": "lead_created_events",
    "lead.follow_up event source": "lead_follow_up_events",
    "lead.follow_up_due event source": "lead_follow_up_due_events",
    "lead.qualified event source": "lead_qualified_events",
    "deal.won event source": "deal_won_events",
    "deal.lost event source": "deal_lost_events",
    "crm.contact.created lifecycle event": "crm_contact_created_events",
    "invoice.overdue event source": "invoice_overdue_events",
    "workflow.failed event source": "workflow_failed_events",
    "ai provider optional; deterministic fallback available": "ai_optional",
}


def _capability_for(requirement: str) -> str:
    text = " ".join(str(requirement).strip().lower().split())
    return _CAPABILITY_ALIASES.get(text, text.replace(" ", "_"))


def _check_requirements(
    requirements: list[str],
    capabilities: Mapping[str, Any],
) -> tuple[CapabilityCheck, ...]:
    checks: list[CapabilityCheck] = []
    for requirement in requirements:
        capability = _capability_for(requirement)
        raw = capabilities.get(capability, "missing")
        if isinstance(raw, Mapping):
            raw = raw.get("state", "unknown")
        try:
            state = CapabilityState(str(raw).lower())
        except ValueError:
            state = CapabilityState.UNKNOWN
        reason = {
            CapabilityState.READY: "capability_ready",
            CapabilityState.DEGRADED: "capability_degraded",
            CapabilityState.MISSING: "capability_missing",
            CapabilityState.UNKNOWN: "capability_state_unknown",
        }[state]
        checks.append(CapabilityCheck(requirement, capability, state, reason))
    return tuple(checks)


def evaluate_business_capability_readiness(
    envelope: BusinessAutonomyEnvelope,
    *,
    workspace_id: str,
    workflow_id: str,
    capabilities: Mapping[str, Any],
    now=None,
) -> BusinessCapabilityReadiness:
    """Evaluate a catalog workflow against real declared runtime capabilities."""
    template = get_template(workflow_id)
    if template is None:
        return BusinessCapabilityReadiness(
            workspace_id, workflow_id, "unknown", Readiness.BLOCKED, "blocked", (),
            ("workflow_not_found",), False,
        )

    admission = evaluate_workflow_admission(
        envelope, template, workspace_id=workspace_id, now=now,
    )
    requirements = [r for r in template.get("requires", ()) if "approval" not in str(r).lower()]
    checks = _check_requirements(list(requirements), capabilities)
    reasons: list[str] = list(admission.reasons)

    # Composite requirements are satisfied when at least one alternative is ready.
    composite = {"crm_or_notification", "email_or_whatsapp"}
    for check in checks:
        if check.capability in composite:
            continue
        if check.state == CapabilityState.MISSING:
            reasons.append(f"capability_missing:{check.capability}")
        elif check.state == CapabilityState.UNKNOWN:
            reasons.append(f"capability_unknown:{check.capability}")
        elif check.state == CapabilityState.DEGRADED:
            reasons.append(f"capability_degraded:{check.capability}")

    for key in composite:
        matching = [c for c in checks if c.capability == key]
        if not matching:
            continue
        # A composite capability is represented by a synthetic readiness flag.
        # Callers may explicitly declare it ready; otherwise unknown is conservative.
        state = capabilities.get(key, "unknown")
        try:
            composite_state = CapabilityState(str(state).lower())
        except ValueError:
            composite_state = CapabilityState.UNKNOWN
        if composite_state == CapabilityState.MISSING:
            reasons.append(f"capability_missing:{key}")
        elif composite_state == CapabilityState.UNKNOWN:
            reasons.append(f"capability_unknown:{key}")
        elif composite_state == CapabilityState.DEGRADED:
            reasons.append(f"capability_degraded:{key}")

    if workspace_id != envelope.workspace_id:
        reasons.append("workspace_mismatch")

    if any(r.startswith("capability_missing") or r == "workspace_mismatch" for r in reasons):
        readiness = Readiness.BLOCKED
    elif any(r.startswith("capability_unknown") or r.startswith("capability_degraded") for r in reasons):
        readiness = Readiness.UNKNOWN
    elif admission.admission == WorkflowAdmission.REVIEW_REQUIRED:
        readiness = Readiness.REVIEW_REQUIRED
    elif admission.admission != WorkflowAdmission.ADMITTED:
        readiness = Readiness.BLOCKED
    else:
        readiness = Readiness.READY

    human_review = readiness == Readiness.REVIEW_REQUIRED
    return BusinessCapabilityReadiness(
        workspace_id=workspace_id,
        workflow_id=workflow_id,
        action=str(template.get("action") or "unknown"),
        readiness=readiness,
        admission=admission.admission.value,
        capability_checks=checks,
        reasons=tuple(dict.fromkeys(reasons)),
        human_review_required=human_review,
    )


def assert_readiness_is_advisory(result: Mapping[str, Any]) -> None:
    """Defense-in-depth: readiness must never become an execution primitive."""
    for key in ("authority", "execution_effect", "approval_effect", "policy_effect"):
        if result.get(key, "none") != "none":
            raise ValueError(f"capability readiness cannot grant {key}")
    forbidden = {
        "provider_execute", "direct_provider_call", "connector_execute",
        "external_api_call", "bypass_action_kernel", "bypass_decision_gate",
        "bypass_council", "auto_approve", "execute_now",
    }
    found = sorted(k for k in result if str(k).lower() in forbidden)
    if found:
        raise ValueError(f"parallel execution metadata rejected: {', '.join(found)}")
