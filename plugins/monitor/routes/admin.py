from fastapi import APIRouter, HTTPException, Depends, Request

router = APIRouter(prefix="/api/v1/monitor/admin", tags=["monitor-admin"])


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
    except Exception:
        return None


@router.get("/targets")
async def list_targets(_auth=Depends(_require_auth)):
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    result = client.table("monitor_targets").select("*").order("name").execute()
    return {"targets": result.data or []}


@router.post("/targets")
async def create_target(data: dict, _auth=Depends(_require_auth)):
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    if not data.get("name") or not data.get("url"):
        raise HTTPException(400, "name and url are required")
    result = client.table("monitor_targets").insert(data).execute()
    return {"ok": True, "data": result.data[0] if result.data else None}


@router.put("/targets/{target_id}")
async def update_target(target_id: str, data: dict, _auth=Depends(_require_auth)):
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    client.table("monitor_targets").update(data).eq("id", target_id).execute()
    return {"ok": True}


@router.delete("/targets/{target_id}")
async def delete_target(target_id: str, _auth=Depends(_require_auth)):
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    client.table("monitor_targets").delete().eq("id", target_id).execute()
    return {"ok": True}
