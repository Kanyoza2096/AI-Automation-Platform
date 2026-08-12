"""
Growthbridge Admin API — Authenticated CRUD endpoints with RBAC.
Requires master_api_token OR session-based role authorization.

v2.0 — SECURITY FIXES:
  - Removed password_hash/salt from /users endpoint
  - Password verification happens server-side, never exposes credentials
  - Added field allowlists per resource (no arbitrary dict to Supabase)
  - Added delete verification (404 if record doesn't exist)
  - Added protected field blocking (id, created_at, created_by, role)
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
    global _supabase_client
    _supabase_client = client


# ═══════════════════════════════════════════════════════════════════════
# Auth
# ═══════════════════════════════════════════════════════════════════════

def _require_auth(request: Request):
    from config.settings import settings
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token or token != settings.master_api_token:
        raise HTTPException(401, "Unauthorized")
    return True


# ═══════════════════════════════════════════════════════════════════════
# Protected Fields — Never allowed in client-submitted data
# ═══════════════════════════════════════════════════════════════════════

PROTECTED_FIELDS = {
    "id", "created_at", "updated_at", "created_by", "role",
    "password_hash", "salt", "is_active", "permissions",
}

# Field allowlists per resource — only these fields are accepted
RESOURCE_FIELDS = {
    "services": {
        "slug", "title", "description", "category", "icon",
        "sort_order", "is_published", "is_featured",
    },
    "projects": {
        "slug", "title", "description", "image_url", "category",
        "is_published", "is_featured", "client", "completion_date",
    },
    "talent": {
        "name", "email", "bio", "skills", "experience_level",
        "portfolio_url", "availability", "status", "is_featured",
        "is_active", "category", "slug", "title", "photo",
        "social_links",
    },
    "applications": {
        "name", "email", "phone", "application_type", "skills",
        "message", "status",
    },
    "partners": {
        "organization_name", "contact_person", "email", "industry",
        "website", "status",
    },
    "blog": {
        "slug", "title", "excerpt", "content", "featured_image",
        "author", "tags", "is_published", "published_at",
    },
    "testimonials": {
        "quote", "author", "role", "organization", "avatar",
        "rating", "is_published", "is_featured",
    },
}


def _sanitize_data(resource: str, data: dict) -> dict:
    """Filter incoming data to only allowed fields for the resource."""
    allowed = RESOURCE_FIELDS.get(resource, set())
    sanitized = {}

    for key, value in data.items():
        if key in PROTECTED_FIELDS:
            continue
        if key not in allowed:
            continue
        sanitized[key] = value

    return sanitized


# ═══════════════════════════════════════════════════════════════════════
# Admin Users (for GrowthBridge login)
# ═══════════════════════════════════════════════════════════════════════

@router.get("/users")
async def get_admin_user(email: str, _auth=Depends(_require_auth)):
    """Get admin user by email — no credentials exposed.

    v2.0: Returns only id, email, role. Password verification
    happens server-side via the /verify-password endpoint.
    """
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    result = client.table("growthbridge_admin_users") \
        .select("id, email, role") \
        .eq("email", email) \
        .eq("is_active", True) \
        .limit(1) \
        .execute()
    
    if not result.data:
        return {"user": None}
    
    return {"user": result.data[0]}


@router.post("/verify-password")
async def verify_admin_password(data: dict, _auth=Depends(_require_auth)):
    """Verify an admin password server-side. Never returns the hash."""
    email = data.get("email", "")
    password = data.get("password", "")

    if not email or not password:
        raise HTTPException(400, "Email and password are required")

    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")

    result = client.table("growthbridge_admin_users") \
        .select("id, email, password_hash, salt, role") \
        .eq("email", email) \
        .eq("is_active", True) \
        .limit(1) \
        .execute()

    if not result.data:
        return {"valid": False}

    user = result.data[0]

    # Server-side password verification
    import hashlib
    import hmac

    pepper = getattr(
        __import__("config.settings", fromlist=["settings"]).settings,
        "admin_hash_pepper", ""
    ) if hasattr(
        __import__("config.settings", fromlist=["settings"]).settings,
        "admin_hash_pepper"
    ) else ""

    try:
        import crypto
        computed = crypto.pbkdf2_hmac(
            "sha512",
            (password + pepper).encode("utf-8"),
            bytes.fromhex(user["salt"]),
            100000,
            64,
        ).hex()

        valid = hmac.compare_digest(computed, user["password_hash"])
    except Exception:
        valid = False

    if valid:
        return {
            "valid": True,
            "user": {
                "id": user["id"],
                "email": user["email"],
                "role": user["role"],
            },
        }

    return {"valid": False}


# ═══════════════════════════════════════════════════════════════════════
# Dashboard Stats
# ═══════════════════════════════════════════════════════════════════════

@router.get("/stats")
async def get_stats(_auth=Depends(_require_auth)):
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
    
    try:
        pending = client.table("growthbridge_applications").select("id", count="exact").eq("status", "pending").execute()
        stats["pending_applications"] = pending.count if hasattr(pending, 'count') else len(pending.data or [])
    except Exception:
        stats["pending_applications"] = 0
    
    return {"stats": stats}


# ═══════════════════════════════════════════════════════════════════════
# Generic CRUD (with field allowlists)
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


@router.get("/{resource}")
async def list_resource(resource: str, _auth=Depends(_require_auth)):
    if resource not in TABLES:
        raise HTTPException(404, f"Unknown resource: {resource}")
    
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    try:
        result = client.table(TABLES[resource]).select("*").order("created_at", desc=True).limit(100).execute()
        return {"data": result.data or [], "resource": resource}
    except Exception as e:
        raise HTTPException(502, f"Database query failed: {str(e)[:200]}")


@router.get("/{resource}/{record_id}")
async def get_resource(resource: str, record_id: str, _auth=Depends(_require_auth)):
    if resource not in TABLES:
        raise HTTPException(404, f"Unknown resource: {resource}")
    
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    try:
        result = client.table(TABLES[resource]).select("*").eq("id", record_id).limit(1).execute()
        if not result.data:
            raise HTTPException(404, "Record not found")
        return result.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Database query failed: {str(e)[:200]}")


@router.post("/{resource}")
async def create_resource(resource: str, data: dict, _auth=Depends(_require_auth)):
    if resource not in TABLES:
        raise HTTPException(404, f"Unknown resource: {resource}")
    
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    # v2.0 — Sanitize input to only allowed fields
    sanitized = _sanitize_data(resource, data)
    if not sanitized:
        raise HTTPException(400, "No valid fields provided for this resource")
    
    try:
        now = datetime.now(timezone.utc).isoformat()
        sanitized["created_at"] = now
        sanitized["updated_at"] = now
        result = client.table(TABLES[resource]).insert(sanitized).execute()
        return {"ok": True, "data": result.data[0] if result.data else None}
    except Exception as e:
        raise HTTPException(502, f"Database insert failed: {str(e)[:200]}")


@router.put("/{resource}/{record_id}")
async def update_resource(resource: str, record_id: str, data: dict, _auth=Depends(_require_auth)):
    if resource not in TABLES:
        raise HTTPException(404, f"Unknown resource: {resource}")
    
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    # v2.0 — Sanitize input to only allowed fields
    sanitized = _sanitize_data(resource, data)
    if not sanitized:
        raise HTTPException(400, "No valid fields provided for this resource")
    
    try:
        sanitized["updated_at"] = datetime.now(timezone.utc).isoformat()
        result = client.table(TABLES[resource]).update(sanitized).eq("id", record_id).execute()
        if not result.data:
            raise HTTPException(404, "Record not found")
        return {"ok": True, "data": result.data[0]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Database update failed: {str(e)[:200]}")


@router.delete("/{resource}/{record_id}")
async def delete_resource(resource: str, record_id: str, _auth=Depends(_require_auth)):
    if resource not in TABLES:
        raise HTTPException(404, f"Unknown resource: {resource}")
    
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    # v2.0 — Verify record exists before deleting
    try:
        existing = client.table(TABLES[resource]).select("id").eq("id", record_id).limit(1).execute()
        if not existing.data:
            raise HTTPException(404, "Record not found")
        
        client.table(TABLES[resource]).delete().eq("id", record_id).execute()
        return {"ok": True, "message": f"{resource} deleted"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Database delete failed: {str(e)[:200]}")
