from fastapi import APIRouter, Depends, Request, HTTPException
from typing import Optional
from api.dependencies import *
from api.v1_models import *
from api.v1_helpers import *
from api.v1_stores import *

router = APIRouter()

# ═══════════════════════════════════════════════════════════════════════════
# METRICS
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/metrics")
async def get_metrics(_auth: bool = Depends(require_auth)):
    """Get full metrics snapshot."""
    try:
        from utils.metrics import metrics as metrics_collector
        snapshot = await _async_wrap(metrics_collector.get_snapshot)
        return _ok({"metrics": snapshot, "as_of": datetime.now(timezone.utc).isoformat()})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/metrics")


@router.get("/metrics/public")
async def get_public_metrics():
    """Get public-facing metrics."""
    try:
        from utils.metrics import metrics as _m
        snap = await _async_wrap(_m.get_snapshot)
        counters = snap.get("counters", {})
        return _ok({
            "uptime_seconds": snap.get("uptime_seconds"),
            "total_posts": counters.get("posts_published", 0),
            "total_messages": counters.get("messages_received", 0),
            "total_errors": counters.get("errors", 0),
            "as_of": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/metrics/public")


@router.get("/metrics/resources")
async def get_resource_metrics(_auth: bool = Depends(require_auth)):
    """Get system resource metrics."""
    try:
        cpu = mem = disk = 0.0
        try:
            import psutil as _ps
            cpu = _ps.cpu_percent(interval=None)
            mem = _ps.virtual_memory().percent
            disk = _ps.disk_usage("/").percent
        except Exception:
            pass
        return _ok({
            "cpu_percent": cpu,
            "memory_percent": mem,
            "disk_percent": disk,
        })
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/metrics/resources")


@router.get("/metrics/query")
async def metrics_query(
    _auth: bool = Depends(require_auth),
    q: str = "",
):
    """Prometheus-compatible metrics query."""
    try:
        from utils.metrics import metrics as _m
        snap = await _async_wrap(_m.get_snapshot)
        value = 0
        if "error" in q.lower():
            value = snap.get("counters", {}).get("errors", 0)
        elif "rps" in q.lower():
            value = snap.get("counters", {}).get("requests_total", 0)
        return JSONResponse({
            "status": "success",
            "data": {
                "result": [{
                    "metric": {"__name__": q or "kanyoza_metric"},
                    "value": [int(datetime.now(timezone.utc).timestamp()), str(value)],
                }]
            },
        })
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/metrics/query")

