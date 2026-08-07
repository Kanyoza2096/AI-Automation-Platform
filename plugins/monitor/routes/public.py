from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/v1/monitor", tags=["monitor"])


def _get_supabase():
    try:
        from services.supabase import get_service_client_from_settings
        return get_service_client_from_settings()
    except Exception:
        return None


@router.get("/status")
async def get_monitor_status():
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    targets = client.table("monitor_targets").select("*").execute()
    
    result = []
    for t in (targets.data or []):
        last_check = client.table("monitor_checks") \
            .select("*").eq("target_id", t["id"]) \
            .order("checked_at", desc=True).limit(1).execute()
        
        uptime = client.table("monitor_checks") \
            .select("status").eq("target_id", t["id"]) \
            .execute()
        
        total = len(uptime.data or [])
        up_count = sum(1 for c in (uptime.data or []) if c["status"] == "up")
        
        result.append({
            "name": t["name"],
            "url": t["url"],
            "status": last_check.data[0]["status"] if last_check.data else "unknown",
            "last_check": last_check.data[0]["checked_at"] if last_check.data else None,
            "response_time_ms": last_check.data[0]["response_time_ms"] if last_check.data else None,
            "uptime_pct": round(up_count / total * 100, 1) if total > 0 else 100,
        })
    
    return {"targets": result}


@router.get("/checks/{target_id}")
async def get_target_checks(target_id: str, limit: int = 50):
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    result = client.table("monitor_checks") \
        .select("*").eq("target_id", target_id) \
        .order("checked_at", desc=True).limit(limit).execute()
    
    return {"checks": result.data or []}
