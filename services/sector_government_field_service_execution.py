"""Canonical Government field-service dispatch bridge via Jira.

A Jira issue is a dispatch request, not proof that field work occurred.
Provider creation is therefore an uncertain receipt until authoritative issue
verification; reconciliation never creates a second dispatch.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from services.action_kernel import ActionContract

GOVERNMENT_FIELD_SERVICE_ACTION_CONTRACT_SPEC = {
    "action_type": "government_dispatch_field_service",
    "risk_level": "high",
    "required_parameters": ("request_id", "team_id"),
    "requires_approval": True,
    "irreversible": False,
    "inline_with_decision": True,
    "autonomous_retryable": False,
}

def register_government_field_service_action(kernel: Any, repository: Any, jira_adapter: Any) -> str:
    async def execute(parameters: dict[str, Any], intent: Any) -> dict[str, Any]:
        workspace_id = str(intent.workspace_id)
        request_id = str(parameters["request_id"]).strip()
        team_id = str(parameters["team_id"]).strip()
        if not request_id or not team_id:
            raise ValueError("request_id and team_id are required")
        request = repository.get_request(workspace_id, request_id)
        if request is None:
            raise ValueError("government service request not found in workspace")
        binding = repository.get_field_service_provider_binding(workspace_id, "jira")
        if binding is None:
            raise ValueError("active Jira field-service provider binding is required")
        meta = dict(getattr(binding, "metadata", {}) or {})
        project_key = str(meta.get("project_key", "")).strip()
        issue_type = str(meta.get("issue_type", "Task")).strip() or "Task"
        if not project_key:
            raise ValueError("Jira field-service binding requires project_key")
        dispatch = repository.create_field_service_dispatch(workspace_id, request_id, team_id, request.title)
        receipt = jira_adapter.create_issue(
            project_key=project_key,
            issue_type=issue_type,
            summary=f"Field service: {request.title}",
            description=f"Dispatch team: {team_id}\nRequest: {request.id}\nPriority: {request.priority}",
        )
        if not isinstance(receipt, dict):
            receipt = {"status": "uncertain", "error": "invalid Jira issue receipt"}
        repository.record_field_service_receipt(
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
    kernel.register(ActionContract(**GOVERNMENT_FIELD_SERVICE_ACTION_CONTRACT_SPEC, handler=execute))
    return GOVERNMENT_FIELD_SERVICE_ACTION_CONTRACT_SPEC["action_type"]
