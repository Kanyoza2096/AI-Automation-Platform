from fastapi import APIRouter, HTTPException, Depends, Request

router = APIRouter(prefix="/api/v1/trends/admin", tags=["trends-admin"])

def _require_auth(request: Request):
    from config.settings import settings
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token or token != settings.master_api_token:
        raise HTTPException(401, "Unauthorized")
    return True

def _get_supabase():
    try:
        from services.supabase import get_service_client_from_settings
        return get_service_client_from_settings()
    except: return None

@router.get("/sources")
async def list_sources(_auth=Depends(_require_auth)):
    client = _get_supabase()
    if not client: raise HTTPException(503, "Database unavailable")
    result = client.table("trend_sources").select("*").execute()
    return {"sources": result.data or []}

@router.post("/sources")
async def create_source(data: dict, _auth=Depends(_require_auth)):
    client = _get_supabase()
    if not client: raise HTTPException(503, "Database unavailable")
    result = client.table("trend_sources").insert(data).execute()
    return {"ok": True, "data": result.data[0] if result.data else None}

@router.get("/rules")
async def list_rules(_auth=Depends(_require_auth)):
    client = _get_supabase()
    if not client: raise HTTPException(503, "Database unavailable")
    result = client.table("trend_brand_rules").select("*").execute()
    return {"rules": result.data or []}

@router.post("/rules")
async def create_rule(data: dict, _auth=Depends(_require_auth)):
    client = _get_supabase()
    if not client: raise HTTPException(503, "Database unavailable")
    result = client.table("trend_brand_rules").insert(data).execute()
    return {"ok": True, "data": result.data[0] if result.data else None}

@router.post("/scan")
async def trigger_scan(_auth=Depends(_require_auth)):
    try:
        from plugins.trend_intelligence.plugin import trend_plugin
        if trend_plugin:
            trend_plugin.scan_all_sources()
            trend_plugin.process_trends()
            return {"ok": True, "message": "Scan triggered"}
        raise HTTPException(503, "Plugin not running")
    except Exception as e:
        raise HTTPException(500, str(e))
