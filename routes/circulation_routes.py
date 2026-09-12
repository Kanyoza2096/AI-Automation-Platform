"""Circulation (DFMO) HTTP surface — Control Plane wire-up."""
from __future__ import annotations
import logging
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, Request, HTTPException

logger = logging.getLogger("kanyoza.routes.circulation")
from core.security.deps import require_species_privileges

router = APIRouter(prefix="/api/v1/circulation", tags=["circulation"], dependencies=[Depends(require_species_privileges)])


def _monitor():
    try:
        from core.circulation_monitor import get_circulation_monitor
        return get_circulation_monitor()
    except Exception:
        pass
    try:
        from core import circulation_monitor as mod
        return getattr(mod, "_monitor", None) or getattr(mod, "circulation_monitor", None)
    except Exception:
        return None


def _snapshot_dict(snap) -> Dict[str, Any]:
    if snap is None:
        return {}
    if isinstance(snap, dict):
        return snap
    blockages = []
    for b in getattr(snap, "active_blockages", []) or []:
        if hasattr(b, "__dict__"):
            d = dict(b.__dict__)
            for k, v in list(d.items()):
                if hasattr(v, "value"):
                    d[k] = v.value
                elif hasattr(v, "isoformat"):
                    d[k] = v.isoformat()
            blockages.append(d)
        else:
            blockages.append(b)
    return {
        "timestamp": getattr(snap, "timestamp", None).isoformat()
        if hasattr(getattr(snap, "timestamp", None), "isoformat")
        else str(getattr(snap, "timestamp", "")),
        "event_bus_flowing": getattr(snap, "event_bus_flowing", True),
        "database_writing": getattr(snap, "database_writing", True),
        "services_healthy": getattr(snap, "services_healthy", 0),
        "services_total": getattr(snap, "services_total", 0),
        "flow_score": getattr(snap, "flow_score", 0),
        "active_blockages": blockages,
    }


@router.get("/status")
async def circulation_status(request: Request):
    mon = _monitor()
    if mon is None:
        return {"ok": True, "status": "unavailable", "message": "Circulation monitor not started"}
    if hasattr(mon, "get_status"):
        return {"ok": True, **mon.get_status()}
    return {"ok": True, "status": "unknown"}


@router.get("/snapshot")
async def circulation_snapshot(request: Request):
    mon = _monitor()
    if mon is None:
        return {"ok": True, "snapshot": None, "status": "unavailable"}
    if hasattr(mon, "check_circulation"):
        snap = await mon.check_circulation()
        return {"ok": True, "snapshot": _snapshot_dict(snap)}
    if hasattr(mon, "get_status"):
        return {"ok": True, "snapshot": mon.get_status()}
    return {"ok": True, "snapshot": {}}


@router.get("/blockages")
async def circulation_blockages(request: Request):
    mon = _monitor()
    if mon is None:
        return {"ok": True, "blockages": []}
    status = mon.get_status() if hasattr(mon, "get_status") else {}
    blockages = status.get("active_blockages") or status.get("blockages") or []
    return {"ok": True, "blockages": blockages}


@router.get("/history")
async def circulation_history(limit: int = 20):
    mon = _monitor()
    if mon is None:
        return {"ok": True, "history": []}
    hist = getattr(mon, "_history", None) or getattr(mon, "history", None) or []
    if callable(hist):
        hist = hist()
    items = list(hist)[-limit:]
    return {"ok": True, "history": items, "limit": limit}


@router.post("/check")
async def circulation_check(request: Request):
    mon = _monitor()
    if mon is None:
        raise HTTPException(503, detail="Circulation monitor not available")
    if hasattr(mon, "check_circulation"):
        snap = await mon.check_circulation()
        return {"ok": True, "snapshot": _snapshot_dict(snap), "source": "circulation_monitor"}
    # Fallbacks: status-only check still useful to operators
    if hasattr(mon, "get_status"):
        status = mon.get_status() or {}
        return {
            "ok": True,
            "snapshot": status,
            "source": "circulation_monitor_status",
            "message": "check_circulation unavailable; returned get_status()",
        }
    raise HTTPException(501, detail="Circulation monitor exposes neither check_circulation nor get_status")


@router.post("/heal/{blockage_id}")
async def circulation_heal(blockage_id: str, request: Request):
    mon = _monitor()
    if mon is None:
        raise HTTPException(503, detail="Circulation monitor not available")
    status = mon.get_status() if hasattr(mon, "get_status") else {}
    blockages = status.get("active_blockages") or status.get("blockages") or []
    target = None
    for b in blockages:
        bid = b.get("id") if isinstance(b, dict) else getattr(b, "id", None)
        if str(bid) == str(blockage_id):
            target = b
            break
    if target is None:
        try:
            from core.circulation_monitor import BlockageReport
            target = BlockageReport(id=blockage_id, location="unknown")
        except Exception:
            target = {"id": blockage_id, "location": "unknown"}
    if hasattr(mon, "auto_heal"):
        healed = mon.auto_heal(target)
        if hasattr(healed, "__await__"):
            healed = await healed
        return {
            "ok": bool(healed),
            "blockage_id": blockage_id,
            "healed": bool(healed),
            "source": "circulation_monitor",
        }
    raise HTTPException(
        501,
        detail=f"auto_heal not available on circulation monitor for blockage {blockage_id}",
    )


def register_circulation_routes(app) -> None:
    app.include_router(router)
