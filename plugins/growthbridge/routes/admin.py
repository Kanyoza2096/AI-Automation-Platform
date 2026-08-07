"""
Growthbridge Admin API — Authenticated CRUD endpoints.
Requires master_api_token or valid JWT.
"""

from fastapi import APIRouter, HTTPException, Depends, Request
from typing import Optional

router = APIRouter(prefix="/api/v1/growthbridge/admin", tags=["growthbridge-admin"])


def _require_auth(request: Request):
    """Require master API token for admin operations."""
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


# ── Dashboard Stats ───────────────────────────────────────────────────────

@router.get("/stats")
async def get_stats(_auth=Depends(_require_auth)):
    """Get Growthbridge admin dashboard stats."""
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    stats = {}
    tables = {
        "services": "growthbridge_services",
        "projects": "growthbridge_projects",
        "talent": "growthbridge_talent",
        "applications": "growthbridge_applications",
        "partners": "growthbridge_partners",
        "blog": "growthbridge_blog",
    }
    
    for key, table in tables.items():
        try:
            result = client.table(table).select("id", count="exact").execute()
            stats[key] = result.count if hasattr(result, 'count') else len(result.data or [])
        except Exception:
            stats[key] = 0
    
    # Pending applications
    try:
        pending = client.table("growthbridge_applications").select("id", count="exact").eq("status", "pending").execute()
        stats["pending_applications"] = pending.count if hasattr(pending, 'count') else len(pending.data or [])
    except Exception:
        stats["pending_applications"] = 0
    
    return {"stats": stats}


# ── Generic CRUD for any Growthbridge table ───────────────────────────────

TABLES = {
    "services": "growthbridge_services",
    "projects": "growthbridge_projects",
    "talent": "growthbridge_talent",
    "applications": "growthbridge_applications",
    "partners": "growthbridge_partners",
    "blog": "growthbridge_blog",
    "testimonials": "growthbridge_testimonials",
}


@router.get("/{resource}")
async def list_resource(resource: str, _auth=Depends(_require_auth)):
    """List all records for a resource."""
    if resource not in TABLES:
        raise HTTPException(404, f"Unknown resource: {resource}")
    
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    result = client.table(TABLES[resource]).select("*").order("created_at", desc=True).limit(100).execute()
    return {"data": result.data or [], "resource": resource}


@router.get("/{resource}/{record_id}")
async def get_resource(resource: str, record_id: str, _auth=Depends(_require_auth)):
    """Get a single record."""
    if resource not in TABLES:
        raise HTTPException(404, f"Unknown resource: {resource}")
    
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    result = client.table(TABLES[resource]).select("*").eq("id", record_id).limit(1).execute()
    if not result.data:
        raise HTTPException(404, "Record not found")
    return result.data[0]


@router.post("/{resource}")
async def create_resource(resource: str, data: dict, _auth=Depends(_require_auth)):
    """Create a new record."""
    if resource not in TABLES:
        raise HTTPException(404, f"Unknown resource: {resource}")
    
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    result = client.table(TABLES[resource]).insert(data).execute()
    return {"ok": True, "data": result.data[0] if result.data else None}


@router.put("/{resource}/{record_id}")
async def update_resource(resource: str, record_id: str, data: dict, _auth=Depends(_require_auth)):
    """Update a record."""
    if resource not in TABLES:
        raise HTTPException(404, f"Unknown resource: {resource}")
    
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    data["updated_at"] = "now()"
    result = client.table(TABLES[resource]).update(data).eq("id", record_id).execute()
    return {"ok": True, "data": result.data[0] if result.data else None}


@router.delete("/{resource}/{record_id}")
async def delete_resource(resource: str, record_id: str, _auth=Depends(_require_auth)):
    """Delete a record."""
    if resource not in TABLES:
        raise HTTPException(404, f"Unknown resource: {resource}")
    
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    client.table(TABLES[resource]).delete().eq("id", record_id).execute()
    return {"ok": True, "message": f"{resource} deleted"}
