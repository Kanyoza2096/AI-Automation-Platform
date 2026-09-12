"""Sprint 5 — Organism weekly reports + manual SLO alert probe."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

router = APIRouter(prefix="/api/v1", tags=["reports"])


@router.get("/reports/workspace/{workspace_id}")
async def workspace_report(
    workspace_id: str,
    request: Request,
    window_days: int = Query(7, ge=1, le=90),
    _a=Depends(lambda: __import__("api.v1", fromlist=["require_auth"]).require_auth),
):
    identity = getattr(request.state, "auth_identity", None)
    try:
        from core.tenant_guard import assert_workspace_access
        workspace_id = assert_workspace_access(identity, workspace_id)
    except Exception as exc:
        raise HTTPException(403, detail=str(exc))
    from core.organism_report import build_organism_report
    return build_organism_report(workspace_id, window_days=window_days)


@router.post("/reports/run-weekly")
async def run_weekly(
    request: Request,
    _a=Depends(lambda: __import__("api.v1", fromlist=["require_auth"]).require_auth),
):
    identity = getattr(request.state, "auth_identity", None)
    if not identity or not getattr(identity, "is_species", False):
        raise HTTPException(403, detail="Species operator required")
    from core.organism_report import run_weekly_reports
    reports = await run_weekly_reports()
    return {"count": len(reports), "reports": reports}


@router.post("/slo/alert-check")
async def slo_alert_check(
    request: Request,
    workspace_id: Optional[str] = Query(None),
    _a=Depends(lambda: __import__("api.v1", fromlist=["require_auth"]).require_auth),
):
    """Force one SLO evaluation + optional webhook (species or tenant)."""
    identity = getattr(request.state, "auth_identity", None)
    from core.slo_metrics import compute_workspace_slos, compute_platform_slos
    from core.slo_alerts import slo_alerter

    if workspace_id:
        try:
            from core.tenant_guard import assert_workspace_access
            workspace_id = assert_workspace_access(identity, workspace_id)
        except Exception as exc:
            raise HTTPException(403, detail=str(exc))
        slo = compute_workspace_slos(workspace_id)
    elif identity and getattr(identity, "is_species", False):
        slo = compute_platform_slos()
        slo = {**slo, "workspace_id": "platform", "breaches": slo.get("breaches") or (
            ["platform_critical"] if slo.get("status") == "critical" else []
        )}
    else:
        raise HTTPException(400, detail="workspace_id required")

    alerted = slo_alerter.evaluate_and_alert(slo)
    return {"slo": slo, "alerted_breaches": alerted, "webhook_enabled": slo_alerter.enabled}
