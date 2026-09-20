"""V68.7 — business capability operating certification.

Certifies that the customer-facing workflow catalog is actually backed by the
existing Business Automation and Action Kernel planes. This is an inspection
and certification surface only: it never executes a provider, grants authority,
or installs automation.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from services.business_automation_catalog import list_templates
from services.business_automation import BusinessAutomationEngine
from services.action_kernel import ActionKernel, build_default_action_kernel
from services.sales_orchestration import SalesOrchestrator


class CertificationState(str, Enum):
    CERTIFIED = "certified"
    REVIEW_REQUIRED = "review_required"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class CapabilityCertification:
    workflow_id: str
    action: str
    state: CertificationState
    checks: tuple[str, ...]
    reasons: tuple[str, ...] = ()
    risk: str = "unknown"
    requires_approval: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "action": self.action,
            "state": self.state.value,
            "checks": list(self.checks),
            "reasons": list(self.reasons),
            "risk": self.risk,
            "requires_approval": self.requires_approval,
            "authority": "none",
            "execution_effect": "none",
        }


def _contract_map(kernel: ActionKernel) -> Mapping[str, Any]:
    return getattr(kernel, "_contracts", {})


def certify_catalog(
    *,
    automation_engine: BusinessAutomationEngine | None = None,
    action_kernel: ActionKernel | None = None,
) -> tuple[CapabilityCertification, ...]:
    """Certify catalog-to-runtime wiring without invoking any handler."""
    automation_engine = automation_engine or BusinessAutomationEngine()
    action_kernel = action_kernel or build_default_action_kernel(allow_in_memory=True)
    actions = getattr(automation_engine, "_actions", {})
    contracts = _contract_map(action_kernel)
    results: list[CapabilityCertification] = []

    for template in list_templates():
        workflow_id = str(template["id"])
        action = str(template.get("action") or "")
        risk = str(template.get("risk") or "unknown").lower()
        checks: list[str] = []
        reasons: list[str] = []

        if action not in actions:
            reasons.append("business_automation_action_missing")
        else:
            checks.append("business_automation_action_registered")

        # Some catalog actions are deliberately not provider-side Action Kernel
        # contracts: read-only reporting has no side effect, while the bounded
        # sales orchestrator is itself a planner/delegator whose selected action
        # must enter the Action Kernel exactly once.
        read_only = action in getattr(automation_engine, "READ_ONLY_ACTIONS", frozenset())
        orchestrator_delegate = action == "sales_orchestrate" and getattr(SalesOrchestrator, "EXECUTION_PLANE", "") == "action_kernel_delegate"

        contract = contracts.get(action)
        if read_only:
            checks.append("read_only_capability_classified")
        elif orchestrator_delegate:
            checks.append("orchestrator_delegates_to_action_kernel")
        elif contract is None:
            reasons.append("action_kernel_contract_missing")
        else:
            checks.append("action_kernel_contract_registered")
            contract_risk = str(getattr(contract, "risk_level", "unknown")).lower()
            if risk != "unknown" and contract_risk != risk:
                reasons.append(f"catalog_kernel_risk_mismatch:{risk}:{contract_risk}")
            else:
                checks.append("catalog_kernel_risk_aligned")
            declared_approval = bool(getattr(contract, "requires_approval", False))
            requires_approval = declared_approval or "approval" in " ".join(template.get("requires", ())).lower()
            if declared_approval:
                checks.append("action_kernel_approval_boundary_present")
            if risk in {"high", "critical"} and not declared_approval and "approval" not in " ".join(template.get("requires", ())).lower():
                reasons.append("high_impact_workflow_missing_approval_boundary")

        requires_approval = bool(contract and getattr(contract, "requires_approval", False)) or "approval" in " ".join(template.get("requires", ())).lower()

        if read_only:
            # Read-only reporting is certified without an Action Kernel contract.
            pass
        elif orchestrator_delegate:
            # The orchestrator's selected action is independently certified by
            # its own catalog entry and enters the Kernel exactly once.
            pass

        if reasons:
            state = CertificationState.BLOCKED
        elif requires_approval or risk in {"high", "critical"}:
            state = CertificationState.REVIEW_REQUIRED
        else:
            state = CertificationState.CERTIFIED

        results.append(CapabilityCertification(
            workflow_id=workflow_id,
            action=action,
            state=state,
            checks=tuple(checks),
            reasons=tuple(reasons),
            risk=risk,
            requires_approval=requires_approval,
        ))
    return tuple(results)


def assert_certification_is_non_executing(result: Mapping[str, Any]) -> None:
    """Reject attempts to turn certification metadata into an execution path."""
    for key in ("authority", "execution_effect", "approval_effect", "policy_effect"):
        if result.get(key, "none") != "none":
            raise ValueError(f"capability certification cannot grant {key}")
    forbidden = {"provider_execute", "direct_provider_call", "connector_execute", "external_api_call", "execute_now", "auto_approve", "bypass_action_kernel", "bypass_council"}
    found = sorted(k for k in result if str(k).lower() in forbidden)
    if found:
        raise ValueError(f"parallel execution metadata rejected: {', '.join(found)}")
