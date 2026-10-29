from fastapi import APIRouter, Depends, Request, HTTPException
from typing import Optional
from api.dependencies import *
from api.v1_models import *
from api.v1_helpers import *
from api.v1_stores import *

router = APIRouter()

# ═══════════════════════════════════════════════════════════════════════════
# AI CONFIG & PERSONA
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/ai/config")
async def get_ai_config(_auth: bool = Depends(require_auth)):
    """Get AI configuration."""
    try:
        from config.settings import settings
        from api.ai_runtime import get as _rt_get, VALID_MOODS
        rt = await _async_wrap(_rt_get)
        return _ok({
            "provider": settings.ai_provider,
            "model": settings.gemini_model,
            "chat_temperature": settings.gemini_chat_temperature,
            "post_temperature": settings.gemini_post_temperature,
            "safety_level": settings.gemini_safety_level,
            "available_models": [
                "gemini-2.5-flash", "gemini-2.5-pro",
                "gemini-1.5-pro", "gemini-1.5-flash",
            ],
            "available_providers": ["gemini", "huggingface", "cloudflare"],
            "system_prompt_override": rt["system_prompt_override"],
            "persona_mood": rt["persona_mood"],
            "tone_assertiveness": rt["tone_assertiveness"],
            "tone_humor": rt["tone_humor"],
            "tone_formality": rt["tone_formality"],
            "available_moods": sorted(VALID_MOODS),
        })
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/ai/config")


@router.post("/ai/interpret")
async def ai_interpret(
    body: AIInterpretRequest,
    _auth: bool = Depends(require_auth),
):
    """Run KDO AI interpretation through the provider port.

    The endpoint returns AIReceipt data only. It cannot create or execute an
    ActionIntent; application/domain code owns interpretation-to-intent
    translation and governance remains upstream of the Action Kernel.
    """
    try:
        import os
        from config.settings import settings
        from services.connectors.cloudflare_ai_adapter import CloudflareAIAdapter
        from services.ai_policy import select_ai_route
        from services.ai_provider import AIReceipt

        account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip() or getattr(settings, "cloudflare_account_id", "").strip()
        api_token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip() or getattr(settings, "cloudflare_api_token", "").strip()
        if not account_id or not api_token:
            return _ok({"status": "unavailable", "provider": "cloudflare_ai", "error": "Cloudflare AI is not configured"}, 503)

        route = select_ai_route(body.task)
        adapter = CloudflareAIAdapter(account_id=account_id, token=api_token, model=body.model or route.model)
        try:
            cid = correlation_id_var.get() or _uuid_mod.uuid4().hex
            receipt = await _async_wrap(
                adapter.analyze,
                user_prompt=body.message,
                system_prompt=body.system_prompt,
                model=body.model or route.model,
                correlation_id=cid,
                structured=body.structured,
            )
        finally:
            adapter.close()

        if not isinstance(receipt, AIReceipt):
            return _safe_err("AI provider returned an invalid receipt", "POST /api/v1/ai/interpret", 502)
        return _ok({
            "ai_receipt": receipt.to_dict(),
            "authority": "interpretation_only",
            "action_intent_created": False,
            "provider_policy": {"task": body.task, "provider": route.provider, "model": body.model or route.model},
        }, 200 if receipt.status == "succeeded" else 503)
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/ai/interpret")


@router.post("/image/generate")
async def generate_image_endpoint(
    body: ImageGenerateRequest,
    _auth: bool = Depends(require_auth),
):
    """Generate high-quality AI image on demand using Cloudflare Workers AI free models."""
    try:
        from services.cloudflare_image import generate_trend_image
        from service_container import get_container
        from services.supabase import SupabaseService

        container = get_container()
        supabase_svc = container.try_resolve(SupabaseService) if container else None
        supabase_client = supabase_svc.client if supabase_svc else None

        trend_payload = {
            "title": body.topic,
            "content": body.content or "",
            "author": body.author or "",
            "platform": body.platform or "facebook",
        }

        image_url = await _async_wrap(generate_trend_image, trend_payload, supabase_client=supabase_client)
        if not image_url:
            return _ok({
                "success": False,
                "image_url": None,
                "message": "Image generation failed or Cloudflare Workers AI credentials unconfigured. Text-only fallback active.",
            })

        return _ok({
            "success": True,
            "image_url": image_url,
            "topic": body.topic,
        })
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/image/generate")


@router.get("/image/status")
async def get_image_status(_auth: bool = Depends(require_auth)):
    """Check Cloudflare Workers AI configuration and available free models."""
    try:
        import os
        from services.cloudflare_image import MODELS

        account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
        api_token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
        if not account_id or not api_token:
            try:
                from config.settings import settings
                account_id = account_id or getattr(settings, "cloudflare_account_id", "").strip()
                api_token = api_token or getattr(settings, "cloudflare_api_token", "").strip()
            except Exception:
                pass

        is_configured = bool(account_id and api_token)

        return _ok({
            "configured": is_configured,
            "provider": "Cloudflare Workers AI",
            "tier": "Free Tier",
            "models": MODELS,
            "primary_model": MODELS.get("flux_schnell"),
            "fallback_model": MODELS.get("sdxl_base"),
        })
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/image/status")


@router.put("/ai/config")
async def update_ai_config(
    body: AIConfigUpdateRequest,
    _auth: bool = Depends(require_auth),
):
    """Update AI configuration."""
    try:
        from config.settings import settings
        from api.ai_runtime import update as _rt_update, get as _rt_get

        data = body.model_dump(exclude_none=True)

        if "provider" in data:
            settings.ai_provider = data["provider"]
        if "model" in data:
            settings.gemini_model = str(data["model"])

        for fk, sk in [
            ("chat_temperature", "gemini_chat_temperature"),
            ("post_temperature", "gemini_post_temperature"),
        ]:
            if fk in data:
                setattr(settings, sk, float(data[fk]))

        if "safety_level" in data:
            settings.gemini_safety_level = data["safety_level"]

        runtime_keys = {
            "system_prompt_override", "persona_mood",
            "tone_assertiveness", "tone_humor", "tone_formality",
        }
        rt_updates = {k: v for k, v in data.items() if k in runtime_keys}
        if rt_updates:
            await _async_wrap(_rt_update, **rt_updates)

        rt = await _async_wrap(_rt_get)
        return _ok({
            "updated": True,
            "config": {"provider": settings.ai_provider, "model": settings.gemini_model, **rt},
        })
    except Exception as exc:
        return _safe_err(exc, "PUT /api/v1/ai/config")


@router.get("/ai/persona")
async def get_ai_persona(_auth: bool = Depends(require_auth)):
    """Get AI persona configuration."""
    try:
        from api.ai_runtime import get as _rt_get
        from config.settings import settings
        rt = await _async_wrap(_rt_get)
        return _ok({
            "tone": int(rt.get("tone_formality", 0.6) * 100),
            "aggression": int(rt.get("tone_assertiveness", 0.7) * 100),
            "humor": int(rt.get("tone_humor", 0.2) * 100),
            "model": getattr(settings, "gemini_model", "gemini-2.5-flash"),
            "system_prompt": rt.get("system_prompt_override") or "",
            **rt,
        })
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/ai/persona")


@router.put("/ai/persona")
async def update_ai_persona(
    body: dict,
    _auth: bool = Depends(require_auth),
):
    """Update AI persona."""
    try:
        from api.ai_runtime import update as _rt_update, get as _rt_get
        from config.settings import settings

        rt_updates = {}
        if "tone" in body:
            rt_updates["tone_formality"] = round(float(body["tone"]) / 100.0, 4)
        if "aggression" in body:
            rt_updates["tone_assertiveness"] = round(float(body["aggression"]) / 100.0, 4)
        if "humor" in body:
            rt_updates["tone_humor"] = round(float(body["humor"]) / 100.0, 4)
        if "model" in body:
            settings.gemini_model = str(body["model"])
        if "system_prompt" in body:
            rt_updates["system_prompt_override"] = body["system_prompt"] or None

        for k in ("persona_mood", "tone_formality", "tone_assertiveness", "tone_humor"):
            if k in body and k not in rt_updates:
                rt_updates[k] = body[k]

        rt = (
            await _async_wrap(_rt_update, **rt_updates)
            if rt_updates
            else await _async_wrap(_rt_get)
        )
        return _ok({"updated": True, "persona": rt})
    except ValueError as exc:
        return _err(str(exc), 400)
    except Exception as exc:
        return _safe_err(exc, "PUT /api/v1/ai/persona")


@router.get("/persona")
async def get_persona(_auth: bool = Depends(require_auth)):
    """Get bot persona for authenticated callers."""
    try:
        from core.persona import bot_persona
        from api.ai_runtime import get as _rt_get, VALID_MOODS
        rt = await _async_wrap(_rt_get)
        state = await _async_wrap(bot_persona.to_dict)
        return _ok({
            "name": bot_persona.name,
            "tagline": bot_persona.tagline,
            "current_mood": state.get("current_mood", "professional"),
            "available_moods": sorted(VALID_MOODS),
            **rt,
        })
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/persona")


@router.put("/persona")
async def update_persona(
    body: dict,
    _auth: bool = Depends(require_auth),
):
    """Update persona settings."""
    try:
        from api.ai_runtime import update as _rt_update
        allowed = {
            "persona_mood", "tone_assertiveness", "tone_humor",
            "tone_formality", "system_prompt_override",
        }
        unknown = set(body.keys()) - allowed
        if unknown:
            return _err(f"Unknown keys: {sorted(unknown)}", 400)
        rt = await _async_wrap(
            _rt_update,
            **{k: v for k, v in body.items() if k in allowed},
        )
        return _ok({"updated": True, "persona": rt})
    except ValueError as exc:
        return _err(str(exc), 400)
    except Exception as exc:
        return _safe_err(exc, "PUT /api/v1/persona")


@router.post("/persona/reset")
async def reset_persona(_auth: bool = Depends(require_workspace_operator)):
    """Reset persona to defaults."""
    try:
        from api.ai_runtime import update as _rt_update
        rt = await _async_wrap(
            _rt_update,
            persona_mood="professional",
            tone_assertiveness=0.7,
            tone_humor=0.2,
            tone_formality=0.6,
            system_prompt_override=None,
        )
        return _ok({"reset": True, "persona": rt})
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/persona/reset")

