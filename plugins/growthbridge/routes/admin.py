"""
Growthbridge Public API — Read-only endpoints for the website.
No authentication required for GET endpoints.
POST endpoints for applications/registrations are open to public.

Performance:
  - Shared Supabase client (created once)
  - All endpoints wrapped in error handling
  - Health endpoint for monitoring (no DB call)
  - Support for field selection to reduce payload size
"""

from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Query
from typing import Optional, List

router = APIRouter(prefix="/api/v1/growthbridge", tags=["growthbridge"])

# ═══════════════════════════════════════════════════════════════════════
# Health Check — no database, instant response
# ═══════════════════════════════════════════════════════════════════════

@router.get("/health")
async def health():
    """Lightweight health check for monitoring. No Supabase call."""
    return {
        "status": "ok",
        "service": "growthbridge",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ═══════════════════════════════════════════════════════════════════════
# Shared Supabase client — created once, reused across all requests
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
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def _select_fields(query, table: str, fields: Optional[str] = None):
    """Build select clause — full select or specific fields."""
    if fields:
        # Validate fields exist to prevent errors
        valid_fields = [f.strip() for f in fields.split(",") if f.strip()]
        if valid_fields:
            return query.select(", ".join(valid_fields))
    return query.select("*")


def _paginate(query, page: int = 1, limit: int = 10):
    """Apply pagination to a query."""
    offset = (page - 1) * limit
    return query.range(offset, offset + limit - 1)


# ── Services ──────────────────────────────────────────────────────────────

@router.get("/services")
async def get_services(
    category: Optional[str] = None,
    fields: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, le=100),
):
    """Get published services with optional category filter and pagination."""
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    try:
        query = client.table("growthbridge_services") \
            .eq("is_published", True) \
            .order("sort_order")
        
        query = _select_fields(query, "growthbridge_services", fields)
        
        if category:
            query = query.eq("category", category)
        
        query = _paginate(query, page, limit)
        result = query.execute()
        
        return {
            "services": result.data or [],
            "page": page,
            "limit": limit,
        }
    except Exception as e:
        raise HTTPException(502, f"Database query failed: {str(e)[:200]}")


@router.get("/services/{slug}")
async def get_service(slug: str):
    """Get a single service by slug."""
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    try:
        result = client.table("growthbridge_services") \
            .select("*") \
            .eq("slug", slug) \
            .eq("is_published", True) \
            .limit(1) \
            .execute()
        
        if not result.data:
            raise HTTPException(404, "Service not found")
        return result.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Database query failed: {str(e)[:200]}")


# ── Projects ──────────────────────────────────────────────────────────────

@router.get("/projects")
async def get_projects(
    featured: Optional[bool] = None,
    fields: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, le=100),
):
    """Get published projects with optional featured filter and pagination."""
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    try:
        query = client.table("growthbridge_projects") \
            .eq("is_published", True) \
            .order("created_at", desc=True)
        
        query = _select_fields(query, "growthbridge_projects", fields)
        
        if featured:
            query = query.eq("is_featured", True)
        
        query = _paginate(query, page, limit)
        result = query.execute()
        
        return {
            "projects": result.data or [],
            "page": page,
            "limit": limit,
        }
    except Exception as e:
        raise HTTPException(502, f"Database query failed: {str(e)[:200]}")


@router.get("/projects/{slug}")
async def get_project(slug: str):
    """Get a single project by slug."""
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    try:
        result = client.table("growthbridge_projects") \
            .select("*") \
            .eq("slug", slug) \
            .eq("is_published", True) \
            .limit(1) \
            .execute()
        
        if not result.data:
            raise HTTPException(404, "Project not found")
        return result.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Database query failed: {str(e)[:200]}")


# ── Blog ──────────────────────────────────────────────────────────────────

@router.get("/blog")
async def get_blog_posts(
    page: int = Query(1, ge=1),
    limit: int = Query(10, le=50),
    tag: Optional[str] = None,
    fields: Optional[str] = None,
):
    """Get published blog posts with pagination and optional tag filter."""
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    try:
        query = client.table("growthbridge_blog") \
            .eq("is_published", True) \
            .order("published_at", desc=True)
        
        # For blog listing, use lighter fields by default
        if fields:
            query = _select_fields(query, "growthbridge_blog", fields)
        else:
            # Default: exclude heavy content field from listing
            query = query.select("id, slug, title, excerpt, featured_image, author, tags, is_published, published_at, created_at")
        
        if tag:
            # Filter by tag (csontains in JSON array)
            query = query.contains("tags", [tag])
        
        query = _paginate(query, page, limit)
        result = query.execute()
        
        # Get total count for pagination info
        count_query = client.table("growthbridge_blog") \
            .select("id", count="exact") \
            .eq("is_published", True)
        if tag:
            count_query = count_query.contains("tags", [tag])
        count_result = count_query.execute()
        total = count_result.count if hasattr(count_result, 'count') else len(result.data or [])
        
        return {
            "posts": result.data or [],
            "page": page,
            "limit": limit,
            "total": total,
            "total_pages": max(1, (total + limit - 1) // limit) if total else 1,
        }
    except Exception as e:
        raise HTTPException(502, f"Database query failed: {str(e)[:200]}")


@router.get("/blog/{slug}")
async def get_blog_post(slug: str):
    """Get a single blog post by slug — includes full content."""
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    try:
        result = client.table("growthbridge_blog") \
            .select("*") \
            .eq("slug", slug) \
            .eq("is_published", True) \
            .limit(1) \
            .execute()
        
        if not result.data:
            raise HTTPException(404, "Post not found")
        return result.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Database query failed: {str(e)[:200]}")


# ── Testimonials ──────────────────────────────────────────────────────────

@router.get("/testimonials")
async def get_testimonials(
    limit: int = Query(20, le=50),
):
    """Get published testimonials."""
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    try:
        result = client.table("growthbridge_testimonials") \
            .select("*") \
            .eq("is_published", True) \
            .order("created_at", desc=True) \
            .limit(limit) \
            .execute()
        
        return {"testimonials": result.data or []}
    except Exception as e:
        raise HTTPException(502, f"Database query failed: {str(e)[:200]}")


# ── Combined Endpoint (for homepage) ─────────────────────────────────────

@router.get("/homepage")
async def get_homepage_data():
    """
    Single endpoint for homepage data.
    Returns featured projects, latest blog posts, and testimonials.
    One request instead of three.
    """
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    result = {
        "featured_projects": [],
        "latest_posts": [],
        "testimonials": [],
    }
    
    # Featured projects
    try:
        projects = client.table("growthbridge_projects") \
            .select("id, slug, title, description, image_url, category") \
            .eq("is_published", True) \
            .eq("is_featured", True) \
            .order("created_at", desc=True) \
            .limit(6) \
            .execute()
        result["featured_projects"] = projects.data or []
    except Exception:
        pass
    
    # Latest blog posts (exclude content)
    try:
        posts = client.table("growthbridge_blog") \
            .select("id, slug, title, excerpt, featured_image, author, published_at") \
            .eq("is_published", True) \
            .order("published_at", desc=True) \
            .limit(3) \
            .execute()
        result["latest_posts"] = posts.data or []
    except Exception:
        pass
    
    # Testimonials
    try:
        testimonials = client.table("growthbridge_testimonials") \
            .select("*") \
            .eq("is_published", True) \
            .order("created_at", desc=True) \
            .limit(10) \
            .execute()
        result["testimonials"] = testimonials.data or []
    except Exception:
        pass
    
    return result


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
    
    try:
        now = datetime.now(timezone.utc).isoformat()
        result = client.table("growthbridge_talent").insert({
            "name": data["name"],
            "email": data["email"],
            "bio": data.get("bio", ""),
            "skills": data.get("skills", []),
            "experience_level": data.get("experience_level", ""),
            "portfolio_url": data.get("portfolio_url", ""),
            "availability": data.get("availability", ""),
            "status": "pending",
            "created_at": now,
            "updated_at": now,
        }).execute()
        
        return {
            "ok": True,
            "message": "Application submitted successfully",
            "id": result.data[0]["id"] if result.data else None,
        }
    except Exception as e:
        raise HTTPException(502, f"Failed to submit application: {str(e)[:200]}")


@router.post("/partners/apply")
async def apply_partner(data: dict):
    """Submit a partnership application."""
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    if not data.get("organization_name") or not data.get("email"):
        raise HTTPException(400, "'organization_name' and 'email' are required")
    
    try:
        now = datetime.now(timezone.utc).isoformat()
        result = client.table("growthbridge_partners").insert({
            "organization_name": data["organization_name"],
            "contact_person": data.get("contact_person", ""),
            "email": data["email"],
            "industry": data.get("industry", ""),
            "website": data.get("website", ""),
            "status": "pending",
            "created_at": now,
            "updated_at": now,
        }).execute()
        
        return {
            "ok": True,
            "message": "Partnership request submitted",
            "id": result.data[0]["id"] if result.data else None,
        }
    except Exception as e:
        raise HTTPException(502, f"Failed to submit partnership request: {str(e)[:200]}")


@router.post("/contact")
async def contact(data: dict):
    """Submit a general contact/application form."""
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    if not data.get("name") or not data.get("email") or not data.get("application_type"):
        raise HTTPException(400, "'name', 'email', and 'application_type' are required")
    
    try:
        now = datetime.now(timezone.utc).isoformat()
        result = client.table("growthbridge_applications").insert({
            "name": data["name"],
            "email": data["email"],
            "phone": data.get("phone", ""),
            "application_type": data["application_type"],
            "skills": data.get("skills", ""),
            "message": data.get("message", ""),
            "status": "pending",
            "created_at": now,
            "updated_at": now,
        }).execute()
        
        return {
            "ok": True,
            "message": "Your message has been received",
            "id": result.data[0]["id"] if result.data else None,
        }
    except Exception as e:
        raise HTTPException(502, f"Failed to submit contact form: {str(e)[:200]}")
