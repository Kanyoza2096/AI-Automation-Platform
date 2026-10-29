"""Read-only evidence projection for IT deployment reconciliation."""
from __future__ import annotations
from typing import Any


def build_it_deployment_evidence(repository: Any, *, workspace_id: str, deployment_id: str, limit: int = 100) -> dict[str, Any]:
    deployment = repository.get_deployment(workspace_id, deployment_id)
    if deployment is None:
        raise ValueError("deployment not found in workspace")
    attempts = repository.list_reconciliation_evidence(workspace_id, deployment_id, limit=limit)
    latest = attempts[0] if attempts else None
    return {
        "schema": "KDO.ITDeploymentReconciliationEvidence/v1",
        "workspaceId": str(workspace_id),
        "deploymentId": str(deployment_id),
        "serviceId": str(deployment.service_id),
        "deploymentVersion": deployment.version,
        "evidence": attempts,
        "latest": latest,
        "verification": {
            "state": (latest or {}).get("verification_state", "not_observed"),
            "providerStateIsBusinessOutcome": False,
            "authority": "IT_DEPLOYMENT_RECONCILIATION_SERVICE",
        },
        "readOnly": True,
    }
