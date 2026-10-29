"""vNext durable Connector Fabric evidence read model.

This is deliberately read-only. It reads the durable certification registry and
health observation evidence for the backend-derived workspace. Evidence and
eligibility are exposed separately from execution authority.
"""
from __future__ import annotations

from typing import Any

from services.vnext_connector_fabric_read_model import NOT_EXPOSED, get_connector_fabric_record


def _service_client():
    try:
        from services.supabase import get_service_client_from_settings
        return get_service_client_from_settings()
    except Exception:
        return None


def _latest(client: Any, table: str, workspace_id: str, connector_key: str, *, order: str):
    if client is None:
        return None
    try:
        response = (
            client.table(table)
            .select("*")
            .eq("workspace_id", workspace_id)
            .eq("connector_key", connector_key)
            .order(order, desc=True)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        return rows[0] if rows else None
    except Exception:
        return None


def build_connector_fabric_evidence(workspace_id: str, connector_key: str) -> dict[str, Any]:
    if not workspace_id.strip():
        raise ValueError("workspace_id is required")
    record = get_connector_fabric_record(workspace_id, connector_key)
    client = _service_client()

    certification = _latest(
        client, "connector_certification_registry", workspace_id, connector_key, order="updated_at"
    )
    health = _latest(
        client, "connector_health_observations", workspace_id, connector_key, order="observed_at"
    )

    certification_view: dict[str, Any] = {"state": NOT_EXPOSED, "source": NOT_EXPOSED}
    if certification is not None:
        certification_view = {
            "state": certification.get("registry_state", NOT_EXPOSED),
            "status": certification.get("certification_status", NOT_EXPOSED),
            "connectorVersion": certification.get("connector_version", NOT_EXPOSED),
            "certificationVersion": certification.get("certification_version", NOT_EXPOSED),
            "liveProviderVerified": certification.get("live_provider_verified", False),
            "certifiedAt": certification.get("certified_at"),
            "expiresAt": certification.get("expires_at"),
            "evidenceRef": certification.get("evidence_ref", ""),
            "generation": certification.get("generation", 1),
            "reason": certification.get("reason", ""),
            "source": "connector_certification_registry",
        }

    health_view: dict[str, Any] = {"state": NOT_EXPOSED, "source": NOT_EXPOSED}
    if health is not None:
        health_view = {
            "state": health.get("health_state", NOT_EXPOSED),
            "observedAt": health.get("observed_at"),
            "connectionId": health.get("connection_id", NOT_EXPOSED),
            "drift": health.get("drift", []),
            "providerStatusCode": health.get("provider_status_code"),
            "latencyMs": health.get("latency_ms"),
            "evidenceRef": health.get("evidence_ref", ""),
            "source": "connector_health_observations",
        }

    return {
        "workspaceId": workspace_id,
        "connector": record,
        "health": health_view,
        "certification": certification_view,
        "authority": "none",
        "readOnly": True,
        "eligibilityInputOnly": True,
        "executionEffect": "none",
    }
