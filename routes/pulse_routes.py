"""Universal Pulse HTTP surface — Control Plane wire-up."""
from __future__ import annotations
import logging
from fastapi import APIRouter, Depends, HTTPException

logger = logging.getLogger("kanyoza.routes.pulse")
from core.security.deps import require_auth_context

router = APIRouter(prefix="/api/v1/pulse", tags=["pulse"], dependencies=[Depends(require_auth_context)])


def _pulse():
    try:
        from core.universal_pulse import get_universal_pulse
        return get_universal_pulse()
    except Exception:
        pass
    try:
        from core import universal_pulse as mod
        return getattr(mod, "_pulse", None) or getattr(mod, "universal_pulse", None)
    except Exception:
        return None


@router.get("/status")
async def pulse_status():
    p = _pulse()
    if p is None:
        return {"ok": True, "status": "unavailable", "message": "Universal pulse not started"}
    if hasattr(p, "get_status"):
        return {"ok": True, **p.get_status()}
    return {"ok": True, "status": "unknown"}


@router.get("/tables")
async def pulse_tables():
    p = _pulse()
    if p is None:
        return {"ok": True, "tables": []}
    status = p.get_status() if hasattr(p, "get_status") else {}
    tables = status.get("tables") or status.get("table_activity") or []
    return {"ok": True, "tables": tables}


@router.get("/stagnated")
async def pulse_stagnated():
    p = _pulse()
    if p is None:
        return {"ok": True, "stagnated": []}
    status = p.get_status() if hasattr(p, "get_status") else {}
    stagnated = status.get("stagnated") or status.get("stagnated_systems") or []
    return {"ok": True, "stagnated": stagnated}


@router.get("/alerts")
async def pulse_alerts():
    p = _pulse()
    if p is None:
        return {"ok": True, "alerts": []}
    status = p.get_status() if hasattr(p, "get_status") else {}
    alerts = status.get("alerts") or status.get("recent_alerts") or []
    return {"ok": True, "alerts": alerts}


def register_pulse_routes(app) -> None:
    app.include_router(router)
