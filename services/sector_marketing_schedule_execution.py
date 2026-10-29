"""Canonical internal Marketing & Media content-schedule action.

Scheduling an already-governed content candidate is a durable KDO domain write;
it is not a provider publication. External publication remains a separate
provider-backed action.
"""
from __future__ import annotations
from typing import Any
from datetime import datetime
from services.action_kernel import ActionContract, ActionIntent

MARKETING_SCHEDULE_CONTENT_ACTION_CONTRACT_SPEC = {
    "action_type": "marketing_schedule_content",
    "risk_level": "medium",
    "required_parameters": ("campaign_id", "candidate_id", "platform", "scheduled_at", "decision_id"),
    "requires_approval": True,
    "irreversible": False,
    "inline_with_decision": True,
    "autonomous_retryable": False,
}


def register_marketing_schedule_content_action(kernel, marketing_repository) -> None:
    async def run(parameters: dict[str, Any], intent: ActionIntent) -> dict[str, Any]:
        payload = dict(parameters)
        value = payload.get("scheduled_at")
        if isinstance(value, str):
            payload["scheduled_at"] = datetime.fromisoformat(value.replace("Z", "+00:00"))
        schedule = marketing_repository.schedule_content(intent.workspace_id, payload)
        return {
            "ok": True,
            "status": "succeeded",
            "internal_receipt": {
                "kind": "KDOInternalDomainReceipt",
                "action_type": intent.action_type,
                "domain_operation": "schedule_content",
                "object_id": getattr(schedule, "id", None),
            },
            "object_id": getattr(schedule, "id", None),
            "data": schedule.model_dump(mode="json"),
        }

    kernel.register(ActionContract(**MARKETING_SCHEDULE_CONTENT_ACTION_CONTRACT_SPEC, handler=run))
