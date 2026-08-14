"""
Monitor Plugin Admin Routes v2.1 — FIXED
"""

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, Field
from typing import Optional
import hmac

router = APIRouter(prefix="/api/v1/monitor/admin", tags=["monitor-admin"])


# ═══════════════════════════════════════════════════════════════════════════
# AUTH (with hmac.compare_digest)
# ═══════════════════════════════════════════════════════════════════════════

def _require_auth(request: Request):
    from config.settings import settings

    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        token = request.headers.get("X-API-Key", "")

    master_token = getattr(settings, "master_api_token", "") or ""

    if not isinstance(token, str) or not isinstance(master_token, str):
        raise HTTPException(401, "Unauthorized")

    if not master_token or not token:
        raise HTTPException(401, "Unauthorized")

    # v2.1 FIX: Use hmac.compare_digest for timing-safe comparison
    if not hmac.compare_digest(token, master_token):
        raise HTTPException(401, "Unauthorized")

    return True


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
async def list_targets(_auth=Depends(_require_auth)):
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")

    result = client.table("monitor_targets").select("*").order("name").execute()
    return {"targets": result.data or []}


@router.post("/targets")
async def create_target(body: TargetCreate, _auth=Depends(_require_auth)):
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")

    data = body.model_dump()
    result = client.table("monitor_targets").insert(data).execute()
    return {"ok": True, "data": result.data[0] if result.data else None}


@router.put("/targets/{target_id}")
async def update_target(
    target_id: str, body: TargetUpdate, _auth=Depends(_require_auth)
):
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")

    # Only include non-None fields
    data = {k: v for k, v in body.model_dump().items() if v is not None}

    if not data:
        raise HTTPException(400, "No fields to update")

    client.table("monitor_targets").update(data).eq("id", target_id).execute()
    return {"ok": True}


@router.delete("/targets/{target_id}")
async def delete_target(target_id: str, _auth=Depends(_require_auth)):
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")

    client.table("monitor_targets").delete().eq("id", target_id).execute()
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

    if target_id:
        query = query.eq("target_id", target_id)

    result = query.execute()
    return {"ok": True, "checks": result.data or []}
