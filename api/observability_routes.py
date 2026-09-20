"""C9.2 operational observability surface.

One read model joins existing SLO, request metrics, decisions and outbox
state. It is deliberately authority-free: it reports evidence and never
changes execution state.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Query

router = APIRouter(prefix="/api/v1", tags=["observability"])

def _auth():
    from api.v1 import require_auth
    return require_auth

@router.get("/observability/workspace/{workspace_id}")
async def workspace_observability(
    workspace_id: str,
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    _a=Depends(_auth()),
):
    identity = getattr(request.state, "auth_identity", None)
    try:
        from core.tenant_guard import assert_workspace_access
        workspace_id = assert_workspace_access(identity, workspace_id)
    except Exception as exc:
        raise HTTPException(403, detail=str(exc))

    from core.slo_metrics import compute_workspace_slos
    from core.decision_gate import decision_gate
    from core.publish_outbox import publish_outbox
    from utils.metrics import metrics

    slo = compute_workspace_slos(workspace_id)
    decisions = [d.to_dict() for d in decision_gate.list_for_workspace(workspace_id, limit=limit)]
    outbox = [i.to_dict() for i in publish_outbox.list_for_workspace(workspace_id, limit=limit)]
    ctx = getattr(request.state, "correlation_id", "")

    return {
        "schema_version": "c9.2.1",
        "authority": "none",
        "workspace_id": workspace_id,
        "status": slo["status"],
        "correlation_id": ctx,
        "slo": slo,
        "metrics": metrics.get_snapshot(),
        "recent_decisions": decisions,
        "recent_outbox": outbox,
        "traceability": {
            "decision_to_action": "decision_id",
            "action_to_external_attempt": "execution_id",
            "request_to_chain": "correlation_id",
            "ambiguous_external_state": "reconciliation_required",
        },
    }

@router.get("/observability/health")
async def observability_health(
    request: Request,
    _a=Depends(_auth()),
):
    identity = getattr(request.state, "auth_identity", None)
    if not identity:
        raise HTTPException(401, detail="Authentication required")
    from core.slo_metrics import compute_workspace_slos
    from utils.metrics import metrics
    ws = getattr(identity, "workspace_id", None)
    if not ws and not getattr(identity, "is_species", False):
        raise HTTPException(400, detail="workspace_id required")
    data = compute_workspace_slos(ws or "platform")
    return {
        "schema_version": "c9.2.1",
        "authority": "none",
        "status": data["status"],
        "workspace_id": ws or "platform",
        "breaches": data["breaches"],
        "api": data.get("api", {}),
        "outbox": data.get("outbox", {}),
        "bus": data.get("bus", {}),
        "metrics_available": bool(metrics.get_snapshot().get("timings")),
    }
