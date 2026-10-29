"""
Sprint 4 — SLO health endpoints.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

router = APIRouter(prefix="/api/v1", tags=["slo"])


@router.get("/slo/workspace/{workspace_id}")
async def workspace_slos(
    workspace_id: str,
    request: Request,
    window_hours: float = Query(24.0, ge=1.0, le=168.0),
    _a=Depends(lambda: __import__("api.v1", fromlist=["require_auth"]).require_auth),
):
    identity = getattr(request.state, "auth_identity", None)
    try:
        from core.tenant_guard import assert_workspace_access
        workspace_id = assert_workspace_access(identity, workspace_id)
    except Exception as exc:
        raise HTTPException(403, detail=str(exc))
    from core.slo_metrics import compute_workspace_slos
    return compute_workspace_slos(workspace_id, window_hours=window_hours)


@router.get("/slo/platform")
async def platform_slos(
    request: Request,
    window_hours: float = Query(24.0, ge=1.0, le=168.0),
    _a=Depends(lambda: __import__("api.v1", fromlist=["require_auth"]).require_auth),
):
    identity = getattr(request.state, "auth_identity", None)
    if not identity or not getattr(identity, "is_species", False):
        raise HTTPException(403, detail="Species operator required")
    from core.slo_metrics import compute_platform_slos
    return compute_platform_slos(window_hours=window_hours)


@router.get("/slo/health")
async def slo_health_summary(
    request: Request,
    workspace_id: Optional[str] = Query(None),
    _a=Depends(lambda: __import__("api.v1", fromlist=["require_auth"]).require_auth),
):
    """
    Compact probe for load balancers / uptime robots (auth required).
    Returns 200 always with status field; use status for alerting.
    """
    identity = getattr(request.state, "auth_identity", None)
    from core.slo_metrics import compute_workspace_slos, compute_platform_slos

    if workspace_id:
        try:
            from core.tenant_guard import assert_workspace_access
            workspace_id = assert_workspace_access(identity, workspace_id)
        except Exception as exc:
            raise HTTPException(403, detail=str(exc))
        data = compute_workspace_slos(workspace_id)
        return {
            "status": data["status"],
            "workspace_id": workspace_id,
            "breaches": data["breaches"],
            "publish_success_rate": data["publish"]["success_rate"],
            "outbox_depth": data["outbox"]["depth"],
            "outbox_dead": data["outbox"]["dead"],
            "bus_dead_letters": data["bus"].get("dead_letters"),
        }

    if identity and getattr(identity, "is_species", False):
        plat = compute_platform_slos()
        return {
            "status": plat["status"],
            "scope": "platform",
            "workspace_count": plat["workspace_count"],
            "global_outbox_depth": plat["global_outbox"]["depth"],
            "global_outbox_dead": plat["global_outbox"]["dead"],
            "bus_dead_letters": plat["bus"].get("dead_letters"),
        }

    # Non-species without workspace_id: use their workspace
    ws = getattr(identity, "workspace_id", None) if identity else None
    if not ws:
        raise HTTPException(400, detail="workspace_id required")
    data = compute_workspace_slos(ws)
    return {
        "status": data["status"],
        "workspace_id": ws,
        "breaches": data["breaches"],
        "publish_success_rate": data["publish"]["success_rate"],
        "outbox_depth": data["outbox"]["depth"],
        "outbox_dead": data["outbox"]["dead"],
        "bus_dead_letters": data["bus"].get("dead_letters"),
    }
