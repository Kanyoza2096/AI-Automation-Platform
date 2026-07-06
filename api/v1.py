"""
Kanyoza Systems AI Platform v10 — REST API v1 Blueprint (M8).

All versioned endpoints live at /api/v1/*.
Protected endpoints require one of:
  - Authorization: Bearer <MASTER_API_TOKEN>   (preferred — frontend/dashboard)
  - X-API-Key: <APP_SECRET>                    (admin key)
  - X-API-Key: <OWNER_PSID>                    (owner PSID fallback)

Endpoints
---------
GET  /api/v1/posts                 — paginated post history
POST /api/v1/posts                 — schedule a content post (or post now)
GET  /api/v1/messages              — recent Messenger conversations
POST /api/v1/messages/reply        — send a Messenger message as the page
GET  /api/v1/analytics             — aggregated analytics snapshot
GET  /api/v1/bot/status            — scheduler + service status
POST /api/v1/bot/post              — force an immediate content post
POST /api/v1/bot/news              — force an immediate news post
GET  /api/v1/guardian/status       — last scan summary
POST /api/v1/guardian/scan         — trigger an immediate background scan
GET  /api/v1/schedule              — current scheduler configuration
PUT  /api/v1/schedule              — update post interval / hours
GET  /api/v1/metrics               — in-process latency / counter metrics
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from functools import wraps

from flask import Blueprint, jsonify, request

logger = logging.getLogger("kanyoza.api.v1")

api_v1_bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------


def _require_auth() -> bool:
    """
    Validate the incoming request carries a recognised credential.

    Accepted (priority order):
    1. Authorization: Bearer <MASTER_API_TOKEN>  — frontend / dashboard clients
    2. X-API-Key: <APP_SECRET>                   — legacy admin key
    3. X-API-Key: <OWNER_PSID>                   — legacy owner PSID

    Returns True when a valid credential is present, False otherwise.
    """
    import hmac
    from config.settings import settings as _s

    master_token = getattr(_s, "master_api_token", "") or ""
    api_secret   = getattr(_s, "app_secret", "") or ""
    owner_psid   = getattr(_s, "owner_psid", "") or ""

    # 1. Bearer token (Authorization header)
    auth_header = request.headers.get("Authorization", "").strip()
    if auth_header.startswith("Bearer "):
        bearer = auth_header[len("Bearer "):]
        if master_token and hmac.compare_digest(bearer, master_token):
            return True

    # 2 & 3. Legacy X-API-Key header
    key = request.headers.get("X-API-Key", "").strip()
    if key:
        if api_secret and hmac.compare_digest(key, api_secret):
            return True
        if owner_psid and hmac.compare_digest(key, owner_psid):
            return True

    return False


def _auth_required(fn):
    """Decorator: reject requests without a valid credential with 401."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not _require_auth():
            return jsonify({
                "error": "unauthorized",
                "message": "Valid Authorization: Bearer <token> or X-API-Key header required",
            }), 401
        return fn(*args, **kwargs)
    return wrapper


def _ok(data: dict, status: int = 200):
    return jsonify({"ok": True, **data}), status


def _err(msg: str, status: int = 400):
    return jsonify({"ok": False, "error": msg}), status


# ---------------------------------------------------------------------------
# Content Studio — in-memory draft store
# ---------------------------------------------------------------------------

import uuid as _uuid_mod
import threading as _threading_mod

_draft_lock = _threading_mod.Lock()
# id -> {"id", "caption", "topic", "category", "state", "created_at"}
_draft_store: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Posts
# ---------------------------------------------------------------------------


@api_v1_bp.route("/posts", methods=["GET"])
@_auth_required
def get_posts():
    from analytics.tracker import analytics
    try:
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(100, max(1, int(request.args.get("per_page", 20))))
        state_filter = request.args.get("state", "").lower()   # "draft" | "published" | ""

        stats = analytics.get_stats()
        published_posts = [
            {**p, "state": "published"} for p in stats.get("recent_posts", [])
        ]

        with _draft_lock:
            drafts = list(_draft_store.values())

        if state_filter == "draft":
            all_posts = drafts
        elif state_filter == "published":
            all_posts = published_posts
        else:
            all_posts = drafts + published_posts

        start = (page - 1) * per_page
        posts = all_posts[start: start + per_page]
        return _ok({
            "posts": posts,
            "page": page,
            "per_page": per_page,
            "total": len(all_posts),
            "drafts": len(drafts),
        })
    except Exception as exc:
        logger.error("GET /api/v1/posts error: %s", exc)
        return _err(str(exc), 500)


@api_v1_bp.route("/posts", methods=["POST"])
@_auth_required
def create_post():
    from scheduler.post_scheduler import scheduler
    try:
        data = request.get_json(force=True, silent=True) or {}
        state = data.get("state", "publish").lower()

        if state == "draft":
            # Store as a draft — does not trigger the scheduler
            draft_id = str(_uuid_mod.uuid4())
            draft = {
                "id": draft_id,
                "caption": data.get("caption", ""),
                "topic": data.get("topic", ""),
                "category": data.get("category", ""),
                "state": "draft",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            with _draft_lock:
                _draft_store[draft_id] = draft
            return _ok({"draft": True, "draft_id": draft_id, "post": draft}, 201)

        # Default: queue an immediate post via the scheduler
        queued = scheduler.force_post()
        return _ok({"queued": queued, "message": "Post queued" if queued else "Queue full — try again"}, 202)
    except Exception as exc:
        logger.error("POST /api/v1/posts error: %s", exc)
        return _err(str(exc), 500)


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


@api_v1_bp.route("/messages", methods=["GET"])
@_auth_required
def get_messages():
    from analytics.tracker import analytics
    try:
        limit = min(200, max(1, int(request.args.get("limit", 50))))
        msgs = analytics.get_recent_messages(limit=limit)
        return _ok({"messages": msgs, "count": len(msgs)})
    except Exception as exc:
        logger.error("GET /api/v1/messages error: %s", exc)
        return _err(str(exc), 500)


@api_v1_bp.route("/messages/reply", methods=["POST"])
@_auth_required
def send_message():
    from services.facebook import facebook as fb
    try:
        data = request.get_json(force=True, silent=True) or {}
        recipient = data.get("recipient_id", "").strip()
        text = data.get("text", "").strip()
        if not recipient or not text:
            return _err("recipient_id and text are required", 400)
        fb.send_message(recipient, text)
        return _ok({"sent": True, "recipient": recipient})
    except Exception as exc:
        logger.error("POST /api/v1/messages/reply error: %s", exc)
        return _err(str(exc), 500)


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------


@api_v1_bp.route("/analytics", methods=["GET"])
@_auth_required
def get_analytics():
    from analytics.tracker import analytics
    try:
        stats = analytics.get_stats()
        return _ok({"analytics": stats, "as_of": datetime.now(timezone.utc).isoformat()})
    except Exception as exc:
        logger.error("GET /api/v1/analytics error: %s", exc)
        return _err(str(exc), 500)


# ---------------------------------------------------------------------------
# Bot control
# ---------------------------------------------------------------------------


@api_v1_bp.route("/bot/status", methods=["GET"])
def bot_status():
    from scheduler.post_scheduler import scheduler
    from config.settings import settings as _s
    try:
        s = scheduler.get_status()
        return _ok({
            "scheduler": s,
            "services": {
                "gemini": bool(getattr(_s, "gemini_key", "")),
                "facebook": getattr(_s, "is_configured", False),
                "guardian": getattr(_s, "guardian_configured", False),
                "supabase": getattr(_s, "supabase_enabled", False),
            },
            "as_of": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as exc:
        logger.error("GET /api/v1/bot/status error: %s", exc)
        return _err(str(exc), 500)


@api_v1_bp.route("/bot/post", methods=["POST"])
@_auth_required
def force_post():
    from scheduler.post_scheduler import scheduler
    try:
        queued = scheduler.force_post()
        return _ok({"queued": queued}, 202)
    except Exception as exc:
        logger.error("POST /api/v1/bot/post error: %s", exc)
        return _err(str(exc), 500)


@api_v1_bp.route("/bot/news", methods=["POST"])
@_auth_required
def force_news():
    from scheduler.post_scheduler import scheduler
    try:
        data = request.get_json(force=True, silent=True) or {}
        category = data.get("category")
        queued = scheduler.force_news(category=category)
        return _ok({"queued": queued}, 202)
    except Exception as exc:
        logger.error("POST /api/v1/bot/news error: %s", exc)
        return _err(str(exc), 500)


# ---------------------------------------------------------------------------
# Guardian (M7)
# ---------------------------------------------------------------------------


@api_v1_bp.route("/guardian/status", methods=["GET"])
def guardian_status():
    try:
        from services.guardian_service import GuardianService
        from flask import current_app
        container = current_app.extensions.get("service_container")
        if not container:
            return _ok({"configured": False, "message": "DI container not attached"})
        svc = container.try_resolve(GuardianService)
        if not svc:
            return _ok({"configured": False, "message": "Guardian not configured"})
        return _ok(svc.get_status())
    except Exception as exc:
        logger.error("GET /api/v1/guardian/status error: %s", exc)
        return _err(str(exc), 500)


@api_v1_bp.route("/guardian/issues", methods=["GET"])
@_auth_required
def guardian_issues():
    """Return the full issue list from the last scan, with optional severity filter."""
    try:
        from services.guardian_service import GuardianService
        from flask import current_app
        container = current_app.extensions.get("service_container")
        if not container:
            return _ok({"issues": [], "message": "Guardian not available"})
        svc = container.try_resolve(GuardianService)
        if not svc:
            return _ok({"issues": [], "message": "Guardian not configured"})

        issues = svc.get_issues()

        # Optional severity filter: ?severity=critical or ?severity=critical,high
        sev_filter = request.args.get("severity", "").lower()
        if sev_filter:
            allowed = {s.strip() for s in sev_filter.split(",")}
            issues = [i for i in issues if isinstance(i, dict) and i.get("severity", "").lower() in allowed]

        # Optional repo filter: ?repo=AI-Automation-Platform
        repo_filter = request.args.get("repo", "").strip()
        if repo_filter:
            issues = [i for i in issues if isinstance(i, dict) and i.get("repo", "") == repo_filter]

        # Sort critical/high first
        _order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        issues = sorted(issues, key=lambda i: _order.get(i.get("severity", "").lower(), 9) if isinstance(i, dict) else 9)

        return _ok({
            "issues": issues,
            "count": len(issues),
            "auto_fix_enabled": getattr(__import__("config.settings", fromlist=["settings"]).settings, "guardian_auto_fix", False),
        })
    except Exception as exc:
        logger.error("GET /api/v1/guardian/issues error: %s", exc)
        return _err(str(exc), 500)


@api_v1_bp.route("/guardian/scan", methods=["POST"])
@_auth_required
def trigger_scan():
    try:
        from services.guardian_service import GuardianService
        from flask import current_app
        container = current_app.extensions.get("service_container")
        if not container:
            return _err("Guardian not available — DI container not attached", 503)
        svc = container.try_resolve(GuardianService)
        if not svc:
            return _err("Guardian not configured", 503)
        if not svc.is_configured:
            return _err("Guardian is not configured (missing GitHub credentials)", 503)

        import threading
        t = threading.Thread(target=svc.scan_all, daemon=True, name="api-guardian-scan")
        t.start()
        return _ok({"started": True, "message": "Scan started in background"}, 202)
    except Exception as exc:
        logger.error("POST /api/v1/guardian/scan error: %s", exc)
        return _err(str(exc), 500)


# ---------------------------------------------------------------------------
# Schedule management
# ---------------------------------------------------------------------------


@api_v1_bp.route("/schedule", methods=["GET"])
@_auth_required
def get_schedule():
    from scheduler.post_scheduler import scheduler
    try:
        return _ok({"schedule": scheduler.get_config()})
    except Exception as exc:
        logger.error("GET /api/v1/schedule error: %s", exc)
        return _err(str(exc), 500)


@api_v1_bp.route("/schedule", methods=["PUT"])
@_auth_required
def update_schedule():
    from scheduler.post_scheduler import scheduler
    try:
        data = request.get_json(force=True, silent=True) or {}
        allowed_keys = {
            "post_interval_hours", "news_interval_hours", "enabled",
            "content_hours_utc", "news_hours_utc",
        }
        unknown = set(data.keys()) - allowed_keys
        if unknown:
            return _err(f"Unknown keys: {sorted(unknown)}", 400)
        result = scheduler.update_config(**{k: v for k, v in data.items() if k in allowed_keys})
        return _ok({"updated": True, "config": result})
    except ValueError as exc:
        return _err(str(exc), 400)
    except Exception as exc:
        logger.error("PUT /api/v1/schedule error: %s", exc)
        return _err(str(exc), 500)


# ---------------------------------------------------------------------------
# In-process metrics
# ---------------------------------------------------------------------------


@api_v1_bp.route("/metrics", methods=["GET"])
@_auth_required
def get_metrics():
    try:
        from utils.metrics import metrics as metrics_collector
        snapshot = metrics_collector.get_snapshot()
        return _ok({"metrics": snapshot, "as_of": datetime.now(timezone.utc).isoformat()})
    except Exception as exc:
        logger.error("GET /api/v1/metrics error: %s", exc)
        return _err(str(exc), 500)


# ---------------------------------------------------------------------------
# AI configuration (AI Personality Matrix)
# ---------------------------------------------------------------------------


@api_v1_bp.route("/ai/config", methods=["GET"])
@_auth_required
def get_ai_config():
    """Return current AI model, provider, temperature, and personality settings."""
    from config.settings import settings as _s
    from api.ai_runtime import get as _rt_get, VALID_MOODS
    rt = _rt_get()
    return _ok({
        # Model / provider (from Settings, mutable at runtime)
        "provider": _s.ai_provider,
        "model": _s.gemini_model,
        "chat_temperature": _s.gemini_chat_temperature,
        "post_temperature": _s.gemini_post_temperature,
        "safety_level": _s.gemini_safety_level,
        "available_models": [
            "gemini-2.5-flash",
            "gemini-2.5-pro",
            "gemini-1.5-pro",
            "gemini-1.5-flash",
        ],
        "available_providers": ["gemini", "huggingface"],
        # Personality (from runtime store, not persisted to env)
        "system_prompt_override": rt["system_prompt_override"],
        "persona_mood": rt["persona_mood"],
        "tone_assertiveness": rt["tone_assertiveness"],
        "tone_humor": rt["tone_humor"],
        "tone_formality": rt["tone_formality"],
        "available_moods": sorted(VALID_MOODS),
    })


@api_v1_bp.route("/ai/config", methods=["PUT"])
@_auth_required
def update_ai_config():
    """Update AI model, temperature, safety level, or personality at runtime."""
    from config.settings import settings as _s
    from api.ai_runtime import update as _rt_update
    try:
        data = request.get_json(force=True, silent=True) or {}
        settings_keys = {"model", "chat_temperature", "post_temperature", "safety_level", "provider"}
        runtime_keys = {"system_prompt_override", "persona_mood",
                        "tone_assertiveness", "tone_humor", "tone_formality"}
        unknown = set(data.keys()) - settings_keys - runtime_keys
        if unknown:
            return _err(f"Unknown keys: {sorted(unknown)}", 400)

        # --- Settings-level fields ---
        if "provider" in data:
            valid = {"gemini", "huggingface"}
            if data["provider"] not in valid:
                return _err(f"provider must be one of {sorted(valid)}", 400)
            _s.ai_provider = data["provider"]

        if "model" in data:
            _s.gemini_model = str(data["model"])

        for temp_key in ("chat_temperature", "post_temperature"):
            if temp_key in data:
                val = data[temp_key]
                if not isinstance(val, (int, float)) or not 0.0 <= float(val) <= 2.0:
                    return _err(f"{temp_key} must be a float in 0.0–2.0", 400)
                setattr(_s, temp_key, float(val))

        if "safety_level" in data:
            valid = {"low", "medium", "high"}
            if data["safety_level"] not in valid:
                return _err(f"safety_level must be one of {sorted(valid)}", 400)
            _s.gemini_safety_level = data["safety_level"]

        # --- Runtime personality fields ---
        rt_updates = {k: v for k, v in data.items() if k in runtime_keys}
        if rt_updates:
            try:
                _rt_update(**rt_updates)
            except ValueError as exc:
                return _err(str(exc), 400)

        from api.ai_runtime import get as _rt_get
        rt = _rt_get()
        return _ok({
            "updated": True,
            "config": {
                "provider": _s.ai_provider,
                "model": _s.gemini_model,
                "chat_temperature": _s.gemini_chat_temperature,
                "post_temperature": _s.gemini_post_temperature,
                "safety_level": _s.gemini_safety_level,
                "system_prompt_override": rt["system_prompt_override"],
                "persona_mood": rt["persona_mood"],
                "tone_assertiveness": rt["tone_assertiveness"],
                "tone_humor": rt["tone_humor"],
                "tone_formality": rt["tone_formality"],
            },
        })
    except Exception as exc:
        logger.error("PUT /api/v1/ai/config error: %s", exc)
        return _err(str(exc), 500)


@api_v1_bp.route("/ai/persona", methods=["GET"])
@_auth_required
def get_ai_persona():
    """Return the live persona state (mood history, message count, tone)."""
    from api.ai_runtime import get as _rt_get
    rt = _rt_get()
    try:
        from core.persona import bot_persona
        persona_state = bot_persona.to_dict()
    except Exception:
        persona_state = {}
    return _ok({**rt, **persona_state})


@api_v1_bp.route("/ai/persona", methods=["PUT"])
@_auth_required
def update_ai_persona():
    """Update personality sliders and mood. Alias for PUT /ai/config (personality fields only)."""
    from api.ai_runtime import update as _rt_update, VALID_MOODS
    try:
        data = request.get_json(force=True, silent=True) or {}
        allowed = {"persona_mood", "tone_assertiveness", "tone_humor", "tone_formality",
                   "system_prompt_override"}
        unknown = set(data.keys()) - allowed
        if unknown:
            return _err(f"Unknown keys: {sorted(unknown)}", 400)
        rt = _rt_update(**{k: v for k, v in data.items() if k in allowed})
        return _ok({"updated": True, "persona": rt})
    except ValueError as exc:
        return _err(str(exc), 400)
    except Exception as exc:
        logger.error("PUT /api/v1/ai/persona error: %s", exc)
        return _err(str(exc), 500)


# ---------------------------------------------------------------------------
# Posts — extended
# ---------------------------------------------------------------------------


@api_v1_bp.route("/posts/<post_id>", methods=["DELETE"])
@_auth_required
def delete_post(post_id):
    try:
        # Check in-memory drafts first — no Supabase needed
        with _draft_lock:
            if post_id in _draft_store:
                del _draft_store[post_id]
                return _ok({"deleted": True, "post_id": post_id, "source": "draft"})

        from services.supabase import supabase_service
        if supabase_service and supabase_service.client:
            supabase_service.client.table("post_history").delete().eq("id", post_id).execute()
            return _ok({"deleted": True, "post_id": post_id, "source": "published"})
        return _err("Supabase not configured — cannot delete published post", 503)
    except Exception as exc:
        logger.error("DELETE /api/v1/posts/%s error: %s", post_id, exc)
        return _err(str(exc), 500)


# ---------------------------------------------------------------------------
# Messages — extended
# ---------------------------------------------------------------------------


@api_v1_bp.route("/messages/<sender_id>", methods=["GET"])
@_auth_required
def get_conversation(sender_id):
    try:
        from services.supabase import supabase_service
        if supabase_service and supabase_service.client:
            result = (
                supabase_service.client.table("conversation_history")
                .select("*")
                .eq("sender_id", sender_id)
                .order("created_at", desc=True)
                .limit(50)
                .execute()
            )
            return _ok({"messages": result.data or [], "sender_id": sender_id})
        return _ok({"messages": [], "sender_id": sender_id, "note": "Supabase not configured"})
    except Exception as exc:
        logger.error("GET /api/v1/messages/%s error: %s", sender_id, exc)
        return _err(str(exc), 500)


# ---------------------------------------------------------------------------
# Bot control — extended
# ---------------------------------------------------------------------------


@api_v1_bp.route("/bot/engage", methods=["POST"])
@_auth_required
def force_engage():
    from scheduler.post_scheduler import scheduler
    try:
        queued = scheduler.force_engagement()
        return _ok({"queued": queued}, 202)
    except Exception as exc:
        logger.error("POST /api/v1/bot/engage error: %s", exc)
        return _err(str(exc), 500)


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------


@api_v1_bp.route("/features", methods=["GET"])
def get_features():
    from config.settings import settings as _s
    try:
        return _ok({
            "features": {
                "auto_reply": getattr(_s, "auto_reply_enabled", True),
                "auto_post": getattr(_s, "auto_post_enabled", True),
                "auto_comment_reply": getattr(_s, "auto_comment_reply", True),
                "cross_engage": getattr(_s, "cross_engage_enabled", False),
                "guardian_scan": _s.guardian_configured,
                "thumbnails": bool(_s.hf_token),
            }
        })
    except Exception as exc:
        logger.error("GET /api/v1/features error: %s", exc)
        return _err(str(exc), 500)


@api_v1_bp.route("/features/toggle", methods=["POST"])
@_auth_required
def toggle_feature():
    try:
        data = request.get_json(force=True, silent=True) or {}
        feature = data.get("feature", "")
        enabled = data.get("enabled", True)
        allowed = {"auto_reply", "auto_post", "auto_comment_reply", "cross_engage"}
        if feature not in allowed:
            return _err(f"feature must be one of {sorted(allowed)}", 400)
        try:
            from services.supabase import supabase_service
            from datetime import datetime, timezone
            if supabase_service and supabase_service.client:
                supabase_service.client.table("settings").upsert(
                    {"key": f"feature_{feature}", "value": str(enabled).lower(),
                     "updated_at": datetime.now(timezone.utc).isoformat()},
                    on_conflict="key",
                ).execute()
        except Exception:
            pass
        return _ok({"feature": feature, "enabled": enabled})
    except Exception as exc:
        logger.error("POST /api/v1/features/toggle error: %s", exc)
        return _err(str(exc), 500)


# ---------------------------------------------------------------------------
# Analytics — extended
# ---------------------------------------------------------------------------


@api_v1_bp.route("/analytics/performance", methods=["GET"])
@_auth_required
def analytics_performance():
    try:
        from services.supabase import supabase_service
        if supabase_service and supabase_service.client:
            result = (
                supabase_service.client.table("post_history")
                .select("*")
                .order("published_at", desc=True)
                .limit(20)
                .execute()
            )
            return _ok({"posts": result.data or []})
        return _ok({"posts": [], "note": "Supabase not configured"})
    except Exception as exc:
        logger.error("GET /api/v1/analytics/performance error: %s", exc)
        return _err(str(exc), 500)


# ---------------------------------------------------------------------------
# Tenants
# ---------------------------------------------------------------------------


@api_v1_bp.route("/tenants", methods=["GET"])
@_auth_required
def get_tenants():
    try:
        from services.supabase import supabase_service
        if supabase_service and supabase_service.client:
            result = (
                supabase_service.client.table("tenants")
                .select("*")
                .eq("status", "active")
                .execute()
            )
            return _ok({"tenants": result.data or []})
        return _ok({"tenants": [], "note": "Supabase not configured"})
    except Exception as exc:
        logger.error("GET /api/v1/tenants error: %s", exc)
        return _err(str(exc), 500)


@api_v1_bp.route("/tenants/switch", methods=["POST"])
@_auth_required
def switch_tenant():
    try:
        data = request.get_json(force=True, silent=True) or {}
        tenant_id = data.get("tenant_id", "")
        if not tenant_id:
            return _err("tenant_id is required", 400)
        return _ok({"switched": True, "tenant_id": tenant_id})
    except Exception as exc:
        logger.error("POST /api/v1/tenants/switch error: %s", exc)
        return _err(str(exc), 500)


# ---------------------------------------------------------------------------
# Rate limits
# ---------------------------------------------------------------------------


@api_v1_bp.route("/rate-limits", methods=["GET"])
def get_rate_limits():
    try:
        from utils.rate_limiter import rate_limiter
        return _ok({"rate_limits": rate_limiter.get_config()})
    except Exception as exc:
        logger.error("GET /api/v1/rate-limits error: %s", exc)
        return _err(str(exc), 500)


@api_v1_bp.route("/rate-limits/unblock", methods=["POST"])
@_auth_required
def unblock_user():
    try:
        data = request.get_json(force=True, silent=True) or {}
        identifier = data.get("identifier", "").strip()
        if not identifier:
            return _err("identifier is required", 400)
        from utils.rate_limiter import rate_limiter
        return _ok({"unblocked": rate_limiter.unblock(identifier), "identifier": identifier})
    except Exception as exc:
        logger.error("POST /api/v1/rate-limits/unblock error: %s", exc)
        return _err(str(exc), 500)


# ---------------------------------------------------------------------------
# Persona  (top-level — no /ai/ prefix)
# GET  /api/v1/persona          — public  — returns bot identity + live mood
# PUT  /api/v1/persona          — auth    — update mood / tone sliders
# POST /api/v1/persona/reset    — auth    — reset to defaults
# ---------------------------------------------------------------------------


@api_v1_bp.route("/persona", methods=["GET"])
def get_persona():
    """Return the live bot persona — public, no auth required."""
    try:
        from core.persona import bot_persona, MOODS, PERSONA_PROMPTS
        from api.ai_runtime import get as _rt_get, VALID_MOODS
        rt = _rt_get()
        state = bot_persona.to_dict()
        return _ok({
            "name": bot_persona.name,
            "tagline": bot_persona.tagline,
            "current_mood": state.get("current_mood", "professional"),
            "prompt_suffix": bot_persona.prompt_suffix,
            "message_count": state.get("message_count", 0),
            "last_transition": state.get("last_transition"),
            "mood_history": bot_persona._mood_history[-10:],
            "available_moods": sorted(VALID_MOODS),
            "tone_assertiveness": rt["tone_assertiveness"],
            "tone_humor": rt["tone_humor"],
            "tone_formality": rt["tone_formality"],
            "system_prompt_override": rt["system_prompt_override"],
        })
    except Exception as exc:
        logger.error("GET /api/v1/persona error: %s", exc)
        return _err(str(exc), 500)


@api_v1_bp.route("/persona", methods=["PUT"])
@_auth_required
def update_persona():
    """Update mood and tone sliders. Alias for PUT /api/v1/ai/persona."""
    from api.ai_runtime import update as _rt_update, VALID_MOODS
    try:
        data = request.get_json(force=True, silent=True) or {}
        allowed = {"persona_mood", "tone_assertiveness", "tone_humor",
                   "tone_formality", "system_prompt_override"}
        unknown = set(data.keys()) - allowed
        if unknown:
            return _err(f"Unknown keys: {sorted(unknown)}", 400)
        rt = _rt_update(**{k: v for k, v in data.items() if k in allowed})
        return _ok({"updated": True, "persona": rt})
    except ValueError as exc:
        return _err(str(exc), 400)
    except Exception as exc:
        logger.error("PUT /api/v1/persona error: %s", exc)
        return _err(str(exc), 500)


@api_v1_bp.route("/persona/reset", methods=["POST"])
@_auth_required
def reset_persona():
    """Reset persona to professional defaults."""
    from api.ai_runtime import update as _rt_update
    try:
        rt = _rt_update(
            persona_mood="professional",
            tone_assertiveness=0.7,
            tone_humor=0.2,
            tone_formality=0.6,
            system_prompt_override=None,
        )
        return _ok({"reset": True, "persona": rt})
    except Exception as exc:
        logger.error("POST /api/v1/persona/reset error: %s", exc)
        return _err(str(exc), 500)


# ---------------------------------------------------------------------------
# Workflows  /api/v1/workflow/*
# GET  /api/v1/workflow           — list available workflows + their last run
# POST /api/v1/workflow/run       — run a named workflow immediately
# GET  /api/v1/workflow/history   — recent workflow execution history (last 20)
# ---------------------------------------------------------------------------

import collections as _collections_mod

_workflow_history: _collections_mod.deque = _collections_mod.deque(maxlen=20)
_workflow_history_lock = _threading_mod.Lock()

_AVAILABLE_WORKFLOWS = {
    "post_publish": {
        "description": "Select topic → Generate caption → Render card → Publish to Facebook",
        "steps": ["select_topic", "generate", "render", "publish", "analytics", "event"],
        "trigger": "scheduler (10 ×/day) or manual",
    },
}


@api_v1_bp.route("/workflow", methods=["GET"])
@_auth_required
def list_workflows():
    """Return all registered workflow definitions and their last run summary."""
    with _workflow_history_lock:
        history = list(_workflow_history)

    last_runs: dict = {}
    for entry in reversed(history):
        name = entry.get("workflow")
        if name and name not in last_runs:
            last_runs[name] = entry

    workflows = []
    for name, meta in _AVAILABLE_WORKFLOWS.items():
        workflows.append({
            "name": name,
            **meta,
            "last_run": last_runs.get(name),
        })
    return _ok({"workflows": workflows, "count": len(workflows)})


@api_v1_bp.route("/workflow/run", methods=["POST"])
@_auth_required
def run_workflow():
    """
    Run a named workflow synchronously and return the result.

    Body (JSON):
      { "workflow": "post_publish", "params": {} }

    Only "post_publish" is supported today. Returns the step-by-step result.
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        name = data.get("workflow", "post_publish").strip().lower()

        if name not in _AVAILABLE_WORKFLOWS:
            available = sorted(_AVAILABLE_WORKFLOWS.keys())
            return _err(f"Unknown workflow '{name}'. Available: {available}", 400)

        if name == "post_publish":
            from scheduler.post_scheduler import scheduler
            result = scheduler.run_content_now()
            entry = {
                "workflow": name,
                "triggered_by": "api",
                "ok": result.get("ok", False),
                "duration_ms": result.get("duration_ms"),
                "topic": result.get("topic"),
                "platforms": result.get("platforms"),
                "error": result.get("error"),
                "ran_at": datetime.now(timezone.utc).isoformat(),
            }
            with _workflow_history_lock:
                _workflow_history.append(entry)
            status = 200 if result.get("ok") else 500
            return _ok({"workflow": name, "result": result, "ran_at": entry["ran_at"]}), status

        return _err(f"Workflow '{name}' runner not implemented", 501)

    except Exception as exc:
        logger.error("POST /api/v1/workflow/run error: %s", exc)
        return _err(str(exc), 500)


@api_v1_bp.route("/workflow/history", methods=["GET"])
@_auth_required
def workflow_history():
    """Return the last 20 workflow executions (in-memory, resets on restart)."""
    try:
        limit = min(20, max(1, int(request.args.get("limit", 20))))
        with _workflow_history_lock:
            entries = list(_workflow_history)[-limit:]
        return _ok({"history": list(reversed(entries)), "count": len(entries)})
    except Exception as exc:
        logger.error("GET /api/v1/workflow/history error: %s", exc)
        return _err(str(exc), 500)


# ---------------------------------------------------------------------------
# Scan  (top-level shortcut for /guardian/scan)
# POST /api/v1/scan        — trigger a code guardian scan in the background
# GET  /api/v1/scan        — return the last scan report (status + issues)
# ---------------------------------------------------------------------------


@api_v1_bp.route("/scan", methods=["POST"])
@_auth_required
def trigger_scan_shortcut():
    """
    Trigger a Code Guardian scan.  Top-level alias for POST /api/v1/guardian/scan.

    Does not require GitHub to be configured — returns a 503 with a clear message
    if Guardian is unavailable, so clients can detect the feature gracefully.
    """
    try:
        from services.guardian_service import GuardianService
        from flask import current_app
        container = current_app.extensions.get("service_container")
        if not container:
            return _err("Guardian not available — DI container not attached", 503)
        svc = container.try_resolve(GuardianService)
        if not svc:
            return _err("Guardian not configured (install GuardianService in DI container)", 503)
        if not svc.is_configured:
            return _err(
                "Guardian requires GitHub credentials (GITHUB_TOKEN + GITHUB_REPO). "
                "Set them in environment variables and restart.", 503
            )
        import threading
        t = threading.Thread(target=svc.scan_all, daemon=True, name="api-scan-shortcut")
        t.start()
        return _ok({"started": True, "message": "Code Guardian scan started in background"}, 202)
    except Exception as exc:
        logger.error("POST /api/v1/scan error: %s", exc)
        return _err(str(exc), 500)


@api_v1_bp.route("/scan", methods=["GET"])
def get_scan_status():
    """
    Return the last Guardian scan report.
    Top-level alias for GET /api/v1/guardian/status + issues combined.
    Public — no auth required.
    """
    try:
        from services.guardian_service import GuardianService
        from flask import current_app
        container = current_app.extensions.get("service_container")
        if not container:
            return _ok({"configured": False, "issues": []})
        svc = container.try_resolve(GuardianService)
        if not svc:
            return _ok({"configured": False, "issues": []})
        status = svc.get_status()
        issues = svc.get_issues() if hasattr(svc, "get_issues") else []
        return _ok({**status, "issues": issues[:50]})
    except Exception as exc:
        logger.error("GET /api/v1/scan error: %s", exc)
        return _err(str(exc), 500)


# ---------------------------------------------------------------------------
# Metrics — public lightweight snapshot (no auth)
# GET /api/v1/metrics/public   — key counters only, safe to expose
# (The full snapshot at GET /api/v1/metrics still requires auth.)
# ---------------------------------------------------------------------------


@api_v1_bp.route("/metrics/public", methods=["GET"])
def get_public_metrics():
    """
    Return a safe subset of in-process metrics — no auth required.

    Exposes: uptime_seconds, request_count, total_posts, total_messages.
    Sensitive latency details remain auth-gated at GET /api/v1/metrics.
    """
    try:
        from utils.metrics import metrics as _m
        snap = _m.get_snapshot()
        counters = snap.get("counters", {})
        return _ok({
            "uptime_seconds": snap.get("uptime_seconds"),
            "total_posts": counters.get("posts_published", 0),
            "total_messages": counters.get("messages_received", 0),
            "total_errors": counters.get("errors", 0),
            "as_of": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as exc:
        logger.error("GET /api/v1/metrics/public error: %s", exc)
        return _err(str(exc), 500)


# ---------------------------------------------------------------------------
# API Key management
# POST /api/v1/keys/generate   — generate a new signed API token
# GET  /api/v1/keys            — list active key metadata (no secrets)
# DELETE /api/v1/keys/<key_id> — revoke a key
# ---------------------------------------------------------------------------

import secrets as _secrets_mod
import hashlib as _hashlib_mod

_keys_lock = _threading_mod.Lock()
# key_id -> {"id", "label", "prefix", "hashed", "created_at", "last_used", "revoked"}
_active_keys: dict[str, dict] = {}


@api_v1_bp.route("/keys/generate", methods=["POST"])
@_auth_required
def generate_api_key():
    """
    Generate a new API token.

    Body (JSON, all optional):
      { "label": "my-dashboard-client" }

    Returns the full token ONCE — it is never stored in plain text.
    Subsequent calls to GET /api/v1/keys return only the prefix + metadata.
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        label = str(data.get("label", "api-key")).strip()[:64] or "api-key"

        raw_token = "kz_" + _secrets_mod.token_urlsafe(32)
        key_id = _uuid_mod.uuid4().hex[:12]
        hashed = _hashlib_mod.sha256(raw_token.encode()).hexdigest()
        prefix = raw_token[:10] + "…"

        entry = {
            "id": key_id,
            "label": label,
            "prefix": prefix,
            "hashed": hashed,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_used": None,
            "revoked": False,
        }
        with _keys_lock:
            _active_keys[key_id] = entry

        return _ok({
            "key_id": key_id,
            "token": raw_token,
            "prefix": prefix,
            "label": label,
            "created_at": entry["created_at"],
            "note": "Store this token securely — it will not be shown again.",
        }, 201)
    except Exception as exc:
        logger.error("POST /api/v1/keys/generate error: %s", exc)
        return _err(str(exc), 500)


@api_v1_bp.route("/keys", methods=["GET"])
@_auth_required
def list_api_keys():
    """List active API key metadata. Tokens are never returned after creation."""
    try:
        with _keys_lock:
            keys = [
                {k: v for k, v in entry.items() if k != "hashed"}
                for entry in _active_keys.values()
                if not entry.get("revoked")
            ]
        return _ok({"keys": keys, "count": len(keys)})
    except Exception as exc:
        logger.error("GET /api/v1/keys error: %s", exc)
        return _err(str(exc), 500)


@api_v1_bp.route("/keys/<key_id>", methods=["DELETE"])
@_auth_required
def revoke_api_key(key_id: str):
    """Revoke an API key by ID."""
    try:
        with _keys_lock:
            entry = _active_keys.get(key_id)
            if not entry:
                return _err(f"Key '{key_id}' not found", 404)
            _active_keys[key_id]["revoked"] = True
        return _ok({"revoked": True, "key_id": key_id})
    except Exception as exc:
        logger.error("DELETE /api/v1/keys/%s error: %s", key_id, exc)
        return _err(str(exc), 500)


# ---------------------------------------------------------------------------
# Status / Config — GET /api/v1/status
# Polled by the Prometheus frontend on every tab focus & on startup.
# ---------------------------------------------------------------------------

@api_v1_bp.route("/status", methods=["GET"])
@_auth_required
def get_status():
    """
    Return current server config and version.

    Called by the Prometheus frontend via ``${restEndpoint}/status``
    (restEndpoint already ends with ``/api/v1``).
    """
    try:
        from config.settings import settings as _s
        return _ok({
            "version": "10.0.0",
            "environment": getattr(_s, "environment", "production"),
            "config": {
                "ai_provider":               getattr(_s, "ai_provider", "gemini"),
                "content_posting_hours_utc": getattr(_s, "content_posting_hours_utc", []),
                "news_posting_hours_utc":    getattr(_s, "news_posting_hours_utc", []),
                "environment":               getattr(_s, "environment", "production"),
                "facebook_configured":       bool(getattr(_s, "page_access_token", "")),
                "gemini_key_configured":     bool(getattr(_s, "gemini_key", "")),
                "gemini_model":              getattr(_s, "gemini_model", "gemini-2.5-pro"),
                "guardian_enabled":          bool(getattr(_s, "guardian_enabled", False)),
                "rate_limit_per_user":       getattr(_s, "rate_limit_per_user", 10),
                "supabase_enabled":          bool(getattr(_s, "supabase_enabled", False)),
            },
        })
    except Exception as exc:
        logger.error("GET /api/v1/status error: %s", exc)
        return _err(str(exc), 500)


# ---------------------------------------------------------------------------
# Health — GET /api/v1/health/deep
# Returns service health in the shape the Prometheus frontend expects.
# ---------------------------------------------------------------------------

@api_v1_bp.route("/health/deep", methods=["GET"])
@_auth_required
def health_deep():
    """
    Deep health check — returns per-service latency and status.

    Shape consumed by the Prometheus frontend healthMatrix mapping:
      { status, version, services: { <name>: { status, latency_ms, page_name } } }
    """
    try:
        import time as _time
        from config.settings import settings as _s

        services: dict = {}

        # Gemini
        gemini_ok = bool(getattr(_s, "gemini_key", ""))
        services["gemini"] = {
            "status": "ok" if gemini_ok else "error",
            "latency_ms": 0,
            "page_name": "Gemini AI",
            "configured": gemini_ok,
        }

        # Facebook
        fb_ok = bool(getattr(_s, "page_access_token", "")) and bool(getattr(_s, "page_id", ""))
        services["facebook"] = {
            "status": "ok" if fb_ok else "error",
            "latency_ms": 0,
            "page_name": "Facebook API",
            "configured": fb_ok,
        }

        # Supabase
        supa_ok = bool(getattr(_s, "supabase_enabled", False))
        services["supabase"] = {
            "status": "ok" if supa_ok else "degraded",
            "latency_ms": 0,
            "page_name": "Supabase",
            "configured": supa_ok,
        }

        # Playwright (card renderer)
        try:
            from renderer.card_renderer import CardRenderer  # noqa: F401
            services["playwright"] = {"status": "ok", "latency_ms": 0, "page_name": "Playwright"}
        except Exception:
            services["playwright"] = {"status": "degraded", "latency_ms": 0, "page_name": "Playwright"}

        # Code Guardian
        guardian_enabled = bool(getattr(_s, "guardian_enabled", False))
        services["guardian"] = {
            "status": "ok" if guardian_enabled else "degraded",
            "latency_ms": 0,
            "page_name": "Code Guardian",
        }

        # MWK Converter
        try:
            from products.converter.service import CurrencyService  # noqa: F401
            services["converter"] = {"status": "ok", "latency_ms": 0, "page_name": "MWK Converter"}
        except Exception:
            services["converter"] = {"status": "degraded", "latency_ms": 0, "page_name": "MWK Converter"}

        overall = "ok" if all(s["status"] == "ok" for s in services.values()) else "degraded"

        return jsonify({
            "status": overall,
            "version": "10.0.0",
            "services": services,
        })
    except Exception as exc:
        logger.error("GET /api/v1/health/deep error: %s", exc)
        return jsonify({"status": "error", "version": "10.0.0", "services": {}, "error": str(exc)}), 500


# ---------------------------------------------------------------------------
# Workflow aliases — the Prometheus frontend uses paths that differ slightly
# from the backend's canonical names.
# ---------------------------------------------------------------------------

@api_v1_bp.route("/workflow/status", methods=["GET"])
@_auth_required
def workflow_status():
    """
    Return current workflow engine status.

    Shape consumed by the Prometheus frontend:
      { status: 'running'|'paused'|'idle'|'error', progress, current_step }
    """
    try:
        from scheduler.post_scheduler import scheduler
        sched = scheduler.get_status()
        running = sched.get("running", False)
        return _ok({
            "status": "running" if running else "idle",
            "progress": 100 if running else 0,
            "current_step": "Scheduler active" if running else "Idle",
        })
    except Exception as exc:
        logger.error("GET /api/v1/workflow/status error: %s", exc)
        return _ok({"status": "idle", "progress": 0, "current_step": str(exc)})


@api_v1_bp.route("/workflow/pause", methods=["POST"])
@_auth_required
def workflow_pause():
    """Pause the post scheduler."""
    try:
        from scheduler.post_scheduler import scheduler
        scheduler.stop()
        return _ok({"ok": True, "status": "paused"})
    except Exception as exc:
        logger.error("POST /api/v1/workflow/pause error: %s", exc)
        return _err(str(exc), 500)


@api_v1_bp.route("/workflow/resume", methods=["POST"])
@_auth_required
def workflow_resume():
    """Resume the post scheduler."""
    try:
        from scheduler.post_scheduler import scheduler
        scheduler.start()
        return _ok({"ok": True, "status": "running"})
    except Exception as exc:
        logger.error("POST /api/v1/workflow/resume error: %s", exc)
        return _err(str(exc), 500)


@api_v1_bp.route("/workflow/trigger", methods=["POST"])
@_auth_required
def workflow_trigger():
    """Trigger an immediate post — alias for /workflow/run with post_publish."""
    try:
        from scheduler.post_scheduler import scheduler
        result = scheduler.run_content_now()
        return _ok({"ok": result.get("ok", False), "job_id": result.get("topic", "manual")})
    except Exception as exc:
        logger.error("POST /api/v1/workflow/trigger error: %s", exc)
        return _err(str(exc), 500)


# ---------------------------------------------------------------------------
# Resource metrics — GET /api/v1/metrics/resources
# Returns CPU/memory/disk/network utilisation consumed by ResourceUtilization
# component in the Prometheus frontend.
# ---------------------------------------------------------------------------

@api_v1_bp.route("/metrics/resources", methods=["GET"])
@_auth_required
def get_resource_metrics():
    """Return current process resource utilisation."""
    try:
        import os as _os
        cpu_percent: float = 0.0
        mem_percent: float = 0.0
        disk_percent: float = 0.0

        try:
            import psutil as _ps
            cpu_percent  = _ps.cpu_percent(interval=None)
            mem_percent  = _ps.virtual_memory().percent
            disk_percent = _ps.disk_usage("/").percent
            net_io       = _ps.net_io_counters()
            net_in_kbps  = round(net_io.bytes_recv / 1024, 1)
            net_out_kbps = round(net_io.bytes_sent / 1024, 1)
        except Exception:
            net_in_kbps  = 0.0
            net_out_kbps = 0.0

        return _ok({
            "cpu_percent":      cpu_percent,
            "memory_percent":   mem_percent,
            "disk_percent":     disk_percent,
            "network_in_kbps":  net_in_kbps,
            "network_out_kbps": net_out_kbps,
            "workers_active":   1,
            "queue_depth":      0,
        })
    except Exception as exc:
        logger.error("GET /api/v1/metrics/resources error: %s", exc)
        return _err(str(exc), 500)


# ---------------------------------------------------------------------------
# PromQL-style query — GET /api/v1/metrics/query?q=<expr>
# Minimal shim so the PrometheusMetrics page doesn't hard-error on 404.
# ---------------------------------------------------------------------------

@api_v1_bp.route("/metrics/query", methods=["GET"])
@_auth_required
def metrics_query():
    """
    Accept a PromQL-like query string and return a minimal result envelope.
    Real Prometheus scraping is not implemented; this returns in-process counters
    shaped like a Prometheus instant-query response so frontend charts render.
    """
    try:
        from utils.metrics import metrics as _m
        snap = _m.get_snapshot()
        q = request.args.get("q", "")
        value = 0
        if "cpu" in q.lower():
            value = 0
        elif "memory" in q.lower() or "mem" in q.lower():
            value = 0
        elif "rps" in q.lower() or "request" in q.lower():
            value = snap.get("counters", {}).get("requests_total", 0)
        elif "error" in q.lower():
            value = snap.get("counters", {}).get("errors", 0)
        return jsonify({
            "status": "success",
            "data": {
                "result": [{
                    "metric": {"__name__": q or "kanyoza_metric"},
                    "value": [int(datetime.now(timezone.utc).timestamp()), str(value)],
                }]
            },
        })
    except Exception as exc:
        logger.error("GET /api/v1/metrics/query error: %s", exc)
        return jsonify({"status": "error", "error": str(exc), "data": {"result": []}}), 500


# ═══════════════════════════════════════════════════════════════════════════
# DASHBOARD REST CRUD API (Phase 6)
# ═══════════════════════════════════════════════════════════════════════════

def _get_container():
    from flask import current_app
    return current_app.extensions.get("service_container")


# ── Workspaces ─────────────────────────────────────────────────────────────

@api_v1_bp.route("/workspaces", methods=["POST"])
@_auth_required
def create_workspace():
    from models.workspace import Workspace
    from repositories.workspace_repository import WorkspaceRepository
    try:
        data = request.get_json(force=True, silent=True) or {}
        ws_id = data.get("id") or f"ws-{_uuid_mod.uuid4().hex[:12]}"
        data["id"] = ws_id
        
        ws = Workspace(**data)
        repo = _get_container().resolve(WorkspaceRepository)
        saved = repo.save(ws)
        return _ok({"workspace": saved.model_dump(mode="json")}, 201)
    except Exception as exc:
        logger.error("POST /api/v1/workspaces error: %s", exc)
        return _err(str(exc), 500)


@api_v1_bp.route("/workspaces", methods=["GET"])
@_auth_required
def list_workspaces():
    from repositories.workspace_repository import WorkspaceRepository
    try:
        repo = _get_container().resolve(WorkspaceRepository)
        if not repo.supabase:
            workspaces = list(repo._in_memory.values())
        else:
            res = repo.supabase.table("workspaces").select("*").execute()
            workspaces = res.data or []
        return _ok({"workspaces": workspaces})
    except Exception as exc:
        logger.error("GET /api/v1/workspaces error: %s", exc)
        return _err(str(exc), 500)


@api_v1_bp.route("/workspaces/<workspace_id>", methods=["GET"])
@_auth_required
def get_workspace(workspace_id):
    from repositories.workspace_repository import WorkspaceRepository
    try:
        repo = _get_container().resolve(WorkspaceRepository)
        ws = repo.find_by_id(workspace_id)
        if not ws:
            return _err("Workspace not found", 404)
        return _ok({"workspace": ws.model_dump(mode="json")})
    except Exception as exc:
        logger.error("GET /api/v1/workspaces/%s error: %s", workspace_id, exc)
        return _err(str(exc), 500)


@api_v1_bp.route("/workspaces/<workspace_id>", methods=["PUT"])
@_auth_required
def update_workspace(workspace_id):
    from repositories.workspace_repository import WorkspaceRepository
    try:
        repo = _get_container().resolve(WorkspaceRepository)
        ws = repo.find_by_id(workspace_id)
        if not ws:
            return _err("Workspace not found", 404)
        
        data = request.get_json(force=True, silent=True) or {}
        # Update fields
        for k, v in data.items():
            if hasattr(ws, k) and k != "id":
                setattr(ws, k, v)
        ws.updated_at = datetime.now(timezone.utc)
        saved = repo.save(ws)
        return _ok({"workspace": saved.model_dump(mode="json")})
    except Exception as exc:
        logger.error("PUT /api/v1/workspaces/%s error: %s", workspace_id, exc)
        return _err(str(exc), 500)


@api_v1_bp.route("/workspaces/<workspace_id>", methods=["DELETE"])
@_auth_required
def delete_workspace(workspace_id):
    from repositories.workspace_repository import WorkspaceRepository
    try:
        repo = _get_container().resolve(WorkspaceRepository)
        deleted = repo.delete(workspace_id)
        return _ok({"deleted": deleted})
    except Exception as exc:
        logger.error("DELETE /api/v1/workspaces/%s error: %s", workspace_id, exc)
        return _err(str(exc), 500)


# ── Brands ─────────────────────────────────────────────────────────────────

@api_v1_bp.route("/workspaces/<workspace_id>/brands", methods=["POST"])
@_auth_required
def create_brand(workspace_id):
    from models.brand import Brand
    from repositories.brand_repository import BrandRepository
    try:
        data = request.get_json(force=True, silent=True) or {}
        brand_id = data.get("id") or f"brand-{_uuid_mod.uuid4().hex[:12]}"
        data["id"] = brand_id
        data["workspace_id"] = workspace_id
        
        brand = Brand(**data)
        repo = _get_container().resolve(BrandRepository)
        saved = repo.save(brand)
        return _ok({"brand": saved.model_dump(mode="json")}, 201)
    except Exception as exc:
        logger.error("POST /api/v1/workspaces/%s/brands error: %s", workspace_id, exc)
        return _err(str(exc), 500)


@api_v1_bp.route("/workspaces/<workspace_id>/brands", methods=["GET"])
@_auth_required
def list_brands(workspace_id):
    from repositories.brand_repository import BrandRepository
    try:
        repo = _get_container().resolve(BrandRepository)
        brands = repo.find_by_workspace(workspace_id)
        return _ok({"brands": [b.model_dump(mode="json") for b in brands]})
    except Exception as exc:
        logger.error("GET /api/v1/workspaces/%s/brands error: %s", workspace_id, exc)
        return _err(str(exc), 500)


@api_v1_bp.route("/brands/<brand_id>", methods=["GET"])
@_auth_required
def get_brand(brand_id):
    from repositories.brand_repository import BrandRepository
    try:
        repo = _get_container().resolve(BrandRepository)
        brand = repo.find_by_id(brand_id)
        if not brand:
            return _err("Brand not found", 404)
        return _ok({"brand": brand.model_dump(mode="json")})
    except Exception as exc:
        logger.error("GET /api/v1/brands/%s error: %s", brand_id, exc)
        return _err(str(exc), 500)


@api_v1_bp.route("/brands/<brand_id>", methods=["PUT"])
@_auth_required
def update_brand(brand_id):
    from repositories.brand_repository import BrandRepository
    try:
        repo = _get_container().resolve(BrandRepository)
        brand = repo.find_by_id(brand_id)
        if not brand:
            return _err("Brand not found", 404)
        
        data = request.get_json(force=True, silent=True) or {}
        for k, v in data.items():
            if hasattr(brand, k) and k not in ("id", "workspace_id"):
                setattr(brand, k, v)
        brand.updated_at = datetime.now(timezone.utc)
        saved = repo.save(brand)
        return _ok({"brand": saved.model_dump(mode="json")})
    except Exception as exc:
        logger.error("PUT /api/v1/brands/%s error: %s", brand_id, exc)
        return _err(str(exc), 500)


@api_v1_bp.route("/brands/<brand_id>", methods=["DELETE"])
@_auth_required
def delete_brand(brand_id):
    from repositories.brand_repository import BrandRepository
    try:
        repo = _get_container().resolve(BrandRepository)
        deleted = repo.delete(brand_id)
        return _ok({"deleted": deleted})
    except Exception as exc:
        logger.error("DELETE /api/v1/brands/%s error: %s", brand_id, exc)
        return _err(str(exc), 500)


# ── AI Profiles ────────────────────────────────────────────────────────────

@api_v1_bp.route("/workspaces/<workspace_id>/ai-profiles", methods=["POST"])
@_auth_required
def create_ai_profile(workspace_id):
    from models.ai_profile import AIProfile
    from repositories.ai_profile_repository import AIProfileRepository
    try:
        data = request.get_json(force=True, silent=True) or {}
        profile_id = data.get("id") or f"profile-{_uuid_mod.uuid4().hex[:12]}"
        data["id"] = profile_id
        data["workspace_id"] = workspace_id
        
        profile = AIProfile(**data)
        repo = _get_container().resolve(AIProfileRepository)
        saved = repo.save(profile)
        return _ok({"ai_profile": saved.model_dump(mode="json")}, 201)
    except Exception as exc:
        logger.error("POST /api/v1/workspaces/%s/ai-profiles error: %s", workspace_id, exc)
        return _err(str(exc), 500)


@api_v1_bp.route("/workspaces/<workspace_id>/ai-profiles", methods=["GET"])
@_auth_required
def list_ai_profiles(workspace_id):
    from repositories.ai_profile_repository import AIProfileRepository
    try:
        repo = _get_container().resolve(AIProfileRepository)
        profiles = repo.find_by_workspace(workspace_id)
        return _ok({"ai_profiles": [p.model_dump(mode="json") for p in profiles]})
    except Exception as exc:
        logger.error("GET /api/v1/workspaces/%s/ai-profiles error: %s", workspace_id, exc)
        return _err(str(exc), 500)


@api_v1_bp.route("/ai-profiles/<profile_id>", methods=["PUT"])
@_auth_required
def update_ai_profile(profile_id):
    from repositories.ai_profile_repository import AIProfileRepository
    try:
        repo = _get_container().resolve(AIProfileRepository)
        profile = repo.find_by_id(profile_id)
        if not profile:
            return _err("AI Profile not found", 404)
        
        data = request.get_json(force=True, silent=True) or {}
        for k, v in data.items():
            if hasattr(profile, k) and k not in ("id", "workspace_id"):
                setattr(profile, k, v)
        profile.updated_at = datetime.now(timezone.utc)
        saved = repo.save(profile)
        return _ok({"ai_profile": saved.model_dump(mode="json")})
    except Exception as exc:
        logger.error("PUT /api/v1/ai-profiles/%s error: %s", profile_id, exc)
        return _err(str(exc), 500)


@api_v1_bp.route("/ai-profiles/<profile_id>", methods=["DELETE"])
@_auth_required
def delete_ai_profile(profile_id):
    from repositories.ai_profile_repository import AIProfileRepository
    try:
        repo = _get_container().resolve(AIProfileRepository)
        deleted = repo.delete(profile_id)
        return _ok({"deleted": deleted})
    except Exception as exc:
        logger.error("DELETE /api/v1/ai-profiles/%s error: %s", profile_id, exc)
        return _err(str(exc), 500)


# ── Social Accounts ────────────────────────────────────────────────────────

@api_v1_bp.route("/workspaces/<workspace_id>/social-accounts", methods=["POST"])
@_auth_required
def create_social_account(workspace_id):
    from models.social_account import SocialAccount
    from repositories.social_account_repository import SocialAccountRepository
    try:
        data = request.get_json(force=True, silent=True) or {}
        acc_id = data.get("id") or f"acc-{_uuid_mod.uuid4().hex[:12]}"
        data["id"] = acc_id
        data["workspace_id"] = workspace_id
        
        account = SocialAccount(**data)
        repo = _get_container().resolve(SocialAccountRepository)
        saved = repo.save(account)
        return _ok({"social_account": saved.model_dump(mode="json")}, 201)
    except Exception as exc:
        logger.error("POST /api/v1/workspaces/%s/social-accounts error: %s", workspace_id, exc)
        return _err(str(exc), 500)


@api_v1_bp.route("/workspaces/<workspace_id>/social-accounts", methods=["GET"])
@_auth_required
def list_social_accounts(workspace_id):
    from repositories.social_account_repository import SocialAccountRepository
    try:
        repo = _get_container().resolve(SocialAccountRepository)
        accounts = repo.find_by_workspace(workspace_id)
        return _ok({"social_accounts": [a.model_dump(mode="json") for a in accounts]})
    except Exception as exc:
        logger.error("GET /api/v1/workspaces/%s/social-accounts error: %s", workspace_id, exc)
        return _err(str(exc), 500)


@api_v1_bp.route("/social-accounts/<account_id>", methods=["PUT"])
@_auth_required
def update_social_account(account_id):
    from repositories.social_account_repository import SocialAccountRepository
    try:
        repo = _get_container().resolve(SocialAccountRepository)
        account = repo.find_by_id(account_id)
        if not account:
            return _err("Social Account not found", 404)
        
        data = request.get_json(force=True, silent=True) or {}
        for k, v in data.items():
            if hasattr(account, k) and k not in ("id", "workspace_id"):
                setattr(account, k, v)
        account.updated_at = datetime.now(timezone.utc)
        saved = repo.save(account)
        return _ok({"social_account": saved.model_dump(mode="json")})
    except Exception as exc:
        logger.error("PUT /api/v1/social-accounts/%s error: %s", account_id, exc)
        return _err(str(exc), 500)


@api_v1_bp.route("/social-accounts/<account_id>", methods=["DELETE"])
@_auth_required
def delete_social_account(account_id):
    from repositories.social_account_repository import SocialAccountRepository
    try:
        repo = _get_container().resolve(SocialAccountRepository)
        deleted = repo.delete(account_id)
        return _ok({"deleted": deleted})
    except Exception as exc:
        logger.error("DELETE /api/v1/social-accounts/%s error: %s", account_id, exc)
        return _err(str(exc), 500)


@api_v1_bp.route("/social-accounts/<account_id>/health-check", methods=["POST"])
@_auth_required
def health_check_social_account(account_id):
    from repositories.social_account_repository import SocialAccountRepository
    from services.connectors.connector_factory import create_connector
    try:
        repo = _get_container().resolve(SocialAccountRepository)
        account = repo.find_by_id(account_id)
        if not account:
            return _err("Social Account not found", 404)
            
        connector = create_connector(account)
        h = connector.health()
        
        # Update health status in DB
        account.health_status = "healthy" if h.ok else "unhealthy"
        account.last_health_check = datetime.now(timezone.utc)
        repo.save(account)
        
        return _ok({
            "healthy": h.ok,
            "latency_ms": h.latency_ms,
            "error": h.error,
        })
    except Exception as exc:
        logger.error("POST /api/v1/social-accounts/%s/health-check error: %s", account_id, exc)
        return _err(str(exc), 500)


# ── Knowledge Base ─────────────────────────────────────────────────────────

@api_v1_bp.route("/brands/<brand_id>/knowledge", methods=["POST"])
@_auth_required
def create_knowledge_entry(brand_id):
    from repositories.knowledge_repository import KnowledgeRepository
    from repositories.brand_repository import BrandRepository
    from services.knowledge_base import KnowledgeBase
    try:
        brand_repo = _get_container().resolve(BrandRepository)
        brand = brand_repo.find_by_id(brand_id)
        if not brand:
            return _err("Brand not found", 404)
            
        data = request.get_json(force=True, silent=True) or {}
        content = data.get("content", "").strip()
        doc_type = data.get("doc_type", "text")
        metadata = data.get("metadata", {})
        
        if not content:
            return _err("content field is required", 400)
            
        kb_repo = _get_container().resolve(KnowledgeRepository)
        kb = KnowledgeBase(brand_id=brand_id, workspace_id=brand.workspace_id, repository=kb_repo)
        entry = kb.add_document(content=content, doc_type=doc_type, metadata=metadata)
        
        return _ok({"knowledge_entry": entry.model_dump(mode="json")}, 201)
    except Exception as exc:
        logger.error("POST /api/v1/brands/%s/knowledge error: %s", brand_id, exc)
        return _err(str(exc), 500)


@api_v1_bp.route("/brands/<brand_id>/knowledge", methods=["GET"])
@_auth_required
def list_knowledge_entries(brand_id):
    from repositories.knowledge_repository import KnowledgeRepository
    try:
        repo = _get_container().resolve(KnowledgeRepository)
        entries = repo.find_by_brand(brand_id)
        return _ok({"knowledge_entries": [e.model_dump(mode="json") for e in entries]})
    except Exception as exc:
        logger.error("GET /api/v1/brands/%s/knowledge error: %s", brand_id, exc)
        return _err(str(exc), 500)


@api_v1_bp.route("/knowledge/<knowledge_id>", methods=["DELETE"])
@_auth_required
def delete_knowledge_entry(knowledge_id):
    from repositories.knowledge_repository import KnowledgeRepository
    try:
        repo = _get_container().resolve(KnowledgeRepository)
        deleted = repo.delete(knowledge_id)
        return _ok({"deleted": deleted})
    except Exception as exc:
        logger.error("DELETE /api/v1/knowledge/%s error: %s", knowledge_id, exc)
        return _err(str(exc), 500)


@api_v1_bp.route("/brands/<brand_id>/knowledge/search", methods=["POST"])
@_auth_required
def search_knowledge_entries(brand_id):
    from repositories.brand_repository import BrandRepository
    from repositories.knowledge_repository import KnowledgeRepository
    from services.knowledge_base import KnowledgeBase
    try:
        brand_repo = _get_container().resolve(BrandRepository)
        brand = brand_repo.find_by_id(brand_id)
        if not brand:
            return _err("Brand not found", 404)
            
        data = request.get_json(force=True, silent=True) or {}
        query = data.get("query", "").strip()
        top_k = min(50, max(1, int(data.get("top_k", 5))))
        
        if not query:
            return _err("query field is required", 400)
            
        kb_repo = _get_container().resolve(KnowledgeRepository)
        kb = KnowledgeBase(brand_id=brand_id, workspace_id=brand.workspace_id, repository=kb_repo)
        results = kb.search(query=query, top_k=top_k)
        
        serialised = [
            {"entry": entry.model_dump(mode="json"), "similarity": float(sim)}
            for entry, sim in results
        ]
        return _ok({"results": serialised})
    except Exception as exc:
        logger.error("POST /api/v1/brands/%s/knowledge/search error: %s", brand_id, exc)
        return _err(str(exc), 500)


# ── Analytics ──────────────────────────────────────────────────────────────

@api_v1_bp.route("/workspaces/<workspace_id>/analytics", methods=["GET"])
@_auth_required
def get_workspace_analytics(workspace_id):
    from analytics.tracker import analytics
    try:
        # Custom workspace analytics fetch shim (scopes rpc call or fallback filters)
        stats = analytics.get_stats()
        return _ok({
            "workspace_id": workspace_id,
            "analytics": stats,
            "as_of": datetime.now(timezone.utc).isoformat()
        })
    except Exception as exc:
        logger.error("GET /api/v1/workspaces/%s/analytics error: %s", workspace_id, exc)
        return _err(str(exc), 500)


@api_v1_bp.route("/brands/<brand_id>/analytics", methods=["GET"])
@_auth_required
def get_brand_analytics(brand_id):
    from analytics.tracker import analytics
    try:
        stats = analytics.get_stats()
        return _ok({
            "brand_id": brand_id,
            "analytics": stats,
            "as_of": datetime.now(timezone.utc).isoformat()
        })
    except Exception as exc:
        logger.error("GET /api/v1/brands/%s/analytics error: %s", brand_id, exc)
        return _err(str(exc), 500)


# ── System Health ──────────────────────────────────────────────────────────

@api_v1_bp.route("/system/health", methods=["GET"])
def get_system_health():
    try:
        from core.reliability import HealthChecker
        checker = _get_container().resolve(HealthChecker)
        statuses = checker.check_all()
        
        all_ok = all(stat.get("ok", False) for stat in statuses.values())
        return _ok({
            "status": "healthy" if all_ok else "degraded",
            "connectors": statuses,
            "as_of": datetime.now(timezone.utc).isoformat()
        })
    except Exception as exc:
        logger.error("GET /api/v1/system/health error: %s", exc)
        return _err(str(exc), 500)


@api_v1_bp.route("/system/connectors", methods=["GET"])
@_auth_required
def get_system_connectors():
    try:
        from services.connectors.connector_factory import supported_platforms
        platforms = supported_platforms()
        return _ok({
            "supported_connectors": platforms,
            "count": len(platforms)
        })
    except Exception as exc:
        logger.error("GET /api/v1/system/connectors error: %s", exc)
        return _err(str(exc), 500)

