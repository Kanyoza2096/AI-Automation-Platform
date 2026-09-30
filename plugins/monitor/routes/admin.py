"""
Monitor Plugin Admin Routes v2.1 — FIXED
"""

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, Field
from typing import Optional

router = APIRouter(prefix="/api/v1/monitor/admin", tags=["monitor-admin"])


# ═══════════════════════════════════════════════════════════════════════════
# AUTH (with hmac.compare_digest)
# ═══════════════════════════════════════════════════════════════════════════

async def _require_auth(request: Request):
    """Use the platform authentication door and require platform authority."""
    from core.security.deps import require_species_privileges
    return await require_species_privileges(request)


# ═══════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ═══════════════════════════════════════════════════════════════════════════

class TargetCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    url: str = Field(..., min_length=1, max_length=2000)
    expected_status: int = Field(default=200, ge=100, le=599)
    is_paused: bool = Field(default=False)


class TargetUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    url: Optional[str] = Field(default=None, min_length=1, max_length=2000)
    expected_status: Optional[int] = Field(default=None, ge=100, le=599)
    is_paused: Optional[bool] = Field(default=None)


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _workspace_id(request: Request) -> str:
    """Return the workspace selected by canonical RequestContext."""
    from core.request_context import require_request_context
    ctx = require_request_context()
    if not ctx.workspace_id:
        raise HTTPException(403, "Authenticated request has no workspace scope")
    return str(ctx.workspace_id)


def _get_supabase():
    try:
        from services.supabase import get_service_client_from_settings
        return get_service_client_from_settings()
    except Exception:
        return None


def _get_monitor_plugin():
    """Get the running monitor plugin instance."""
    try:
        from plugins.monitor.plugin import monitor_plugin, init_monitor_plugin
        if monitor_plugin is None:
            # Try to initialize
            client = _get_supabase()
            if client:
                monitor_plugin = init_monitor_plugin(supabase_client=client)
        return monitor_plugin
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════
# TARGET CRUD (with Pydantic validation)
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/targets")
async def list_targets(request: Request, _auth=Depends(_require_auth)):
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")

    workspace_id = _workspace_id(request)
    result = client.table("monitor_targets").select("*").eq("workspace_id", workspace_id).order("name").execute()
    return {"targets": result.data or []}


@router.post("/targets")
async def create_target(body: TargetCreate, request: Request, _auth=Depends(_require_auth)):
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")

    data = body.model_dump()
    data["workspace_id"] = _workspace_id(request)
    from core.ssrf_guard import validate_public_http_url
    ok, reason = validate_public_http_url(data["url"])
    if not ok:
        raise HTTPException(400, f"URL blocked: {reason}")
    result = client.table("monitor_targets").insert(data).execute()
    return {"ok": True, "data": result.data[0] if result.data else None}


@router.put("/targets/{target_id}")
async def update_target(
    target_id: str, body: TargetUpdate, request: Request, _auth=Depends(_require_auth)
):
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")

    # Only include non-None fields
    data = {k: v for k, v in body.model_dump().items() if v is not None}

    if not data:
        raise HTTPException(400, "No fields to update")
    workspace_id = _workspace_id(request)
    if "url" in data:
        from core.ssrf_guard import validate_public_http_url
        ok, reason = validate_public_http_url(data["url"])
        if not ok:
            raise HTTPException(400, f"URL blocked: {reason}")
    result = client.table("monitor_targets").update(data).eq("id", target_id).eq("workspace_id", workspace_id).execute()
    if not getattr(result, "data", None):
        raise HTTPException(404, "Target not found")
    return {"ok": True}


@router.delete("/targets/{target_id}")
async def delete_target(target_id: str, request: Request, _auth=Depends(_require_auth)):
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")

    result = client.table("monitor_targets").delete().eq("id", target_id).eq("workspace_id", _workspace_id(request)).execute()
    if not getattr(result, "data", None):
        raise HTTPException(404, "Target not found")
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════════════════
# STATS & ACTIONS
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/stats")
async def get_stats(_auth=Depends(_require_auth)):
    plugin = _get_monitor_plugin()
    if not plugin:
        raise HTTPException(503, "Monitor plugin not running")
    return {"ok": True, "stats": plugin.get_stats()}


@router.get("/health")
async def get_health(_auth=Depends(_require_auth)):
    plugin = _get_monitor_plugin()
    if not plugin:
        return {"ok": True, "health": {"status": "stopped", "stats": {}}}
    return {"ok": True, "health": plugin.health_check()}


@router.post("/check-now")
async def check_now(_auth=Depends(_require_auth)):
    plugin = _get_monitor_plugin()
    if not plugin:
        raise HTTPException(503, "Monitor plugin not running")
    plugin._check_all_targets()
    return {"ok": True, "message": "All checks triggered", "stats": plugin.get_stats()}


@router.get("/checks")
async def get_checks(
    request: Request,
    target_id: Optional[str] = None,
    limit: int = 50,
    _auth=Depends(_require_auth),
):
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")

    query = (
        client.table("monitor_checks")
        .select("*")
        .order("checked_at", desc=True)
        .limit(min(limit, 200))
    )

    workspace_id = _workspace_id(request)
    query = query.eq("workspace_id", workspace_id)
    if target_id:
        query = query.eq("target_id", target_id)

    result = query.execute()
    return {"ok": True, "checks": result.data or []}
