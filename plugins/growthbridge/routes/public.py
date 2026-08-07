"""
Growthbridge Public API — Read-only endpoints for the website.
No authentication required for GET endpoints.
POST endpoints for applications/registrations are open to public.
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional

router = APIRouter(prefix="/api/v1/growthbridge", tags=["growthbridge"])


def _get_supabase():
    """Get Supabase client from the plugin context."""
    try:
        from services.supabase import get_service_client_from_settings
        return get_service_client_from_settings()
    except Exception:
        return None


# ── Services ──────────────────────────────────────────────────────────────

@router.get("/services")
async def get_services(category: Optional[str] = None):
    """Get published services, optionally filtered by category."""
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    query = client.table("growthbridge_services").select("*").eq("is_published", True).order("sort_order")
    if category:
        query = query.eq("category", category)
    
    result = query.execute()
    return {"services": result.data or []}


@router.get("/services/{slug}")
async def get_service(slug: str):
    """Get a single service by slug."""
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    result = client.table("growthbridge_services").select("*").eq("slug", slug).eq("is_published", True).limit(1).execute()
    if not result.data:
        raise HTTPException(404, "Service not found")
    return result.data[0]


# ── Projects ──────────────────────────────────────────────────────────────

@router.get("/projects")
async def get_projects(featured: Optional[bool] = None):
    """Get published projects, optionally featured only."""
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    query = client.table("growthbridge_projects").select("*").eq("is_published", True).order("created_at", desc=True)
    if featured:
        query = query.eq("is_featured", True)
    
    result = query.execute()
    return {"projects": result.data or []}


@router.get("/projects/{slug}")
async def get_project(slug: str):
    """Get a single project by slug."""
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    result = client.table("growthbridge_projects").select("*").eq("slug", slug).eq("is_published", True).limit(1).execute()
    if not result.data:
        raise HTTPException(404, "Project not found")
    return result.data[0]


# ── Blog ──────────────────────────────────────────────────────────────────

@router.get("/blog")
async def get_blog_posts(page: int = Query(1, ge=1), limit: int = Query(10, le=50)):
    """Get published blog posts with pagination."""
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    offset = (page - 1) * limit
    result = client.table("growthbridge_blog").select("*").eq("is_published", True) \
        .order("published_at", desc=True).range(offset, offset + limit - 1).execute()
    
    return {"posts": result.data or [], "page": page, "limit": limit}


@router.get("/blog/{slug}")
async def get_blog_post(slug: str):
    """Get a single blog post by slug."""
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    result = client.table("growthbridge_blog").select("*").eq("slug", slug).eq("is_published", True).limit(1).execute()
    if not result.data:
        raise HTTPException(404, "Post not found")
    return result.data[0]


# ── Testimonials ──────────────────────────────────────────────────────────

@router.get("/testimonials")
async def get_testimonials():
    """Get published testimonials."""
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    result = client.table("growthbridge_testimonials").select("*").eq("is_published", True).order("created_at", desc=True).execute()
    return {"testimonials": result.data or []}


# ── Applications & Registrations (POST — public) ──────────────────────────

@router.post("/talent/apply")
async def apply_talent(data: dict):
    """Submit a talent application."""
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    required = ["name", "email"]
    for field in required:
        if not data.get(field):
            raise HTTPException(400, f"'{field}' is required")
    
    result = client.table("growthbridge_talent").insert({
        "name": data["name"],
        "email": data["email"],
        "bio": data.get("bio", ""),
        "skills": data.get("skills", []),
        "experience_level": data.get("experience_level", ""),
        "portfolio_url": data.get("portfolio_url", ""),
        "availability": data.get("availability", ""),
    }).execute()
    
    return {"ok": True, "message": "Application submitted successfully", "id": result.data[0]["id"] if result.data else None}


@router.post("/partners/apply")
async def apply_partner(data: dict):
    """Submit a partnership application."""
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    if not data.get("organization_name") or not data.get("email"):
        raise HTTPException(400, "'organization_name' and 'email' are required")
    
    result = client.table("growthbridge_partners").insert({
        "organization_name": data["organization_name"],
        "contact_person": data.get("contact_person", ""),
        "email": data["email"],
        "industry": data.get("industry", ""),
        "website": data.get("website", ""),
    }).execute()
    
    return {"ok": True, "message": "Partnership request submitted", "id": result.data[0]["id"] if result.data else None}


@router.post("/contact")
async def contact(data: dict):
    """Submit a general contact/application form."""
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    if not data.get("name") or not data.get("email") or not data.get("application_type"):
        raise HTTPException(400, "'name', 'email', and 'application_type' are required")
    
    result = client.table("growthbridge_applications").insert({
        "name": data["name"],
        "email": data["email"],
        "phone": data.get("phone", ""),
        "application_type": data["application_type"],
        "skills": data.get("skills", ""),
        "message": data.get("message", ""),
    }).execute()
    
    return {"ok": True, "message": "Your message has been received", "id": result.data[0]["id"] if result.data else None}
