"""Canonical Energy crew-dispatch bridge via Jira work-order creation.

A Jira issue is a dispatch/work-order request, not proof that a physical crew
was dispatched or arrived. Provider creation remains uncertain until the issue
is authoritatively observed; reconciliation never creates a second request.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from services.action_kernel import ActionContract

ENERGY_CREW_DISPATCH_ACTION_CONTRACT_SPEC = {
    "action_type": "energy_dispatch_crew",
    "risk_level": "high",
    "required_parameters": ("work_order_id", "crew_id"),
    "requires_approval": True,
    "irreversible": False,
    "inline_with_decision": True,
    "autonomous_retryable": False,
}

def register_energy_crew_dispatch_action(kernel: Any, repository: Any, jira_adapter: Any) -> str:
    async def execute(parameters: dict[str, Any], intent: Any) -> dict[str, Any]:
        workspace_id = str(intent.workspace_id)
        work_order_id = str(parameters["work_order_id"]).strip()
        crew_id = str(parameters["crew_id"]).strip()
        if not work_order_id or not crew_id:
            raise ValueError("work_order_id and crew_id are required")
        work_order = repository.get_work_order(workspace_id, work_order_id)
        if work_order is None:
            raise ValueError("energy utility work order not found in workspace")
        binding = repository.get_crew_dispatch_provider_binding(workspace_id, "jira")
        if binding is None:
            raise ValueError("active Jira crew-dispatch provider binding is required")
        meta = dict(getattr(binding, "metadata", {}) or {})
        project_key = str(meta.get("project_key", "")).strip()
        issue_type = str(meta.get("issue_type", "Task")).strip() or "Task"
        if not project_key:
            raise ValueError("Jira crew-dispatch binding requires project_key")
        dispatch = repository.create_crew_dispatch(workspace_id, work_order_id, crew_id)
        receipt = jira_adapter.create_issue(
            project_key=project_key,
            issue_type=issue_type,
            summary=f"Crew dispatch: {work_order.description}",
            description=(f"Dispatch crew: {crew_id}\nWork order: {work_order.id}\n"
                         f"Asset: {work_order.asset_id}\nPriority: {work_order.priority}"),
        )
        if not isinstance(receipt, dict):
            receipt = {"status": "uncertain", "error": "invalid Jira issue receipt"}
        repository.record_crew_dispatch_receipt(
            workspace_id, dispatch.id, receipt.get("request_id"), receipt,
            started_at=datetime.now(timezone.utc),
        )
        return {
            "ok": True,
            "outcome": "uncertain",
            "provider": "jira",
            "provider_object_id": receipt.get("object_id"),
            "provider_issue_key": (receipt.get("metadata") or {}).get("issue_key"),
            "provider_receipt": receipt,
            "provider_receipt_is_business_outcome": False,
            "reconciliation_required": True,
            "dispatch_id": dispatch.id,
        }
    kernel.register(ActionContract(**ENERGY_CREW_DISPATCH_ACTION_CONTRACT_SPEC, handler=execute))
    return ENERGY_CREW_DISPATCH_ACTION_CONTRACT_SPEC["action_type"]
