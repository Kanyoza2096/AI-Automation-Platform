"""
Monitor Plugin Admin Routes v2.0
"""

from fastapi import APIRouter, HTTPException, Depends, Request
from typing import Optional

router = APIRouter(
    prefix="/api/v1/monitor/admin", tags=["monitor-admin"]
)


# ═══════════════════════════════════════════════════════════════════════════
# AUTH
# ═══════════════════════════════════════════════════════════════════════════

def _require_auth(request: Request):
    from config.settings import settings

    token = request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")
    if (
        not token
        or token != settings.master_api_token
    ):
        raise HTTPException(401, "Unauthorized")
    return True


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _get_supabase():
    try:
        from services.supabase import (
            get_service_client_from_settings,
        )
        return get_service_client_from_settings()
    except Exception:
        return None


def _get_monitor_plugin():
    """Get the running monitor plugin instance."""
    try:
        from plugins.monitor.plugin import (
            monitor_plugin,
        )
        return monitor_plugin
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════
# TARGET CRUD
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/targets")
async def list_targets(
    _auth=Depends(_require_auth),
):
    client = _get_supabase()
    if not client:
        raise HTTPException(
            503, "Database unavailable"
        )

    result = (
        client.table("monitor_targets")
        .select("*")
        .order("name")
        .execute()
    )
    return {"targets": result.data or []}


@router.post("/targets")
async def create_target(
    data: dict, _auth=Depends(_require_auth)
):
    client = _get_supabase()
    if not client:
        raise HTTPException(
            503, "Database unavailable"
        )

    # Validate required fields
    if not data.get("name") or not data.get("url"):
        raise HTTPException(
            400, "name and url are required"
        )

    # Validate optional fields
    if "expected_status" in data:
        try:
            data["expected_status"] = int(
                data["expected_status"]
            )
        except (ValueError, TypeError):
            raise HTTPException(
                400,
                "expected_status must be an integer",
            )

    if "is_paused" in data:
        if not isinstance(
            data["is_paused"], bool
        ):
            raise HTTPException(
                400,
                "is_paused must be a boolean",
            )

    result = (
        client.table("monitor_targets")
        .insert(data)
        .execute()
    )
    return {
        "ok": True,
        "data": (
            result.data[0] if result.data else None
        ),
    }


@router.put("/targets/{target_id}")
async def update_target(
    target_id: str,
    data: dict,
    _auth=Depends(_require_auth),
):
    client = _get_supabase()
    if not client:
        raise HTTPException(
            503, "Database unavailable"
        )

    # Validate optional fields on update
    if "expected_status" in data:
        try:
            data["expected_status"] = int(
                data["expected_status"]
            )
        except (ValueError, TypeError):
            raise HTTPException(
                400,
                "expected_status must be an integer",
            )

    if "is_paused" in data:
        if not isinstance(
            data["is_paused"], bool
        ):
            raise HTTPException(
                400,
                "is_paused must be a boolean",
            )

    client.table("monitor_targets").update(
        data
    ).eq("id", target_id).execute()
    return {"ok": True}


@router.delete("/targets/{target_id}")
async def delete_target(
    target_id: str,
    _auth=Depends(_require_auth),
):
    client = _get_supabase()
    if not client:
        raise HTTPException(
            503, "Database unavailable"
        )

    client.table("monitor_targets").delete().eq(
        "id", target_id
    ).execute()
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════════════════
# v2.0 — STATS & ACTIONS
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/stats")
async def get_stats(
    _auth=Depends(_require_auth),
):
    """Get monitor plugin statistics."""
    plugin = _get_monitor_plugin()
    if not plugin:
        raise HTTPException(
            503, "Monitor plugin not running"
        )

    return {
        "ok": True,
        "stats": plugin.get_stats(),
    }


@router.get("/health")
async def get_health(
    _auth=Depends(_require_auth),
):
    """Get monitor plugin health."""
    plugin = _get_monitor_plugin()
    if not plugin:
        return {
            "ok": True,
            "health": {
                "status": "stopped",
                "stats": {},
            },
        }

    return {
        "ok": True,
        "health": plugin.health_check(),
    }


@router.post("/check-now")
async def check_now(
    _auth=Depends(_require_auth),
):
    """Trigger an immediate check of all targets."""
    plugin = _get_monitor_plugin()
    if not plugin:
        raise HTTPException(
            503, "Monitor plugin not running"
        )

    plugin._check_all_targets()
    stats = plugin.get_stats()

    return {
        "ok": True,
        "message": "All checks triggered",
        "stats": stats,
    }


@router.get("/checks")
async def get_checks(
    target_id: Optional[str] = None,
    limit: int = 50,
    _auth=Depends(_require_auth),
):
    """Get recent check history."""
    client = _get_supabase()
    if not client:
        raise HTTPException(
            503, "Database unavailable"
        )

    query = (
        client.table("monitor_checks")
        .select("*")
        .order("checked_at", desc=True)
        .limit(min(limit, 200))
    )

    if target_id:
        query = query.eq(
            "target_id", target_id
        )

    result = query.execute()
    return {
        "ok": True,
        "checks": result.data or [],
    }
