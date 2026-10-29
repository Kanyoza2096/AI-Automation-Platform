"""Canonical post-deployment reconciliation for IT Operations.

Reconciliation is deliberately separate from deployment execution. It performs
an authoritative provider read, validates workspace/service identity, and only
persists a terminal IT deployment outcome when the provider has reached a
terminal state.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


_TERMINAL = {"succeeded", "failed"}


class ITDeploymentReconciliationService:
    def __init__(self, repository: Any, render_adapter: Any):
        self.repository = repository
        self.render_adapter = render_adapter

    def reconcile(
        self,
        *,
        workspace_id: str,
        service_id: str,
        deployment_id: str,
        provider_reference: str,
    ) -> dict[str, Any]:
        workspace_id = str(workspace_id)
        service_id = str(service_id)
        deployment_id = str(deployment_id)
        provider_reference = str(provider_reference)
        if not provider_reference.strip():
            raise ValueError("provider_reference is required")

        deployment = self.repository.get_deployment(workspace_id, deployment_id)
        if deployment is None:
            raise ValueError("deployment not found in workspace")
        if str(deployment.service_id) != service_id:
            raise ValueError("deployment does not belong to the requested service")

        binding = self.repository.get_service_provider_binding(
            workspace_id, service_id, "render"
        )
        if binding is None:
            raise ValueError("active Render service binding is required")
        provider_service_id = str(binding.provider_service_id)
        if not provider_service_id.strip():
            raise ValueError("Render provider service identity is required")

        existing_attempts = self.repository.list_reconciliation_evidence(
            workspace_id, deployment_id, limit=500
        )
        attempt_number = len(existing_attempts) + 1
        observed = self.render_adapter.reconcile_deploy(
            provider_service_id, provider_reference
        )
        if not isinstance(observed, dict):
            raise ValueError("invalid Render reconciliation response")

        status = str(observed.get("status") or "in_progress").lower()
        if status not in _TERMINAL:
            # Provider state is not terminal. Preserve uncertainty rather than
            # inventing business success/failure.
            self.repository.record_reconciliation_evidence(
                workspace_id, deployment_id, service_id, "render", provider_service_id,
                provider_reference, attempt_number, status, False, observed,
                "provider_observed_nonterminal", {"reconciliation_state": "in_progress"},
            )
            return {
                "workspace_id": workspace_id,
                "service_id": service_id,
                "deployment_id": deployment_id,
                "provider": "render",
                "provider_service_id": provider_service_id,
                "provider_reference": provider_reference,
                "status": "in_progress",
                "terminal": False,
                "reconciliation_required": True,
                "observed": observed,
            }

        self.repository.record_reconciliation_evidence(
            workspace_id, deployment_id, service_id, "render", provider_service_id,
            provider_reference, attempt_number, status, True, observed,
            "verified_provider_terminal", {"reconciliation_state": "terminal"},
        )
        evidence = {
            "provider": "render",
            "provider_service_id": provider_service_id,
            "provider_reference": provider_reference,
            "reconciled_at": datetime.now(timezone.utc).isoformat(),
            "reconciliation": observed,
        }
        outcome = self.repository.record_deployment_outcome(
            workspace_id,
            deployment_id,
            status,
            provider_reference=provider_reference,
            evidence=evidence,
        )
        return {
            "workspace_id": workspace_id,
            "service_id": service_id,
            "deployment_id": deployment_id,
            "provider": "render",
            "provider_service_id": provider_service_id,
            "provider_reference": provider_reference,
            "status": status,
            "terminal": True,
            "reconciliation_required": False,
            "observed": observed,
            "outcome_id": getattr(outcome, "id", None),
        }
