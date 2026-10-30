from fastapi import APIRouter, Depends
from core.security.deps import require_species_privileges
from services.supabase import supabase_service

router = APIRouter(prefix="/api/v1/species", tags=["species"])


@router.get("/workspaces")
async def list_workspaces(ctx=Depends(require_species_privileges)):
    client = supabase_service.service_client or supabase_service.client
    if client is None:
        return {"ok": False, "error": "supabase_unavailable", "workspaces": [], "count": 0}
    try:
        result = (
            client.table("workspaces")
            .select("id, name, status, created_at")
            .order("created_at", desc=True)
            .execute()
        )
        return {"ok": True, "workspaces": result.data or [], "count": len(result.data or [])}
    except Exception as exc:
        return {"ok": False, "error": f"query_failed: {type(exc).__name__}", "workspaces": [], "count": 0}
