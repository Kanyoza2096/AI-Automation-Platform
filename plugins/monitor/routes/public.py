"""Monitor Plugin Public Routes v2.0"""

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(
    prefix="/api/v1/monitor", tags=["monitor"]
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


def _get_monitor_plugin():
    """Get the running monitor plugin for real-time state."""
    try:
        from plugins.monitor.plugin import (
            monitor_plugin,
        )
        return monitor_plugin
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════
# STATUS
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/status")
async def get_monitor_status():
    """Get public monitoring status for all targets."""
    client = _get_supabase()
    if not client:
        raise HTTPException(
            503, "Database unavailable"
        )

    from datetime import timedelta

    plugin = _get_monitor_plugin()
    targets = (
        client.table("monitor_targets")
        .select("*")
        .order("name")
        .execute()
    )

    result = []
    for t in targets.data or []:
        target_id = t["id"]

        # Last check
        last_check = (
            client.table("monitor_checks")
            .select("*")
            .eq("target_id", target_id)
            .order("checked_at", desc=True)
            .limit(1)
            .execute()
        )

        # Uptime — last 30 days only
        from datetime import timezone

        cutoff = (
            __import__("datetime").datetime.now(
                timezone.utc
            )
            - timedelta(days=30)
        ).isoformat()

        uptime = (
            client.table("monitor_checks")
            .select("status")
            .eq("target_id", target_id)
            .gte("checked_at", cutoff)
            .execute()
        )

        total = len(uptime.data or [])
        up_count = sum(
            1
            for c in (uptime.data or [])
            if c["status"] == "up"
        )

        # Get live state from plugin if available
        live_state = "unknown"
        if plugin:
            live_state = plugin._target_states.get(
                target_id, "unknown"
            )

        result.append({
            "name": t.get("name", t["url"]),
            "url": t["url"],
            "status": (
                last_check.data[0]["status"]
                if last_check.data
                else "unknown"
            ),
            "live_state": live_state,
            "last_check": (
                last_check.data[0]["checked_at"]
                if last_check.data
                else None
            ),
            "response_time_ms": (
                last_check.data[0][
                    "response_time_ms"
                ]
                if last_check.data
                else None
            ),
            "uptime_pct": (
                round(
                    up_count / total * 100, 1
                )
                if total > 0
                else 100.0
            ),
            "checks_30d": total,
        })

    # v2.0 — Include plugin stats if available
    response = {"targets": result}

    if plugin:
        response["stats"] = plugin.get_stats()

    return response


# ═══════════════════════════════════════════════════════════════════════════
# CHECKS HISTORY
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/checks/{target_id}")
async def get_target_checks(
    target_id: str,
    limit: int = Query(
        default=50, ge=1, le=200
    ),
):
    """Get check history for a specific target."""
    client = _get_supabase()
    if not client:
        raise HTTPException(
            503, "Database unavailable"
        )

    result = (
        client.table("monitor_checks")
        .select("*")
        .eq("target_id", target_id)
        .order("checked_at", desc=True)
        .limit(limit)
        .execute()
    )

    return {"checks": result.data or []}


# ═══════════════════════════════════════════════════════════════════════════
# v2.0 — QUICK STATUS (lightweight, for status badges)
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/quick")
async def get_quick_status():
    """Lightweight status — suitable for status badges and health checks."""
    plugin = _get_monitor_plugin()

    if not plugin:
        return {
            "status": "unknown",
            "message": "Monitor plugin not running",
        }

    stats = plugin.get_stats()
    targets_down = stats.get("targets_down", 0)

    if targets_down == 0:
        status = "all_systems_operational"
    elif targets_down == 1:
        status = "minor_outage"
    else:
        status = "major_outage"

    return {
        "status": status,
        "targets_down": targets_down,
        "targets_monitored": stats.get(
            "targets_monitored", 0
        ),
    }
