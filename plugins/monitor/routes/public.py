"""
Monitor Plugin Public Routes v2.1 — FIXED
"""

from fastapi import APIRouter, HTTPException, Query
from datetime import datetime, timezone, timedelta

router = APIRouter(prefix="/api/v1/monitor", tags=["monitor"])


def _get_supabase():
    try:
        from services.supabase import get_service_client_from_settings
        return get_service_client_from_settings()
    except Exception:
        return None


def _get_monitor_plugin():
    try:
        from plugins.monitor.plugin import monitor_plugin
        return monitor_plugin
    except Exception:
        return None


@router.get("/status")
async def get_monitor_status():
    """Get public monitoring status for all targets."""
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")

    plugin = _get_monitor_plugin()

    # v2.1 FIX: Single query for all targets
    targets_result = client.table("monitor_targets").select("*").order("name").execute()
    targets = targets_result.data or []

    if not targets:
        return {"targets": [], "stats": plugin.get_stats() if plugin else {}}

    # v2.1 FIX: Single query for all target IDs (no N+1)
    target_ids = [t["id"] for t in targets]

    # Single query for latest checks using RPC or IN clause
    # Supabase doesn't support DISTINCT ON easily — use two queries:
    # 1. All recent checks for all targets (last 30 days)
    # 2. Process in Python

    cutoff_30d = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

    all_checks_result = (
        client.table("monitor_checks")
        .select("target_id,status,response_time_ms,checked_at")
        .in_("target_id", target_ids)
        .gte("checked_at", cutoff_30d)
        .order("checked_at", desc=True)
        .limit(2000)  # Cap to prevent huge responses
        .execute()
    )
    all_checks = all_checks_result.data or []

    # Process in Python — group by target
    from collections import defaultdict
    checks_by_target = defaultdict(list)
    for check in all_checks:
        checks_by_target[check["target_id"]].append(check)

    # Get live states from plugin
    live_states = {}
    if plugin:
        live_states = dict(plugin._target_states)

    result = []
    for t in targets:
        target_id = t["id"]
        target_checks = checks_by_target.get(target_id, [])

        # Latest check is first (ordered desc)
        latest = target_checks[0] if target_checks else None

        # Uptime calculation
        total = len(target_checks)
        up_count = sum(1 for c in target_checks if c.get("status") == "up")

        result.append({
            "id": target_id,
            "name": t.get("name", t.get("url", "")),
            "url": t.get("url", ""),
            "status": latest.get("status", "unknown") if latest else "unknown",
            "live_state": live_states.get(target_id, "unknown"),
            "last_check": latest.get("checked_at") if latest else None,
            "response_time_ms": latest.get("response_time_ms") if latest else None,
            "uptime_pct": round(up_count / total * 100, 1) if total > 0 else 100.0,
            "checks_30d": total,
        })

    response = {"targets": result}
    if plugin:
        response["stats"] = plugin.get_stats()

    return response


@router.get("/checks/{target_id}")
async def get_target_checks(target_id: str, limit: int = Query(default=50, ge=1, le=200)):
    """Get check history for a specific target."""
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")

    result = (
        client.table("monitor_checks")
        .select("*")
        .eq("target_id", target_id)
        .order("checked_at", desc=True)
        .limit(limit)
        .execute()
    )

    return {"checks": result.data or []}


@router.get("/quick")
async def get_quick_status():
    """Lightweight status — suitable for status badges and health checks."""
    plugin = _get_monitor_plugin()

    if not plugin:
        return {"status": "unknown", "message": "Monitor plugin not running"}

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
        "targets_monitored": stats.get("targets_monitored", 0),
    }
