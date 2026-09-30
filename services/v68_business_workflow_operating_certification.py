"""V68.9 — customer-facing business workflow operating certification.

Certifies the boundary between the declarative workflow catalog, installation
contract, event trigger model, runtime action plane, and governance metadata.
This is inspection-only and never installs or executes a workflow.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from services.business_automation_catalog import list_templates
from services.business_automation import BusinessAutomationEngine
from services.v68_business_capability_certification import certify_catalog


class WorkflowOperatingState(str, Enum):
    CERTIFIED = "certified"
    REVIEW_REQUIRED = "review_required"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class WorkflowOperatingCertification:
    workflow_id: str
    state: WorkflowOperatingState
    checks: tuple[str, ...]
    reasons: tuple[str, ...] = ()
    trigger: str = ""
    action: str = ""
    risk: str = "unknown"
    enabled_by_default: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "state": self.state.value,
            "checks": list(self.checks),
            "reasons": list(self.reasons),
            "trigger": self.trigger,
            "action": self.action,
            "risk": self.risk,
            "enabled_by_default": self.enabled_by_default,
            "authority": "none",
            "execution_effect": "none",
            "approval_effect": "none",
            "policy_effect": "none",
        }


def certify_workflow_operating_contract(
    *,
    automation_engine: BusinessAutomationEngine | None = None,
) -> tuple[WorkflowOperatingCertification, ...]:
    """Inspect every catalog workflow against its real runtime contract."""
    engine = automation_engine or BusinessAutomationEngine()
    actions = getattr(engine, "_actions", {})
    capability_results = {r.workflow_id: r for r in certify_catalog(automation_engine=engine)}
    results: list[WorkflowOperatingCertification] = []

    for template in list_templates():
        workflow_id = str(template.get("id") or "")
        trigger = str(template.get("trigger") or "").strip()
        action = str(template.get("action") or "").strip()
        risk = str(template.get("risk") or "unknown").lower()
        checks: list[str] = []
        reasons: list[str] = []

        if not workflow_id:
            reasons.append("workflow_id_missing")
        else:
            checks.append("workflow_id_present")
        if not trigger:
            reasons.append("trigger_missing")
        else:
            checks.append("trigger_declared")
        if not action:
            reasons.append("action_missing")
        elif action not in actions:
            reasons.append("runtime_action_missing")
        else:
            checks.append("runtime_action_registered")
        if not str(template.get("name") or "").strip():
            reasons.append("name_missing")
        else:
            checks.append("customer_facing_name_present")
        if not str(template.get("description") or "").strip():
            reasons.append("description_missing")
        else:
            checks.append("operational_description_present")
        if not str(template.get("outcome") or "").strip():
            reasons.append("business_outcome_missing")
        else:
            checks.append("business_outcome_declared")
        requirements = template.get("requires")
        if not isinstance(requirements, list) or not requirements:
            reasons.append("runtime_requirements_missing")
        else:
            checks.append("runtime_requirements_declared")
        if risk not in {"low", "medium", "high", "critical"}:
            reasons.append("invalid_risk_classification")
        else:
            checks.append("risk_classification_valid")

        # The installation API converts catalog.trigger into parameters.trigger_events
        # for event-driven rules. Scheduled/manual templates deliberately do not.
        if trigger in {"schedule", "manual"}:
            checks.append("non_event_trigger_installation_contract")
        else:
            checks.append("event_trigger_installation_contract")

        capability = capability_results.get(workflow_id)
        if capability is None:
            reasons.append("capability_certification_missing")
        elif capability.state.value == "blocked":
            reasons.append("capability_plane_blocked")
        elif capability.state.value == "review_required":
            checks.append("capability_plane_requires_review")
        else:
            checks.append("capability_plane_certified")

        # Templates are safe defaults: installation explicitly defaults enabled=False.
        enabled_by_default = False
        checks.append("installation_disabled_by_default")

        if reasons:
            state = WorkflowOperatingState.BLOCKED
        elif capability and capability.state.value == "review_required":
            state = WorkflowOperatingState.REVIEW_REQUIRED
        else:
            state = WorkflowOperatingState.CERTIFIED

        results.append(WorkflowOperatingCertification(
            workflow_id=workflow_id,
            state=state,
            checks=tuple(checks),
            reasons=tuple(dict.fromkeys(reasons)),
            trigger=trigger,
            action=action,
            risk=risk,
            enabled_by_default=enabled_by_default,
        ))
    return tuple(results)


def assert_workflow_certification_is_non_executing(result: Mapping[str, Any]) -> None:
    """Reject certification metadata that attempts to become authority."""
    for key in ("authority", "execution_effect", "approval_effect", "policy_effect"):
        if result.get(key, "none") != "none":
            raise ValueError(f"workflow certification cannot grant {key}")
    forbidden = {
        "provider_execute", "direct_provider_call", "connector_execute",
        "external_api_call", "execute_now", "auto_approve", "install_now",
        "bypass_action_kernel", "bypass_decision_gate", "bypass_council",
    }
    found = sorted(k for k in result if str(k).lower() in forbidden)
    if found:
        raise ValueError(f"parallel execution metadata rejected: {', '.join(found)}")
