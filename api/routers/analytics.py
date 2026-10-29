from fastapi import APIRouter, Depends, Request, HTTPException
from typing import Optional
from api.dependencies import *
from api.v1_models import *
from api.v1_helpers import *
from api.v1_stores import *

router = APIRouter()

# ═══════════════════════════════════════════════════════════════════════════
# ANALYTICS
#
# INCIDENT-2026-10-16-analytics-leak:
#   Prior to this patch, /analytics/performance, /analytics/posts-performance,
#   and /analytics/engagement-heatmap executed unscoped queries against
#   post_history and returned rows from every workspace to any authenticated
#   caller. The service-role Supabase client bypasses RLS, so there was no
#   secondary guard. Fix: resolve workspace_id from the request via the
#   canonical resolver in api.dependencies and filter every query by it.
#
#   The RPC branch in /analytics/engagement-heatmap remains disabled until
#   per-request JWT propagation into the Supabase client is in place; without
#   it, get_engagement_heatmap cannot scope to the caller's workspace.
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/analytics")
async def get_analytics(
    _auth: bool = Depends(require_auth),
):
    """Get analytics overview — workspace-scoped.

    NOTE: currently sourced from the process-global `analytics` singleton,
    not from workspace-scoped storage. The workspace_id in the response is
    the caller's; the stats themselves are not yet per-workspace. Tracked
    separately from the leak incident above.
    """
    try:
        from analytics.tracker import analytics
        workspace_id = auth_workspace_id_var.get()
        stats = await _async_wrap(analytics.get_stats)
        return _ok({
            "analytics": stats,
            "workspace_id": workspace_id,
            "as_of": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/analytics")


@router.get("/analytics/performance")
async def analytics_performance(
    request: Request,
    _auth: bool = Depends(require_auth),
):
    """Get post performance data — workspace-scoped."""
    try:
        workspace_id = _resolve_workspace_from_request(request)
        if not workspace_id:
            raise HTTPException(status_code=403, detail="Workspace context required")

        from services.supabase import supabase_service
        if supabase_service and supabase_service.client:
            result = await _async_wrap(
                lambda: supabase_service.client.table("post_history")
                .select("*")
                .eq("workspace_id", workspace_id)
                .order("published_at", desc=True)
                .limit(20)
                .execute()
            )
            return _ok({"posts": result.data or []})
        return _ok({"posts": [], "note": "Supabase not configured"})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/analytics/performance")


@router.get("/analytics/posts-performance")
async def analytics_posts_performance(
    request: Request,
    _auth: bool = Depends(require_auth),
    limit: int = 20,
):
    """Get post performance with configurable limit — workspace-scoped."""
    try:
        limit = min(50, max(1, limit))
        workspace_id = _resolve_workspace_from_request(request)
        if not workspace_id:
            raise HTTPException(status_code=403, detail="Workspace context required")

        from services.supabase import supabase_service
        if supabase_service and supabase_service.client:
            result = await _async_wrap(
                lambda: supabase_service.client.table("post_history")
                .select("*")
                .eq("workspace_id", workspace_id)
                .order("published_at", desc=True)
                .limit(limit)
                .execute()
            )
            data = result.data or []
            return _ok({"posts": data, "count": len(data)})
        return _ok({"posts": [], "count": 0, "note": "Supabase not configured"})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/analytics/posts-performance")


@router.get("/analytics/token-usage")
async def analytics_token_usage(_auth: bool = Depends(require_auth)):
    """Get token usage statistics."""
    try:
        from utils.metrics import metrics as _m
        snap = await _async_wrap(_m.get_snapshot)
        counters = snap.get("counters", {})
        return _ok({
            "total_tokens_estimated": counters.get("gemini_tokens_used", 0),
            "api_calls_today": counters.get("requests_total", 0),
            "daily": [],
            "as_of": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/analytics/token-usage")


@router.get("/analytics/engagement-heatmap")
async def analytics_engagement_heatmap(
    request: Request,
    _auth: bool = Depends(require_auth),
):
    """Get engagement heatmap data — workspace-scoped.

    The previous implementation called the `get_engagement_heatmap` RPC with
    empty parameters and relied on the RPC to scope internally. Because the
    Supabase client does not carry the caller's JWT, `auth.jwt()` inside the
    RPC resolves to the service-role identity, not the user — so the RPC
    could not have been scoping correctly. The RPC branch is disabled until
    per-request JWT propagation is in place.
    """
    try:
        workspace_id = _resolve_workspace_from_request(request)
        if not workspace_id:
            raise HTTPException(status_code=403, detail="Workspace context required")

        # TODO(INCIDENT-2026-10-16): re-enable once the Supabase client carries
        # the caller's JWT per request and the RPC scopes by workspace_id.
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        heatmap = [{"day": d, "hours": [0.0] * 24} for d in days]
        return _ok({"heatmap": heatmap, "note": "Pending per-request JWT propagation"})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/analytics/engagement-heatmap")
