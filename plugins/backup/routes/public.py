# plugins/backup/routes/public.py
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/v1/backup", tags=["backup"])


def _get_supabase():
    try:
        from services.supabase import get_service_client_from_settings
        return get_service_client_from_settings()
    except Exception:
        return None


@router.get("/status")
async def get_backup_status():
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    targets = client.table("backup_targets").select("*").execute()
    result = []
    for t in (targets.data or []):
        last = client.table("backup_records") \
            .select("*").eq("target_id", t["id"]) \
            .order("started_at", desc=True).limit(1).execute()
        result.append({
            "name": t["name"],
            "enabled": t["enabled"],
            "schedule": t["schedule"],
            "retention_days": t["retention_days"],
            "last_backup": last.data[0] if last.data else None,
        })
    return {"targets": result}


@router.get("/records")
async def get_backup_records(limit: int = 20):
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    result = client.table("backup_records").select("*").order("started_at", desc=True).limit(limit).execute()
    return {"records": result.data or []}
