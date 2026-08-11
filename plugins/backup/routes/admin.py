"""Backup Plugin Admin Routes v2.0"""

from fastapi import (
    APIRouter,
    HTTPException,
    Depends,
    Request,
)

router = APIRouter(
    prefix="/api/v1/backup/admin",
    tags=["backup-admin"],
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


def _get_backup_plugin():
    """Get the running backup plugin instance."""
    try:
        from plugins.backup.plugin import (
            backup_plugin,
        )
        return backup_plugin
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
        client.table("backup_targets")
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

    if not data.get(
        "name"
    ) or not data.get(
        "connection_string_encrypted"
    ):
        raise HTTPException(
            400,
            "name and connection_string_encrypted are required",
        )

    # Validate optional fields
    if "retention_days" in data:
        try:
            data["retention_days"] = int(
                data["retention_days"]
            )
        except (ValueError, TypeError):
            raise HTTPException(
                400,
                "retention_days must be an integer",
            )

    if "enabled" in data:
        if not isinstance(
            data["enabled"], bool
        ):
            raise HTTPException(
                400,
                "enabled must be a boolean",
            )

    result = (
        client.table("backup_targets")
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

    # Validate optional fields
    if "retention_days" in data:
        try:
            data["retention_days"] = int(
                data["retention_days"]
            )
        except (ValueError, TypeError):
            raise HTTPException(
                400,
                "retention_days must be an integer",
            )

    if "enabled" in data:
        if not isinstance(
            data["enabled"], bool
        ):
            raise HTTPException(
                400,
                "enabled must be a boolean",
            )

    client.table("backup_targets").update(
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

    client.table("backup_targets").delete().eq(
        "id", target_id
    ).execute()
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════════════════
# ACTIONS
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/run")
async def run_backup_now(
    _auth=Depends(_require_auth),
):
    """Trigger an immediate backup for all enabled targets."""
    plugin = _get_backup_plugin()
    if not plugin:
        raise HTTPException(
            503, "Backup plugin not running"
        )

    try:
        plugin._handle_run({})
        stats = plugin.get_stats()
        return {
            "ok": True,
            "message": "Backup triggered",
            "stats": stats,
        }
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/run/{target_id}")
async def run_backup_for_target(
    target_id: str,
    _auth=Depends(_require_auth),
):
    """Trigger a backup for a specific target."""
    client = _get_supabase()
    if not client:
        raise HTTPException(
            503, "Database unavailable"
        )

    # Find the target
    target = (
        client.table("backup_targets")
        .select("*")
        .eq("id", target_id)
        .limit(1)
        .execute()
    )

    if not target.data:
        raise HTTPException(
            404, "Target not found"
        )

    plugin = _get_backup_plugin()
    if not plugin:
        raise HTTPException(
            503, "Backup plugin not running"
        )

    try:
        plugin._run_backup(target.data[0])
        return {
            "ok": True,
            "message": f"Backup triggered for {target.data[0].get('name', target_id)}",
        }
    except Exception as e:
        raise HTTPException(500, str(e))


# ═══════════════════════════════════════════════════════════════════════════
# v2.0 — STATS & HEALTH
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/stats")
async def get_stats(
    _auth=Depends(_require_auth),
):
    """Get backup plugin statistics."""
    plugin = _get_backup_plugin()
    if not plugin:
        raise HTTPException(
            503, "Backup plugin not running"
        )

    return {
        "ok": True,
        "stats": plugin.get_stats(),
    }


@router.get("/health")
async def get_health(
    _auth=Depends(_require_auth),
):
    """Get backup plugin health."""
    plugin = _get_backup_plugin()
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
