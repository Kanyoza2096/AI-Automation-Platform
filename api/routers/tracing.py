from fastapi import APIRouter, Depends, Request, HTTPException
from typing import Optional
from api.dependencies import *
from api.v1_models import *
from api.v1_helpers import *
from api.v1_stores import *

router = APIRouter()

# ═══════════════════════════════════════════════════════════════════════════
# TRACING — Digital Twin
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/traces/recent")
async def get_recent_traces(
    _auth: bool = Depends(require_auth),
    limit: int = 30,
):
    """A09: Get recent request traces for the Digital Twin dashboard."""
    try:
        limit = min(100, max(1, limit))
        from utils.tracing import get_recent_traces
        traces = await _async_wrap(get_recent_traces, limit=limit)
        return _ok({"traces": traces, "count": len(traces)})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/traces/recent")

