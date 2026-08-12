"""
Growthbridge Public API — Read-only endpoints for the website.
No authentication required for GET endpoints.
POST endpoints for applications/registrations are open to public.

v2.0 — Added /team endpoints, /posts aliases, application aliases.
"""

from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Query
from typing import Optional

router = APIRouter(prefix="/api/v1/growthbridge", tags=["growthbridge"])

# ═══════════════════════════════════════════════════════════════════════
# Health Check
# ═══════════════════════════════════════════════════════════════════════

@router.get("/health")
async def health():
    return {"status": "ok", "service": "growthbridge", "timestamp": datetime.now(timezone.utc).isoformat()}


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
# Services
# ═══════════════════════════════════════════════════════════════════════

@router.get("/services")
async def get_services(category: Optional[str] = None, page: int = Query(1, ge=1), limit: int = Query(50, le=100)):
    client = _get_supabase()
    if not client: raise HTTPException(503, "Database unavailable")
    try:
        query = client.table("growthbridge_services").select("*").eq("is_published", True).order("sort_order")
        if category: query = query.eq("category", category)
        offset = (page - 1) * limit
        result = query.range(offset, offset + limit - 1).execute()
        return {"services": result.data or [], "page": page, "limit": limit}
    except Exception as e:
        raise HTTPException(502, f"Database query failed: {str(e)[:200]}")

@router.get("/services/{slug}")
async def get_service(slug: str):
    client = _get_supabase()
    if not client: raise HTTPException(503, "Database unavailable")
    try:
        result = client.table("growthbridge_services").select("*").eq("slug", slug).eq("is_published", True).limit(1).execute()
        if not result.data: raise HTTPException(404, "Service not found")
        return result.data[0]
    except HTTPException: raise
    except Exception as e: raise HTTPException(502, f"Database query failed: {str(e)[:200]}")


# ═══════════════════════════════════════════════════════════════════════
# Projects
# ═══════════════════════════════════════════════════════════════════════

@router.get("/projects")
async def get_projects(featured: Optional[bool] = None, page: int = Query(1, ge=1), limit: int = Query(50, le=100)):
    client = _get_supabase()
    if not client: raise HTTPException(503, "Database unavailable")
    try:
        query = client.table("growthbridge_projects").select("*").eq("is_published", True).order("created_at", desc=True)
        if featured: query = query.eq("is_featured", True)
        offset = (page - 1) * limit
        result = query.range(offset, offset + limit - 1).execute()
        return {"projects": result.data or [], "page": page, "limit": limit}
    except Exception as e:
        raise HTTPException(502, f"Database query failed: {str(e)[:200]}")

@router.get("/projects/{slug}")
async def get_project(slug: str):
    client = _get_supabase()
    if not client: raise HTTPException(503, "Database unavailable")
    try:
        result = client.table("growthbridge_projects").select("*").eq("slug", slug).eq("is_published", True).limit(1).execute()
        if not result.data: raise HTTPException(404, "Project not found")
        return result.data[0]
    except HTTPException: raise
    except Exception as e: raise HTTPException(502, f"Database query failed: {str(e)[:200]}")


# ═══════════════════════════════════════════════════════════════════════
# Blog
# ═══════════════════════════════════════════════════════════════════════

@router.get("/blog")
async def get_blog_posts(page: int = Query(1, ge=1), limit: int = Query(10, le=50), tag: Optional[str] = None):
    client = _get_supabase()
    if not client: raise HTTPException(503, "Database unavailable")
    try:
        query = client.table("growthbridge_blog").select("id, slug, title, excerpt, featured_image, author, tags, is_published, published_at, created_at").eq("is_published", True).order("published_at", desc=True)
        if tag: query = query.contains("tags", [tag])
        offset = (page - 1) * limit
        result = query.range(offset, offset + limit - 1).execute()
        count_result = client.table("growthbridge_blog").select("id", count="exact").eq("is_published", True).execute()
        total = count_result.count if hasattr(count_result, 'count') else len(result.data or [])
        return {"posts": result.data or [], "page": page, "limit": limit, "total": total, "total_pages": max(1, (total + limit - 1) // limit) if total else 1}
    except Exception as e:
        raise HTTPException(502, f"Database query failed: {str(e)[:200]}")

@router.get("/blog/{slug}")
async def get_blog_post(slug: str):
    client = _get_supabase()
    if not client: raise HTTPException(503, "Database unavailable")
    try:
        result = client.table("growthbridge_blog").select("*").eq("slug", slug).eq("is_published", True).limit(1).execute()
        if not result.data: raise HTTPException(404, "Post not found")
        return result.data[0]
    except HTTPException: raise
    except Exception as e: raise HTTPException(502, f"Database query failed: {str(e)[:200]}")


# ── Blog Aliases (/posts) ────────────────────────────────────────────

@router.get("/posts")
async def get_posts(page: int = Query(1, ge=1), limit: int = Query(10, le=50), tag: Optional[str] = None):
    return await get_blog_posts(page=page, limit=limit, tag=tag)

@router.get("/posts/{slug}")
async def get_post(slug: str):
    return await get_blog_post(slug=slug)


# ═══════════════════════════════════════════════════════════════════════
# Team / People
# ═══════════════════════════════════════════════════════════════════════

@router.get("/team")
async def get_team(category: Optional[str] = None, active: Optional[str] = None, featured: Optional[str] = None, q: Optional[str] = None):
    client = _get_supabase()
    if not client: raise HTTPException(503, "Database unavailable")
    try:
        query = client.table("growthbridge_talent").select("*")
        if active == "1": query = query.eq("is_active", True)
        if featured == "1": query = query.eq("is_featured", True)
        if category: query = query.eq("category", category)
        if q: query = query.or_(f"name.ilike.%{q}%,bio.ilike.%{q}%,skills.cs.%7B{q}%7D")
        result = query.order("display_order").limit(100).execute()
        return result.data or []
    except Exception as e:
        raise HTTPException(502, f"Database query failed: {str(e)[:200]}")

@router.get("/team/{slug}")
async def get_team_member(slug: str):
    client = _get_supabase()
    if not client: raise HTTPException(503, "Database unavailable")
    try:
        result = client.table("growthbridge_talent").select("*").eq("slug", slug).limit(1).execute()
        if not result.data: raise HTTPException(404, "Team member not found")
        return result.data[0]
    except HTTPException: raise
    except Exception as e: raise HTTPException(502, f"Database query failed: {str(e)[:200]}")

@router.get("/team/id/{id}")
async def get_team_member_by_id(id: str):
    client = _get_supabase()
    if not client: raise HTTPException(503, "Database unavailable")
    try:
        result = client.table("growthbridge_talent").select("*").eq("id", id).limit(1).execute()
        if not result.data: raise HTTPException(404, "Team member not found")
        return result.data[0]
    except HTTPException: raise
    except Exception as e: raise HTTPException(502, f"Database query failed: {str(e)[:200]}")

@router.post("/team")
async def create_team_member(data: dict):
    client = _get_supabase()
    if not client: raise HTTPException(503, "Database unavailable")
    try:
        now = datetime.now(timezone.utc).isoformat()
        data.setdefault("created_at", now)
        data.setdefault("updated_at", now)
        result = client.table("growthbridge_talent").insert(data).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        raise HTTPException(502, f"Failed to create team member: {str(e)[:200]}")

@router.put("/team/id/{id}")
async def update_team_member(id: str, data: dict):
    client = _get_supabase()
    if not client: raise HTTPException(503, "Database unavailable")
    try:
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        result = client.table("growthbridge_talent").update(data).eq("id", id).execute()
        if not result.data: raise HTTPException(404, "Team member not found")
        return result.data[0]
    except HTTPException: raise
    except Exception as e: raise HTTPException(502, f"Failed to update team member: {str(e)[:200]}")

@router.delete("/team/id/{id}")
async def delete_team_member(id: str):
    client = _get_supabase()
    if not client: raise HTTPException(503, "Database unavailable")
    try:
        client.table("growthbridge_talent").delete().eq("id", id).execute()
        return {"ok": True, "message": "Team member deleted"}
    except Exception as e:
        raise HTTPException(502, f"Failed to delete team member: {str(e)[:200]}")


# ═══════════════════════════════════════════════════════════════════════
# Testimonials
# ═══════════════════════════════════════════════════════════════════════

@router.get("/testimonials")
async def get_testimonials(limit: int = Query(20, le=50)):
    client = _get_supabase()
    if not client: raise HTTPException(503, "Database unavailable")
    try:
        result = client.table("growthbridge_testimonials").select("*").eq("is_published", True).order("created_at", desc=True).limit(limit).execute()
        return {"testimonials": result.data or []}
    except Exception as e:
        raise HTTPException(502, f"Database query failed: {str(e)[:200]}")


# ═══════════════════════════════════════════════════════════════════════
# Public Stats
# ═══════════════════════════════════════════════════════════════════════

@router.get("/stats")
async def get_public_stats():
    client = _get_supabase()
    if not client: raise HTTPException(503, "Database unavailable")
    stats = {}
    tables = {"services": "growthbridge_services", "projects": "growthbridge_projects", "talent": "growthbridge_talent", "applications": "growthbridge_applications", "partners": "growthbridge_partners", "blog": "growthbridge_blog"}
    for key, table in tables.items():
        try:
            result = client.table(table).select("id", count="exact").execute()
            stats[key] = result.count if hasattr(result, 'count') else len(result.data or [])
        except Exception:
            stats[key] = 0
    return {"stats": stats}


# ═══════════════════════════════════════════════════════════════════════
# Forms (POST — public)
# ═══════════════════════════════════════════════════════════════════════

@router.post("/contact")
async def contact(data: dict):
    client = _get_supabase()
    if not client: raise HTTPException(503, "Database unavailable")
    if not data.get("name") or not data.get("email") or not data.get("application_type"):
        raise HTTPException(400, "'name', 'email', and 'application_type' are required")
    try:
        now = datetime.now(timezone.utc).isoformat()
        result = client.table("growthbridge_applications").insert({"name": data["name"], "email": data["email"], "phone": data.get("phone", ""), "application_type": data["application_type"], "skills": data.get("skills", ""), "message": data.get("message", ""), "status": "pending", "created_at": now, "updated_at": now}).execute()
        return {"ok": True, "message": "Your message has been received", "id": result.data[0]["id"] if result.data else None}
    except Exception as e:
        raise HTTPException(502, f"Failed to submit contact form: {str(e)[:200]}")

@router.post("/talent/apply")
async def apply_talent(data: dict):
    client = _get_supabase()
    if not client: raise HTTPException(503, "Database unavailable")
    for field in ["name", "email"]:
        if not data.get(field): raise HTTPException(400, f"'{field}' is required")
    try:
        now = datetime.now(timezone.utc).isoformat()
        result = client.table("growthbridge_talent").insert({"name": data["name"], "email": data["email"], "bio": data.get("bio", ""), "skills": data.get("skills", []), "experience_level": data.get("experience_level", ""), "portfolio_url": data.get("portfolio_url", ""), "availability": data.get("availability", ""), "status": "pending", "created_at": now, "updated_at": now}).execute()
        return {"ok": True, "message": "Application submitted successfully", "id": result.data[0]["id"] if result.data else None}
    except Exception as e:
        raise HTTPException(502, f"Failed to submit application: {str(e)[:200]}")

@router.post("/partners/apply")
async def apply_partner(data: dict):
    client = _get_supabase()
    if not client: raise HTTPException(503, "Database unavailable")
    if not data.get("organization_name") or not data.get("email"):
        raise HTTPException(400, "'organization_name' and 'email' are required")
    try:
        now = datetime.now(timezone.utc).isoformat()
        result = client.table("growthbridge_partners").insert({"organization_name": data["organization_name"], "contact_person": data.get("contact_person", ""), "email": data["email"], "industry": data.get("industry", ""), "website": data.get("website", ""), "status": "pending", "created_at": now, "updated_at": now}).execute()
        return {"ok": True, "message": "Partnership request submitted", "id": result.data[0]["id"] if result.data else None}
    except Exception as e:
        raise HTTPException(502, f"Failed to submit partnership request: {str(e)[:200]}")


# ── Form Aliases (provider expects these paths) ──────────────────────

@router.post("/applications")
async def submit_application(data: dict):
    return await apply_talent(data)

@router.post("/partnerships")
async def submit_partnership(data: dict):
    return await apply_partner(data)
