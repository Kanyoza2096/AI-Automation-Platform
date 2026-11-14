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
    """Get host resource metrics without converting provider failures into zeroes."""
    observed_at = datetime.now(timezone.utc).isoformat()
    try:
        import psutil as _ps
    except Exception:
        return _ok({
            "available": False,
            "source": "unavailable",
            "reason": "psutil_unavailable",
            "cpu_percent": None,
            "memory_percent": None,
            "disk_percent": None,
            "as_of": observed_at,
        })

    values = {"cpu_percent": None, "memory_percent": None, "disk_percent": None}
    failures = []
    for key, read_value in (
        ("cpu_percent", lambda: _ps.cpu_percent(interval=None)),
        ("memory_percent", lambda: _ps.virtual_memory().percent),
        ("disk_percent", lambda: _ps.disk_usage("/").percent),
    ):
        try:
            value = read_value()
            if isinstance(value, (int, float)) and 0 <= value <= 100:
                values[key] = float(value)
            else:
                failures.append(key)
        except Exception:
            failures.append(key)

    available_count = sum(value is not None for value in values.values())
    return _ok({
        "available": available_count > 0,
        "source": "psutil",
        "reason": "partial" if failures and available_count else ("provider_error" if failures else None),
        **values,
        "as_of": observed_at,
    })


@router.get("/metrics/query")
async def metrics_query(
    _auth: bool = Depends(require_auth),
    q: str = "",
):
    """Read only explicitly supported counters; this is not a general PromQL engine."""
    metric_name = q.strip()
    supported = {
        "kanyoza_errors_total": "errors",
        "kanyoza_requests_total": "requests_total",
    }
    if metric_name not in supported:
        return JSONResponse(
            status_code=422,
            content={
                "status": "error",
                "error": "unsupported_query",
                "message": "This endpoint currently supports only kanyoza_errors_total and kanyoza_requests_total. General PromQL expressions are not implemented.",
                "supported_metrics": list(supported),
            },
        )

    try:
        from utils.metrics import metrics as _m
        snap = await _async_wrap(_m.get_snapshot)
        counters = snap.get("counters") if isinstance(snap, dict) else None
        raw_value = counters.get(supported[metric_name]) if isinstance(counters, dict) else None
        if not isinstance(raw_value, (int, float)):
            return JSONResponse(
                status_code=503,
                content={
                    "status": "error",
                    "error": "metric_unavailable",
                    "message": f"The backend has not exposed a numeric value for {metric_name}.",
                },
            )
        return JSONResponse({
            "status": "success",
            "data": {
                "result": [{
                    "metric": {"__name__": metric_name},
                    "value": [int(datetime.now(timezone.utc).timestamp()), str(raw_value)],
                }]
            },
        })
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/metrics/query")

