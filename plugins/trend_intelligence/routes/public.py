from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/v1/trends", tags=["trends"])

def _get_supabase():
    try:
        from services.supabase import get_service_client_from_settings
        return get_service_client_from_settings()
    except: return None

@router.get("/live")
async def get_live_trends(limit: int = 20):
    client = _get_supabase()
    if not client: raise HTTPException(503, "Database unavailable")
    trends = client.table("trend_posts").select("*").order("fetched_at", desc=True).limit(limit).execute()
    return {"trends": trends.data or []}

@router.get("/decisions")
async def get_decisions(limit: int = 30):
    client = _get_supabase()
    if not client: raise HTTPException(503, "Database unavailable")
    decisions = client.table("trend_decisions").select("*").order("decided_at", desc=True).limit(limit).execute()
    return {"decisions": decisions.data or []}
