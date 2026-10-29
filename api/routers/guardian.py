from fastapi import APIRouter, BackgroundTasks, Depends, Request, HTTPException
from typing import Optional
from api.dependencies import *
from api.v1_models import *
from api.v1_helpers import *
from api.v1_stores import *

router = APIRouter()

# ═══════════════════════════════════════════════════════════════════════════
# GUARDIAN
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/guardian/status")
async def guardian_status(container=Depends(get_container)):
    """Get Guardian status."""
    try:
        from services.guardian_service import GuardianService
        svc = await _async_wrap(container.try_resolve, GuardianService)
        if not svc:
            return _ok({"configured": False, "message": "Guardian not configured"})
        status_data = await _async_wrap(svc.get_status)
        return _ok(status_data)
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/guardian/status")


@router.get("/guardian/issues")
async def guardian_issues(
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
    severity: str = "",
):
    """Get Guardian issues, optionally filtered by severity."""
    try:
        from services.guardian_service import GuardianService
        svc = await _async_wrap(container.try_resolve, GuardianService)
        if not svc:
            return _ok({"issues": [], "message": "Guardian not configured"})

        issues = await _async_wrap(svc.get_issues)
        if severity:
            allowed = {s.strip() for s in severity.split(",")}
            issues = [
                i for i in issues
                if isinstance(i, dict) and i.get("severity", "").lower() in allowed
            ]

        order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        issues = sorted(
            issues,
            key=lambda i: order.get(i.get("severity", "").lower(), 9)
            if isinstance(i, dict) else 9,
        )
        return _ok({"issues": issues, "count": len(issues)})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/guardian/issues")


@router.post("/guardian/scan")
async def trigger_scan(
    background_tasks: BackgroundTasks,
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    """Trigger a Guardian scan in the background."""
    try:
        from services.guardian_service import GuardianService
        svc = await _async_wrap(container.try_resolve, GuardianService)
        if not svc or not svc.is_configured:
            return _err("Guardian requires GitHub credentials", 503)

        background_tasks.add_task(_async_wrap, svc.scan_all)
        return _ok({"started": True, "message": "Scan started"}, 202)
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/guardian/scan")

