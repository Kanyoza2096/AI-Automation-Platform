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
