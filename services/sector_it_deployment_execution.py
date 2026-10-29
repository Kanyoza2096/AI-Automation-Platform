"""Canonical IT deployment execution bridge.

This module binds the sector action ``it_request_deployment`` to the existing
Action Kernel and Render Connector Fabric only when both dependencies are
explicitly supplied by application composition.

The bridge never treats Render deploy acceptance as deployment success:
Render returns an uncertain provider receipt, which is persisted as an
uncertain IT deployment outcome and must be reconciled through
``RenderAdapter.reconcile_deploy`` before a terminal sector outcome is
recorded.
"""
from __future__ import annotations

from typing import Any

from services.action_kernel import ActionContract
from services.sector_it_deployment_reconciliation import ITDeploymentReconciliationService


IT_DEPLOYMENT_ACTION_CONTRACT_SPEC = {
    "action_type": "it_request_deployment",
    "risk_level": "critical",
    "required_parameters": ("service_id", "version", "environment"),
    "requires_approval": True,
    "irreversible": True,
    "inline_with_decision": True,
    "autonomous_retryable": False,
}


def _status_from_provider(receipt: dict[str, Any]) -> str:
    raw = str(receipt.get("outcome") or receipt.get("status") or "uncertain").lower()
    if raw in {"failed", "error", "rejected"}:
        return "failed"
    if raw in {"cancelled", "canceled"}:
        return "cancelled"
    if raw in {"succeeded", "success", "completed"}:
        # A Render deploy response is only a request acceptance receipt. The
        # adapter normally returns uncertain, but fail closed if an older
        # adapter ever reports success here.
        return "uncertain"
    return "uncertain"


def register_it_deployment_action(kernel: Any, repository: Any, render_adapter: Any) -> str:
    """Register ``it_request_deployment`` behind the canonical execution kernel.

    Registration is dependency-injected: no credentials, provider discovery,
    or mutation enablement is performed by this function.
    """

    async def execute_deployment(parameters: dict[str, Any], intent: Any) -> dict[str, Any]:
        workspace_id = intent.workspace_id
        service_id = str(parameters["service_id"])
        version = str(parameters["version"])
        environment = str(parameters["environment"]).lower()

        service = repository.get_service(workspace_id, service_id)
        if service is None:
            raise ValueError("IT service not found")
        if str(service.environment).lower() != environment:
            raise ValueError("deployment environment does not match the IT service environment")

        binding = repository.get_service_provider_binding(workspace_id, service_id, "render")
        if binding is None:
            raise ValueError("active Render service binding is required")
        if str(binding.environment).lower() != environment:
            raise ValueError("Render binding environment does not match deployment environment")

        version_ref = repository.get_deployment_version_reference(
            workspace_id, service_id, version, "render"
        )
        if version_ref is None:
            raise ValueError("active Render version reference is required")

        deployment = repository.request_deployment(
            workspace_id,
            service_id,
            {"version": version, "environment": environment},
        )

        receipt = render_adapter.trigger_deploy(
            binding.provider_service_id,
            commit_id=version_ref.provider_version_ref,
            clear_cache=bool(parameters.get("clear_cache", False)),
            confirm=True,
            production=environment == "production",
            confirm_production=environment == "production",
        )
        if not isinstance(receipt, dict):
            receipt = {"ok": False, "outcome": "uncertain", "error": "invalid Render receipt"}

        provider_reference = receipt.get("provider_object_id") or receipt.get("object_id") or receipt.get("deploy_id")
        outcome_status = _status_from_provider(receipt)
        repository.record_deployment_outcome(
            workspace_id,
            deployment.id,
            outcome_status,
            provider_reference=provider_reference,
            evidence={
                "provider": "render",
                "provider_service_id": binding.provider_service_id,
                "provider_version_ref": version_ref.provider_version_ref,
                "provider_receipt": receipt,
                "reconciliation_required": True,
            },
        )

        # The Action Kernel must retain the provider request as uncertain until
        # RenderAdapter.reconcile_deploy establishes terminal provider state.
        return {
            "ok": True,
            "outcome": "uncertain",
            "provider": "render",
            "provider_object_id": provider_reference,
            "deployment_id": deployment.id,
            "service_id": service_id,
            "version": version,
            "environment": environment,
            "provider_service_id": binding.provider_service_id,
            "provider_version_ref": version_ref.provider_version_ref,
            "provider_receipt": receipt,
            "reconciliation_required": True,
        }

    # Reconciliation is intentionally a separate service. The Action Kernel
    # does not invoke ActionContract.reconciler automatically; callers use the
    # canonical reconciliation service after an uncertain provider receipt.
    def reconcile(parameters: dict[str, Any]) -> dict[str, Any]:
        service = ITDeploymentReconciliationService(repository, render_adapter)
        return service.reconcile(
            workspace_id=parameters["workspace_id"],
            service_id=parameters["service_id"],
            deployment_id=parameters["deployment_id"],
            provider_reference=parameters["provider_reference"],
        )

    kernel.register(
        ActionContract(
            IT_DEPLOYMENT_ACTION_CONTRACT_SPEC["action_type"],
            risk_level=IT_DEPLOYMENT_ACTION_CONTRACT_SPEC["risk_level"],
            required_parameters=IT_DEPLOYMENT_ACTION_CONTRACT_SPEC["required_parameters"],
            requires_approval=IT_DEPLOYMENT_ACTION_CONTRACT_SPEC["requires_approval"],
            irreversible=IT_DEPLOYMENT_ACTION_CONTRACT_SPEC["irreversible"],
            inline_with_decision=IT_DEPLOYMENT_ACTION_CONTRACT_SPEC["inline_with_decision"],
            autonomous_retryable=IT_DEPLOYMENT_ACTION_CONTRACT_SPEC["autonomous_retryable"],
            handler=execute_deployment,
        )
    )
    return "it_request_deployment"
