from fastapi import APIRouter, Depends, Request, HTTPException
from typing import Optional
from api.dependencies import *
from api.v1_models import *
from api.v1_helpers import *
from api.v1_stores import *

router = APIRouter()

# ═══════════════════════════════════════════════════════════════════════════
# BOT CONTROL
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/bot/status")
async def bot_status(container=Depends(get_container)):
    """Get publishing status for all brands with job statistics."""
    try:
        from scheduler.brand_scheduler import BrandSchedulerManager
        from config.settings import settings
        
        manager = await _async_wrap(container.resolve, BrandSchedulerManager)
        all_status = await _async_wrap(manager.get_all_status)
        
        # Try to get aggregate job stats
        job_stats = {}
        try:
            from services.job_service import JobService
            job_service = await _async_wrap(container.try_resolve, "JobService")
            if job_service:
                job_stats = await _async_wrap(job_service.get_today_stats)
        except Exception:
            pass
        
        return _ok({
            "brands": all_status,
            "brands_active": len(all_status),
            "services": {
                "gemini": bool(settings.gemini_key),
                "facebook": settings.is_configured,
                "guardian": settings.guardian_configured,
                "supabase": settings.supabase_enabled,
            },
            "today_stats": job_stats,
            "as_of": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/bot/status")


@router.post("/bot/post")
async def force_post(
    request: Request,
    body: dict = {},
    container=Depends(get_container),
    _auth=Depends(require_workspace_operator),
):
    """Force a post now for a brand owned by the authenticated workspace.
    
    Returns job_id for traceability (Architecture §29).
    """
    try:
        from repositories.brand_repository import BrandRepository
        from repositories.ai_profile_repository import AIProfileRepository
        
        brand_id = body.get("brand_id", "") if body else ""
        workspace_id = str(getattr(_auth, "workspace_id", "") or "").strip()

        if not workspace_id:
            return _err("Authenticated workspace context is required", 403)
        if not brand_id:
            return _err("brand_id is required", 400)

        brand_repo = await _async_wrap(container.resolve, BrandRepository)
        brand = await _async_wrap(brand_repo.find_by_id, brand_id)

        # Do not allow a workspace operator to trigger another tenant's brand.
        if not brand or str(getattr(brand, "workspace_id", "") or "") != workspace_id:
            return _err("Brand not found in the authenticated workspace", 404)
        
        ai_profile = None
        if getattr(brand, 'ai_profile_id', None):
            ai_repo = await _async_wrap(container.resolve, AIProfileRepository)
            ai_profile = await _async_wrap(ai_repo.find_by_id, brand.ai_profile_id)
        
        if not ai_profile:
            return _err(f"No AI profile for brand '{brand_id}'", 400)
        profile_workspace_id = getattr(ai_profile, "workspace_id", None)
        if profile_workspace_id and str(profile_workspace_id) != workspace_id:
            return _err("Brand AI profile is not owned by the authenticated workspace", 404)

        from scheduler.brand_scheduler import BrandSchedulerManager
        manager = await _async_wrap(container.resolve, BrandSchedulerManager)
        # Ensure the manager and all existing brand schedulers use the live,
        # container-owned JobService and Upstash client. Never create an
        # isolated scheduler that bypasses durable persistence or Redis locks.
        manager.set_container(container)
        idempotency_key = (
            request.headers.get("Idempotency-Key")
            or (body.get("idempotency_key") if isinstance(body, dict) else None)
        )
        result = await manager.trigger_for_brand(brand_id, idempotency_key=idempotency_key)

        if not result.get("ok"):
            return _err(result.get("error", "Manual publish job could not be durably queued"), 503)

        # Include job_id in response for traceability
        response = {
            "ok": result.get("ok", False),
            "brand_id": brand_id,
            "brand": result.get("brand", brand.name),
            "queued": result.get("queued", False),
            "published": result.get("published", False),
            "topic": result.get("topic", ""),
            "platforms": result.get("platforms", []),
            "duration_ms": result.get("duration_ms"),
            "job_id": result.get("job_id"),
        }
        response["status"] = result.get("status", "QUEUED")
        return _ok(response, 202)
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/bot/post")


@router.post("/bot/news")
async def force_news(
    body: dict = {},
    _auth: bool = Depends(require_auth),
):
    """Force a news post."""
    try:
        from services.news_scraper import news_scraper
        category = body.get("category") if body else None
        queued = await _async_wrap(news_scraper.post_news, category=category)
        return _ok({"queued": bool(queued)}, 202)
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/bot/news")


@router.post("/bot/engage")
async def force_engage(_auth: bool = Depends(require_workspace_operator)):
    """Force engagement run."""
    try:
        from services.social_monitor import social_monitor
        queued = await _async_wrap(social_monitor.run_engagement)
        return _ok({"queued": bool(queued)}, 202)
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/bot/engage")

