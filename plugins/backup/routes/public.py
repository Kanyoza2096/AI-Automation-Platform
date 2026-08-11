"""Backup Plugin Public Routes v2.0"""

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(
    prefix="/api/v1/backup", tags=["backup"]
)


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
# STATUS
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/status")
async def get_backup_status():
    """Get backup status for all targets."""
    client = _get_supabase()
    if not client:
        raise HTTPException(
            503, "Database unavailable"
        )

    plugin = _get_backup_plugin()
    targets = (
        client.table("backup_targets")
        .select("*")
        .order("name")
        .execute()
    )

    result = []
    for t in targets.data or []:
        last = (
            client.table("backup_records")
            .select("*")
            .eq("target_id", t["id"])
            .order("started_at", desc=True)
            .limit(1)
            .execute()
        )

        last_record = (
            last.data[0] if last.data else None
        )

        result.append({
            "name": t.get("name", "Unknown"),
            "enabled": t.get("enabled", False),
            "schedule": t.get("schedule", "daily"),
            "retention_days": t.get(
                "retention_days", 30
            ),
            "last_backup": last_record,
            "last_backup_status": (
                last_record.get("status", "never")
                if last_record
                else "never"
            ),
        })

    response = {"targets": result}

    # v2.0 — Include plugin stats if available
    if plugin:
        response["stats"] = plugin.get_stats()

    return response


# ═══════════════════════════════════════════════════════════════════════════
# RECORDS
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/records")
async def get_backup_records(
    limit: int = Query(
        default=20, ge=1, le=100
    ),
    target_id: str = None,
    status: str = None,
):
    """Get backup records with optional filters."""
    client = _get_supabase()
    if not client:
        raise HTTPException(
            503, "Database unavailable"
        )

    query = (
        client.table("backup_records")
        .select("*")
        .order("started_at", desc=True)
        .limit(limit)
    )

    if target_id:
        query = query.eq("target_id", target_id)

    if status:
        query = query.eq("status", status)

    result = query.execute()
    return {"records": result.data or []}


# ═══════════════════════════════════════════════════════════════════════════
# v2.0 — QUICK STATUS
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/quick")
async def get_quick_status():
    """Lightweight backup status — for dashboards and health checks."""
    plugin = _get_backup_plugin()

    if not plugin:
        return {
            "status": "unknown",
            "message": "Backup plugin not running",
        }

    stats = plugin.get_stats()

    # Determine status
    if (
        stats.get("backups_failed", 0) == 0
        and stats.get("backups_completed", 0) > 0
    ):
        status = "healthy"
    elif stats.get("backups_completed", 0) == 0:
        status = "no_backups_yet"
    elif stats.get("backups_failed", 0) > 0:
        status = "has_failures"
    else:
        status = "unknown"

    return {
        "status": status,
        "stats": stats,
    }
