# plugins/backup/routes/admin.py
from fastapi import APIRouter, HTTPException, Depends, Request

router = APIRouter(prefix="/api/v1/backup/admin", tags=["backup-admin"])


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
    result = client.table("backup_targets").select("*").execute()
    return {"targets": result.data or []}


@router.post("/targets")
async def create_target(data: dict, _auth=Depends(_require_auth)):
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    if not data.get("name") or not data.get("connection_string_encrypted"):
        raise HTTPException(400, "name and connection_string_encrypted are required")
    result = client.table("backup_targets").insert(data).execute()
    return {"ok": True, "data": result.data[0] if result.data else None}


@router.post("/run")
async def run_backup_now(_auth=Depends(_require_auth)):
    try:
        from plugins.backup.plugin import backup_plugin
        if backup_plugin:
            backup_plugin._handle_run({})
            return {"ok": True, "message": "Backup triggered"}
        raise HTTPException(503, "Backup plugin not running")
    except Exception as e:
        raise HTTPException(500, str(e))
