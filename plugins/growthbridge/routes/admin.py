"""
Growthbridge Admin API — Authenticated CRUD endpoints.
Requires master_api_token or valid JWT.
"""

from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Depends, Request
from typing import Optional

router = APIRouter(prefix="/api/v1/growthbridge/admin", tags=["growthbridge-admin"])

# ═══════════════════════════════════════════════════════════════════════
# Shared Supabase client
# ═══════════════════════════════════════════════════════════════════════

_supabase_client = None


def _get_supabase():
    """Get or create the shared Supabase client."""
    global _supabase_client
    if _supabase_client is not None:
        return _supabase_client
    try:
        from services.supabase import get_service_client_from_settings
        _supabase_client = get_service_client_from_settings()
    except Exception:
        pass
    return _supabase_client


def set_supabase_client(client):
    """Allow lifecycle.py to inject the client at boot."""
    global _supabase_client
    _supabase_client = client


# ═══════════════════════════════════════════════════════════════════════
# Auth
# ═══════════════════════════════════════════════════════════════════════

def _require_auth(request: Request):
    """Require master API token for admin operations."""
    from config.settings import settings
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token or token != settings.master_api_token:
        raise HTTPException(401, "Unauthorized")
    return True


# ═══════════════════════════════════════════════════════════════════════
# Resource mapping
# ═══════════════════════════════════════════════════════════════════════

TABLES = {
    "services": "growthbridge_services",
    "projects": "growthbridge_projects",
    "talent": "growthbridge_talent",
    "applications": "growthbridge_applications",
    "partners": "growthbridge_partners",
    "blog": "growthbridge_blog",
    "testimonials": "growthbridge_testimonials",
}


# ═══════════════════════════════════════════════════════════════════════
# Dashboard Stats
# ═══════════════════════════════════════════════════════════════════════

@router.get("/stats")
async def get_stats(_auth=Depends(_require_auth)):
    """Get Growthbridge admin dashboard stats."""
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    stats = {}
    
    for key, table in TABLES.items():
        try:
            result = client.table(table).select("id", count="exact").execute()
            stats[key] = result.count if hasattr(result, 'count') else len(result.data or [])
        except Exception:
            stats[key] = 0
    
    # Count pending applications specifically
    try:
        pending = client.table("growthbridge_applications") \
            .select("id", count="exact") \
            .eq("status", "pending") \
            .execute()
        stats["pending_applications"] = pending.count if hasattr(pending, 'count') else len(pending.data or [])
    except Exception:
        stats["pending_applications"] = 0
    
    return {"stats": stats}


# ═══════════════════════════════════════════════════════════════════════
# Generic CRUD
# ═══════════════════════════════════════════════════════════════════════

@router.get("/{resource}")
async def list_resource(resource: str, _auth=Depends(_require_auth)):
    """List all records for a resource."""
    if resource not in TABLES:
        raise HTTPException(404, f"Unknown resource: {resource}")
    
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    try:
        result = client.table(TABLES[resource]) \
            .select("*") \
            .order("created_at", desc=True) \
            .limit(100) \
            .execute()
        return {"data": result.data or [], "resource": resource}
    except Exception as e:
        raise HTTPException(502, f"Database query failed: {str(e)[:200]}")


@router.get("/{resource}/{record_id}")
async def get_resource(resource: str, record_id: str, _auth=Depends(_require_auth)):
    """Get a single record."""
    if resource not in TABLES:
        raise HTTPException(404, f"Unknown resource: {resource}")
    
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    try:
        result = client.table(TABLES[resource]) \
            .select("*") \
            .eq("id", record_id) \
            .limit(1) \
            .execute()
        if not result.data:
            raise HTTPException(404, "Record not found")
        return result.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Database query failed: {str(e)[:200]}")


@router.post("/{resource}")
async def create_resource(resource: str, data: dict, _auth=Depends(_require_auth)):
    """Create a new record."""
    if resource not in TABLES:
        raise HTTPException(404, f"Unknown resource: {resource}")
    
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    try:
        # Add timestamps
        now = datetime.now(timezone.utc).isoformat()
        data.setdefault("created_at", now)
        data.setdefault("updated_at", now)
        
        result = client.table(TABLES[resource]).insert(data).execute()
        return {"ok": True, "data": result.data[0] if result.data else None}
    except Exception as e:
        raise HTTPException(502, f"Database insert failed: {str(e)[:200]}")


@router.put("/{resource}/{record_id}")
async def update_resource(resource: str, record_id: str, data: dict, _auth=Depends(_require_auth)):
    """Update a record."""
    if resource not in TABLES:
        raise HTTPException(404, f"Unknown resource: {resource}")
    
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    try:
        # Set updated_at to actual current time (not the string "now()")
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        
        result = client.table(TABLES[resource]) \
            .update(data) \
            .eq("id", record_id) \
            .execute()
        
        if not result.data:
            raise HTTPException(404, "Record not found")
        
        return {"ok": True, "data": result.data[0]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Database update failed: {str(e)[:200]}")


@router.delete("/{resource}/{record_id}")
async def delete_resource(resource: str, record_id: str, _auth=Depends(_require_auth)):
    """Delete a record."""
    if resource not in TABLES:
        raise HTTPException(404, f"Unknown resource: {resource}")
    
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    try:
        client.table(TABLES[resource]).delete().eq("id", record_id).execute()
        return {"ok": True, "message": f"{resource} deleted"}
    except Exception as e:
        raise HTTPException(502, f"Database delete failed: {str(e)[:200]}")
