"""vNext Platform Control Plane read model.

This is a backend-owned, read-only projection for the Command Center. It composes
already-authoritative runtime, database, connector and sector-pack surfaces. It
never grants authority, creates intents, executes providers, or invents missing
telemetry.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping
import hashlib
import json


SECTIONS = (
    "situation",
    "intelligence",
    "governance",
    "execution",
    "connectorFabric",
    "sectorPacks",
    "workforce",
    "observability",
    "securityTenancy",
    "certification",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _truth(section: str, source: str, data: Mapping[str, Any] | None, *, state: str = "LIVE",
           confidence: str = "HIGH", evidence: list[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    if data is None:
        return {
            "section": section, "state": "UNKNOWN", "dataClass": "UNAVAILABLE",
            "authoritySource": source, "freshness": "unknown", "confidence": "NONE",
            "evidence": list(evidence or []), "data": {}, "actionsAllowed": [],
            "actionAuthorityGranted": False,
        }
    return {
        "section": section, "state": state, "dataClass": "OBSERVED",
        "authoritySource": source, "freshness": _now(), "confidence": confidence,
        "evidence": list(evidence or [{"source": source, "fingerprint": _fingerprint(data)}]),
        "data": dict(data), "actionsAllowed": [], "actionAuthorityGranted": False,
    }


def _query(db: Any, table: str, workspace_id: str, *, limit: int = 25, order: str = "updated_at") -> tuple[list[Mapping[str, Any]], str | None]:
    if db is None:
        return [], "durable_store_unavailable"
    try:
        q = db.table(table).select("*").eq("workspace_id", workspace_id)
        try:
            q = q.order(order, desc=True)
        except Exception:
            pass
        result = q.limit(limit).execute()
        return list(getattr(result, "data", None) or []), None
    except Exception as exc:
        return [], f"{table}:{type(exc).__name__}"


def build_platform_control_plane_read_model(workspace_id: str, *, db: Any = None, runtime: Any = None) -> dict[str, Any]:
    """Build the canonical platform control-plane projection for one workspace."""
    workspace_id = str(workspace_id or "").strip()
    if not workspace_id:
        raise ValueError("workspace_id is required")

    sections: dict[str, dict[str, Any]] = {}

    # Situation: runtime status is authoritative runtime state; no synthetic health.
    runtime_status = None
    if runtime is not None:
        try:
            runtime_status = runtime.status()
        except Exception:
            runtime_status = None
    sections["situation"] = _truth(
        "situation", "production_runtime", {"runtime": runtime_status} if runtime_status is not None else None
    )

    # Intelligence: use existing durable decisions/recommendations when available.
    decisions, decisions_error = _query(db, "decisions", workspace_id, limit=50, order="created_at")
    recommendations, rec_error = _query(db, "recommendation_ledger", workspace_id, limit=50, order="created_at")
    intelligence_data = {
        "decisionCount": len(decisions),
        "recommendationCount": len(recommendations),
        "decisions": decisions[:10],
        "recommendations": recommendations[:10],
        "sources": {"decisions": "durable_store" if not decisions_error else decisions_error,
                    "recommendations": "durable_store" if not rec_error else rec_error},
    }
    intelligence_state = "LIVE" if not decisions_error or not rec_error else "UNKNOWN"
    sections["intelligence"] = _truth("intelligence", "decisions_and_recommendation_ledger", intelligence_data,
                                      state=intelligence_state,
                                      evidence=[{"table": "decisions", "count": len(decisions)},
                                                {"table": "recommendation_ledger", "count": len(recommendations)}])

    # Governance: durable decisions and action outbox are evidence, not authority.
    action_outbox, outbox_error = _query(db, "action_outbox", workspace_id, limit=50, order="created_at")
    sections["governance"] = _truth(
        "governance", "governance_and_action_outbox",
        {"decisionCount": len(decisions), "pendingActionCount": len(action_outbox),
         "recentDecisions": decisions[:10], "recentActions": action_outbox[:10]},
        state="LIVE" if not outbox_error else "DEGRADED",
        evidence=[{"table": "decisions", "count": len(decisions)}, {"table": "action_outbox", "count": len(action_outbox)}],
    )

    # Execution: external execution ledger is authoritative. Missing DB stays unknown.
    executions, execution_error = _query(db, "external_execution_ledger", workspace_id, limit=100, order="created_at")
    status_counts: dict[str, int] = {}
    for row in executions:
        status = str(row.get("status") or "UNKNOWN").upper()
        status_counts[status] = status_counts.get(status, 0) + 1
    sections["execution"] = _truth(
        "execution", "external_execution_ledger",
        {"count": len(executions), "statusCounts": status_counts, "recent": executions[:20]},
        state="LIVE" if not execution_error else "UNKNOWN",
        evidence=[{"table": "external_execution_ledger", "count": len(executions)}],
    )

    # Connector Fabric: supported inventory + live health helper. Certification/activation
    # registry remains explicitly unavailable here unless a dedicated operator read contract exists.
    connectors = None
    connector_error = None
    try:
        from services.connectors.connector_factory import supported_platforms
        connectors = list(supported_platforms())
    except Exception as exc:
        connector_error = type(exc).__name__
    integration_status = None
    try:
        from core.socket_server import _build_integration_status
        integration_status = _build_integration_status()
    except Exception:
        pass
    connector_data = {
        "supportedConnectors": connectors or [],
        "health": integration_status,
        "activationRegistry": {"state": "NOT_EXPOSED", "authority": "eligibility_input_only"},
        "certificationRegistry": {"state": "NOT_EXPOSED", "authority": "evidence_only"},
    }
    sections["connectorFabric"] = _truth(
        "connectorFabric", "connector_factory_and_connector_health",
        connector_data,
        state="LIVE" if connectors is not None else "UNKNOWN",
        evidence=[{"source": "supported_platforms", "count": len(connectors or [])},
                  {"source": "connector_health", "available": integration_status is not None},
                  {"source": "activation_registry", "state": "NOT_EXPOSED"}],
    )
    if connector_error:
        sections["connectorFabric"]["data"]["inventoryError"] = connector_error

    # Sector packs: use the canonical portfolio module rather than duplicating metadata.
    try:
        from routes.sector_pack_routes import _load, _PORTFOLIO
        packs = [_load(pack_id) for pack_id, _module, _class in _PORTFOLIO]
        sections["sectorPacks"] = _truth(
            "sectorPacks", "sector_pack_manifest", {"count": len(packs), "packs": packs, "version": "1.1"},
            evidence=[{"source": "sector_pack_manifest", "count": len(packs)}],
        )
    except Exception as exc:
        sections["sectorPacks"] = _truth("sectorPacks", "sector_pack_manifest", None,
                                          evidence=[{"reason": type(exc).__name__}])

    # Workforce: project durable workforce identities/runs where available.
    identities, identity_error = _query(db, "agent_identities", workspace_id, limit=50, order="created_at")
    runs, runs_error = _query(db, "agent_autonomous_runs", workspace_id, limit=50, order="created_at")
    sections["workforce"] = _truth(
        "workforce", "durable_workforce_control_plane",
        {"agentCount": len(identities), "runCount": len(runs), "agents": identities[:10], "runs": runs[:10]},
        state="LIVE" if not identity_error and not runs_error else "UNKNOWN",
        evidence=[{"table": "agent_identities", "count": len(identities)}, {"table": "agent_autonomous_runs", "count": len(runs)}],
    )

    # Observability: use runtime status only; deeper metrics remain delegated to existing observability APIs.
    sections["observability"] = _truth(
        "observability", "production_runtime_and_observability_surfaces",
        {"runtimeAvailable": runtime_status is not None,
         "deeperMetrics": "EXPOSED_SEPARATELY",
         "incidentProjection": "EXPOSED_SEPARATELY"},
        state="LIVE" if runtime_status is not None else "UNKNOWN",
    )

    # Security/Tenancy: workspace is backend-derived from RequestContext at the route boundary.
    sections["securityTenancy"] = _truth(
        "securityTenancy", "request_context_and_workspace_scope",
        {"workspaceId": workspace_id, "workspaceScope": "BACKEND_DERIVED",
         "tenantIsolation": "ENFORCED_BY_BACKEND", "rls": "EXPOSED_BY_DURABLE_STORE"},
        evidence=[{"source": "RequestContext", "workspaceId": workspace_id}],
    )

    # Certification: report what can be asserted from installed runtime components without
    # pretending that live provider/device/deployment certification has occurred.
    convergence = None
    if runtime is not None:
        try:
            convergence = runtime.status()
        except Exception:
            convergence = None
    sections["certification"] = _truth(
        "certification", "runtime_convergence_and_backend_certification",
        {"runtimeConvergence": convergence,
         "liveProviderCertification": "EXTERNAL_GATE",
         "productionDeploymentCertification": "EXTERNAL_GATE",
         "deviceCertification": "COMMAND_CENTER_EXTERNAL_GATE"},
        state="LIVE" if convergence is not None else "UNKNOWN",
    )

    live = sum(1 for section in sections.values() if section["state"] == "LIVE")
    degraded = sum(1 for section in sections.values() if section["state"] == "DEGRADED")
    unknown = len(sections) - live - degraded
    overall = "LIVE" if live == len(sections) else ("DEGRADED" if live or degraded else "UNKNOWN")

    return {
        "schema": "KDO.PlatformControlPlaneReadModel/v1",
        "version": "vNext.2",
        "generatedAt": _now(),
        "workspaceId": workspace_id,
        "state": overall,
        "summary": {"sectionCount": len(sections), "live": live, "degraded": degraded, "unknown": unknown},
        "sections": sections,
        "authority": {
            "readOnly": True,
            "actionAuthorityGranted": False,
            "commandCenterRole": "OPERATOR_AND_EVIDENCE_UI",
            "backendRole": "AUTHORITATIVE_CONTROL_PLANE",
            "consequentialCommandsMustUse": [
                "GOVERNANCE", "ACTION_KERNEL", "DURABLE_EXECUTION_LEDGER", "CONNECTOR_FABRIC", "VERIFICATION", "RECONCILIATION",
            ],
        },
    }
