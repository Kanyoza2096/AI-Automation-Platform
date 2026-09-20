"""Read-only sensor health/status surfaces."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from core.security.deps import require_auth_context

router = APIRouter(prefix="/api/v1/sensors", tags=["sensors"], dependencies=[Depends(require_auth_context)])


@router.get("/status")
async def sensors_status():
    sensors = {}
    try:
        from plugins.trend_intelligence.plugin import trend_plugin
        if trend_plugin is not None:
            sensors["trend_intelligence"] = trend_plugin.get_status()
        else:
            sensors["trend_intelligence"] = {"enabled": False, "running": False, "status": "not_started"}
    except Exception as exc:
        sensors["trend_intelligence"] = {"enabled": False, "running": False, "status": "unavailable", "error": str(exc)[:200]}
    return {"ok": True, "sensors": sensors}
