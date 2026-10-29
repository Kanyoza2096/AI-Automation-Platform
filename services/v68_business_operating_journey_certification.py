"""V68.10 — end-to-end business operating journey certification.

Certifies representative customer-facing journeys as compositions of the existing
KDO planes. This module is inspection/certification only: it never installs rules,
executes providers, publishes content, or grants authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from services.business_automation_catalog import get_template
from services.v68_business_capability_certification import certify_catalog
from services.v68_business_workflow_operating_certification import certify_workflow_operating_contract


class JourneyState:
    CERTIFIED = "certified"
    REVIEW_REQUIRED = "review_required"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class OperatingJourneyCertification:
    journey_id: str
    state: str
    workflow_ids: tuple[str, ...]
    stages: tuple[str, ...]
    reasons: tuple[str, ...] = ()
    authority: str = "none"
    execution_effect: str = "none"
    approval_effect: str = "none"
    policy_effect: str = "none"

    def to_dict(self) -> dict[str, Any]:
        return {
            "journey_id": self.journey_id,
            "state": self.state,
            "workflow_ids": list(self.workflow_ids),
            "stages": list(self.stages),
            "reasons": list(self.reasons),
            "authority": "none",
            "execution_effect": "none",
            "approval_effect": "none",
            "policy_effect": "none",
        }


JOURNEYS: tuple[dict[str, Any], ...] = (
    {
        "id": "sales.lead-to-followup",
        "workflows": ("sales.new-lead-capture", "sales.new-lead-followup"),
        "required_stages": ("event_ingress", "workflow_admission", "governed_execution", "business_outcome"),
    },
    {
        "id": "sales.customer-signal-to-next-action",
        "workflows": ("sales.customer-purchase-interest", "sales.customer-price-request", "sales.adaptive-next-action"),
        "required_stages": ("customer_signal", "business_understanding", "decision_governance", "governed_execution", "business_outcome"),
    },
    {
        "id": "content.objective-to-publish-outcome",
        "workflows": (),
        "required_stages": ("objective", "content_intelligence", "guardian", "governance", "durable_publish", "external_outcome", "learning"),
    },
)


def certify_operating_journeys() -> tuple[OperatingJourneyCertification, ...]:
    workflow_results = {r.workflow_id: r for r in certify_workflow_operating_contract()}
    capability_results = {r.workflow_id: r for r in certify_catalog()}
    results: list[OperatingJourneyCertification] = []

    for journey in JOURNEYS:
        reasons: list[str] = []
        stages = list(journey["required_stages"])
        workflow_ids = tuple(journey["workflows"])

        for workflow_id in workflow_ids:
            template = get_template(workflow_id)
            if template is None:
                reasons.append(f"workflow_missing:{workflow_id}")
                continue
            operating = workflow_results.get(workflow_id)
            capability = capability_results.get(workflow_id)
            if operating is None:
                reasons.append(f"workflow_certification_missing:{workflow_id}")
            elif operating.state == "blocked":
                reasons.append(f"workflow_blocked:{workflow_id}")
            if capability is None:
                reasons.append(f"capability_certification_missing:{workflow_id}")
            elif capability.state.value == "blocked":
                reasons.append(f"capability_blocked:{workflow_id}")

        # The content publishing journey is backed by existing canonical-pipeline
        # contracts rather than a Business Automation catalog template.
        if journey["id"] == "content.objective-to-publish-outcome":
            try:
                from services.publish_orchestration import run_publish
                if not callable(run_publish):
                    reasons.append("canonical_publish_pipeline_missing")
            except Exception:
                reasons.append("canonical_publish_pipeline_missing")

        if reasons:
            state = JourneyState.BLOCKED
        elif journey["id"] == "sales.customer-signal-to-next-action":
            # Customer communication / adaptive orchestration remain governed and
            # therefore require review at the consequential edge.
            state = JourneyState.REVIEW_REQUIRED
        else:
            state = JourneyState.CERTIFIED

        results.append(OperatingJourneyCertification(
            journey_id=str(journey["id"]),
            state=state,
            workflow_ids=workflow_ids,
            stages=tuple(stages),
            reasons=tuple(dict.fromkeys(reasons)),
        ))
    return tuple(results)


def assert_operating_journey_is_non_executing(result: Mapping[str, Any]) -> None:
    for key in ("authority", "execution_effect", "approval_effect", "policy_effect"):
        if result.get(key, "none") != "none":
            raise ValueError(f"operating journey certification cannot grant {key}")
    forbidden = {
        "provider_execute", "direct_provider_call", "connector_execute",
        "external_api_call", "execute_now", "auto_approve", "install_now",
        "bypass_action_kernel", "bypass_decision_gate", "bypass_council",
    }
    found = sorted(k for k in result if str(k).lower() in forbidden)
    if found:
        raise ValueError(f"parallel execution metadata rejected: {', '.join(found)}")
