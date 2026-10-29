"""Canonical IT service-remediation execution bridge via GitHub Actions."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from services.action_kernel import ActionContract

IT_SERVICE_REMEDIATION_ACTION_CONTRACT_SPEC = {
    "action_type": "it_remediate_service",
    "risk_level": "critical",
    "required_parameters": ("service_id", "reason"),
    "requires_approval": True,
    "irreversible": True,
    "inline_with_decision": True,
    "autonomous_retryable": False,
}

def register_it_service_remediation_action(kernel: Any, repository: Any, github_adapter: Any) -> str:
    async def execute(parameters: dict[str, Any], intent: Any) -> dict[str, Any]:
        workspace_id = str(intent.workspace_id)
        service_id = str(parameters["service_id"])
        reason = str(parameters["reason"]).strip()
        if not reason:
            raise ValueError("reason is required")
        binding = repository.get_service_provider_binding(workspace_id, service_id, "github")
        if binding is None:
            raise ValueError("active GitHub service-provider binding is required")
        meta = dict(getattr(binding, "metadata", {}) or {})
        owner, repo, workflow_id, ref = (str(meta.get(k, "")).strip() for k in ("owner", "repo", "workflow_id", "ref"))
        if not all((owner, repo, workflow_id, ref)):
            raise ValueError("GitHub remediation binding requires owner, repo, workflow_id and ref")
        remediation = repository.create_service_remediation(workspace_id, service_id, {
            "owner": owner, "repo": repo, "workflow_id": workflow_id, "ref": ref, "reason": reason,
        })
        started_at = datetime.now(timezone.utc)
        receipt = github_adapter.dispatch_workflow(
            owner, repo, workflow_id=workflow_id, ref=ref,
            inputs={"service_id": service_id, "reason": reason}, confirm=True,
        )
        if not isinstance(receipt, dict):
            receipt = {"status": "uncertain", "error": "invalid GitHub workflow dispatch receipt"}
        repository.record_service_remediation_receipt(
            workspace_id, remediation.id, receipt.get("request_id"),
            {"provider_receipt": receipt, "dispatch_started_at": started_at.isoformat()},
        )
        return {
            "ok": True,
            "outcome": "uncertain",
            "provider": "github",
            "provider_request_id": receipt.get("request_id"),
            "provider_receipt": receipt,
            "reconciliation_required": True,
            "remediation_is_business_outcome": False,
            "remediation_id": remediation.id,
        }
    kernel.register(ActionContract(**IT_SERVICE_REMEDIATION_ACTION_CONTRACT_SPEC, handler=execute))
    return IT_SERVICE_REMEDIATION_ACTION_CONTRACT_SPEC["action_type"]
