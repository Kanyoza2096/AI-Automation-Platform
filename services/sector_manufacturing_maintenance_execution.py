"""Canonical manufacturing maintenance-request bridge via Jira."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from services.action_kernel import ActionContract

MANUFACTURING_MAINTENANCE_ACTION_CONTRACT_SPEC = {
    "action_type": "manufacturing_request_maintenance",
    "risk_level": "high",
    "required_parameters": ("maintenance_id", "reason"),
    "requires_approval": True,
    "irreversible": False,
    "inline_with_decision": True,
    "autonomous_retryable": False,
}

def register_manufacturing_maintenance_action(kernel: Any, repository: Any, jira_adapter: Any) -> str:
    async def execute(parameters: dict[str, Any], intent: Any) -> dict[str, Any]:
        workspace_id = str(intent.workspace_id)
        maintenance_id = str(parameters["maintenance_id"])
        reason = str(parameters["reason"]).strip()
        if not reason:
            raise ValueError("reason is required")
        maintenance = repository.get_maintenance(workspace_id, maintenance_id)
        if maintenance is None:
            raise ValueError("maintenance work order not found in workspace")
        binding = repository.get_maintenance_provider_binding(workspace_id, "jira")
        if binding is None:
            raise ValueError("active Jira maintenance-provider binding is required")
        meta = dict(getattr(binding, "metadata", {}) or {})
        project_key = str(meta.get("project_key", "")).strip()
        issue_type = str(meta.get("issue_type", "Task")).strip() or "Task"
        if not project_key:
            raise ValueError("Jira maintenance binding requires project_key")
        record = repository.create_maintenance_request(workspace_id, maintenance_id, {
            "project_key": project_key, "issue_type": issue_type, "reason": reason,
        })
        started_at = datetime.now(timezone.utc)
        receipt = jira_adapter.create_issue(
            project_key=project_key,
            issue_type=issue_type,
            summary=f"Maintenance: {maintenance.description}",
            description=reason,
        )
        if not isinstance(receipt, dict):
            receipt = {"status": "uncertain", "error": "invalid Jira issue receipt"}
        repository.record_maintenance_request_receipt(
            workspace_id, record.id, receipt.get("request_id"), receipt,
            started_at=started_at,
        )
        return {
            "ok": True, "outcome": "uncertain", "provider": "jira",
            "provider_object_id": receipt.get("object_id"),
            "provider_issue_key": (receipt.get("metadata") or {}).get("issue_key"),
            "provider_receipt": receipt, "reconciliation_required": True,
            "maintenance_request_id": record.id,
        }
    kernel.register(ActionContract(**MANUFACTURING_MAINTENANCE_ACTION_CONTRACT_SPEC, handler=execute))
    return MANUFACTURING_MAINTENANCE_ACTION_CONTRACT_SPEC["action_type"]
