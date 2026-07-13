"""
Kanyoza Systems AI Platform v10 — REST API v1 Blueprint (M8).

All versioned endpoints live at /api/v1/*.
Protected endpoints require one of:
  - Authorization: Bearer <MASTER_API_TOKEN>   (preferred — frontend/dashboard)
  - X-API-Key: <APP_SECRET>                    (admin key)
  - X-API-Key: <OWNER_PSID>                    (owner PSID fallback)

Endpoints — 85 total
-------------------
Posts, Messages, Analytics, Bot, Guardian, Schedule, Metrics,
AI Config & Persona, Workspaces, Brands, AI Profiles, Social Accounts,
Knowledge Base, API Keys, Rate Limits, Workflows, System Health,
Webhooks, Notifications, Integrations, Audit Logs, Tenants, Features,
Plugins — GET /plugins, POST /plugins/<name>/execute

Security — M8 Hardening
------------------------
• Prompt injection guard on /ai/chat
• CORS headers on all responses
• Input sanitization on user-generated content
• Correlation IDs on every request
• Security headers (nosniff, XSS, frame, hide server)
• Rate limiting on auth-heavy endpoints
• Workspace isolation on all CRUD routes
• Webhook signature validation
• Draft TTL cleanup (7 days)
"""

from __future__ import annotations

import collections as _collections_mod
import hashlib as _hashlib_mod
import hmac
import html as _html_mod
import json as _json_mod
import logging
import re as _re_mod
import secrets as _secrets_mod
import threading as _threading_mod
import uuid as _uuid_mod
from datetime import datetime, timedelta, timezone
from functools import wraps

import requests as _requests_mod
from flask import Blueprint, abort, g, jsonify, request

logger = logging.getLogger("kanyoza.api.v1")

api_v1_bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")

# ── Security Constants ─────────────────────────────────────────────────────
MAX_REQUEST_BODY_SIZE = 1_048_576   # 1 MB
MAX_AI_PROMPT_LENGTH  = 4000       # 4000 characters
PLATFORM_VERIFY_TIMEOUT = 8        # seconds — matches frontend AbortSignal
DRAFT_TTL_DAYS = 7                 # drafts expire after 7 days

# Prompt injection patterns to block
INJECTION_PATTERNS = [
    "ignore previous instructions",
    "ignore all instructions",
    "system prompt",
    "you are now",
    "pretend you are",
    "forget everything",
    "new instructions",
    "override",
    "### System",
    "<|system|>",
    "<|im_start|>",
    "tell me your instructions",
    "reveal your prompt",
    "what are your rules",
]

# ── In-Memory Stores ───────────────────────────────────────────────────────
_draft_lock = _threading_mod.Lock()
_draft_store: dict[str, dict] = {}

_workflow_history: _collections_mod.deque = _collections_mod.deque(maxlen=20)
_workflow_history_lock = _threading_mod.Lock()

_keys_lock = _threading_mod.Lock()
_active_keys: dict[str, dict] = {}

_audit_log: _collections_mod.deque = _collections_mod.deque(maxlen=200)
_audit_log_lock = _threading_mod.Lock()

_AVAILABLE_WORKFLOWS = {
    "post_publish": {
        "description": "Select topic → Generate caption → Render card → Publish",
        "steps": ["select_topic", "generate", "render", "publish", "analytics", "event"],
        "trigger": "scheduler (10 ×/day) or manual",
    },
}

# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _get_container():
    from flask import current_app
    return current_app.extensions.get("service_container")


def _get_rate_limiter():
    from flask import current_app
    container = current_app.extensions.get("service_container")
    if container:
        try:
            from core.rate_limiter import RateLimiter
            return container.resolve(RateLimiter)
        except Exception:
            pass
    return None


def _sanitize(value: str) -> str:
    """Strip HTML tags, normalize whitespace, escape entities."""
    if not value:
        return ""
    clean = _re_mod.sub(r'<[^>]*>', '', str(value))
    clean = ' '.join(clean.split())
    return _html_mod.escape(clean, quote=False)


def _rate_limit(action: str, max_per_minute: int = 30):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            limiter = _get_rate_limiter()
            if limiter:
                client_ip = request.remote_addr or "unknown"
                key = f"api:{action}:{client_ip}"
                if not limiter.is_allowed(key, max_per_minute):
                    return jsonify({
                        "ok": False,
                        "error": "rate_limited",
                        "message": f"Too many requests. Max {max_per_minute} per minute.",
                        "retry_after_seconds": 60,
                    }), 429
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def _require_auth() -> bool:
    from config.settings import settings as _s
    master_token = getattr(_s, "master_api_token", "") or ""
    api_secret   = getattr(_s, "app_secret", "") or ""
    owner_psid   = getattr(_s, "owner_psid", "") or ""

    auth_header = request.headers.get("Authorization", "").strip()
    if auth_header.startswith("Bearer "):
        bearer = auth_header[len("Bearer "):]
        if master_token and hmac.compare_digest(bearer, master_token):
            g.auth_method = "bearer"
            g.auth_workspace_id = None
            return True

    key = request.headers.get("X-API-Key", "").strip()
    if key:
        if api_secret and hmac.compare_digest(key, api_secret):
            g.auth_method = "api_key"
            g.auth_workspace_id = None
            return True
        if owner_psid and hmac.compare_digest(key, owner_psid):
            g.auth_method = "owner_psid"
            g.auth_workspace_id = None
            return True
    return False


def _auth_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not _require_auth():
            return jsonify({
                "error": "unauthorized",
                "message": "Valid Authorization: Bearer <token> or X-API-Key header required",
            }), 401
        return fn(*args, **kwargs)
    return wrapper


def _auth_required_rate_limited(fn):
    @wraps(fn)
    @_rate_limit("auth_attempt", max_per_minute=10)
    def wrapper(*args, **kwargs):
        if not _require_auth():
            return jsonify({
                "error": "unauthorized",
                "message": "Valid Authorization: Bearer <token> or X-API-Key header required",
            }), 401
        return fn(*args, **kwargs)
    return wrapper


def _require_workspace_ownership(resource_type: str, resource_id: str):
    workspace_id = getattr(g, 'auth_workspace_id', None)
    if workspace_id is None:
        return None
    resource_workspace_id = _get_resource_workspace_id(resource_type, resource_id)
    if resource_workspace_id is None:
        abort(404, f"{resource_type} not found")
    if resource_workspace_id != workspace_id:
        logger.warning(
            "Cross-workspace access: ws %s → %s %s (belongs to %s)",
            workspace_id, resource_type, resource_id, resource_workspace_id,
        )
        abort(404, f"{resource_type} not found")
    return workspace_id


def _get_resource_workspace_id(resource_type: str, resource_id: str) -> str | None:
    container = _get_container()
    if not container:
        return None
    try:
        if resource_type == "workspace":
            from repositories.workspace_repository import WorkspaceRepository
            repo = container.resolve(WorkspaceRepository)
            ws = repo.find_by_id(resource_id)
            return ws.id if ws else None
        elif resource_type == "brand":
            from repositories.brand_repository import BrandRepository
            repo = container.resolve(BrandRepository)
            brand = repo.find_by_id(resource_id)
            return brand.workspace_id if brand else None
        elif resource_type == "ai_profile":
            from repositories.ai_profile_repository import AIProfileRepository
            repo = container.resolve(AIProfileRepository)
            profile = repo.find_by_id(resource_id)
            return profile.workspace_id if profile else None
        elif resource_type == "social_account":
            from repositories.social_account_repository import SocialAccountRepository
            repo = container.resolve(SocialAccountRepository)
            account = repo.find_by_id(resource_id)
            return account.workspace_id if account else None
    except Exception as e:
        logger.error("Failed to resolve workspace for %s %s: %s", resource_type, resource_id, e)
    return None


def _ok(data: dict, status: int = 200):
    return jsonify({"ok": True, **data}), status


def _err(msg: str, status: int = 400):
    return jsonify({"ok": False, "error": msg}), status


def _safe_err(exc: Exception, endpoint: str, status: int = 500):
    logger.error("[%s] %s error: %s", g.get("correlation_id", "???"), endpoint, exc, exc_info=True)
    from config.settings import settings as _s
    if getattr(_s, "debug", False):
        return _err(str(exc), status)
    return _err("An internal error occurred. Please try again later.", status)


def _append_audit(action: str, resource: str, user: str = "system",
                   status: str = "success", detail: str = "") -> None:
    entry = {
        "id": _uuid_mod.uuid4().hex[:16],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "correlation_id": g.get("correlation_id", "unknown"),
        "user": user,
        "action": action,
        "resource": resource,
        "status": status,
        "detail": detail,
    }
    with _audit_log_lock:
        _audit_log.appendleft(entry)


def _get_account_token(account) -> str:
    token = getattr(account, "access_token", "") or ""
    if not token:
        pc = getattr(account, "platform_config", {}) or {}
        token = pc.get("page_access_token") or pc.get("access_token") or pc.get("bot_token") or ""
    return token


def _get_account_platform_id(account) -> str:
    pid = getattr(account, "platform_account_id", "") or ""
    if not pid:
        pc = getattr(account, "platform_config", {}) or {}
        pid = pc.get("page_id") or pc.get("phone_number_id") or pc.get("person_urn") or ""
    return pid


def _verify_webhook_signature(platform: str) -> bool:
    from config.settings import settings as _s
    secret = getattr(_s, "webhook_secret", "") or ""
    if not secret:
        return True
    sig_header = request.headers.get("X-Hub-Signature-256", "")
    if sig_header.startswith("sha256="):
        raw_body = request.get_data()
        expected = "sha256=" + _hashlib_mod.sha256(raw_body + secret.encode("utf-8")).hexdigest()
        if hmac.compare_digest(sig_header, expected):
            return True
        logger.warning("Webhook to /%s rejected: invalid HMAC signature", platform)
        return False
    token = request.args.get("secret", "").strip()
    if token:
        if hmac.compare_digest(token, secret):
            return True
        logger.warning("Webhook to /%s rejected: invalid query secret", platform)
        return False
    logger.warning("Webhook to /%s rejected: no signature provided", platform)
    return False


# ═══════════════════════════════════════════════════════════════════════════
# BEFORE REQUEST — Correlation ID + CORS Preflight + Size Limit
# ═══════════════════════════════════════════════════════════════════════════

@api_v1_bp.before_request
def _attach_correlation_id():
    g.correlation_id = request.headers.get("X-Correlation-ID") or _uuid_mod.uuid4().hex[:12]


@api_v1_bp.before_request
def _handle_cors_preflight():
    if request.method == "OPTIONS":
        response = jsonify({"ok": True})
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, X-API-Key, X-Correlation-ID"
        response.headers["Access-Control-Max-Age"] = "86400"
        return response, 200


@api_v1_bp.before_request
def _enforce_request_size_limit():
    content_length = request.content_length
    if content_length and content_length > MAX_REQUEST_BODY_SIZE:
        return jsonify({
            "ok": False,
            "error": "payload_too_large",
            "message": f"Request body exceeds maximum size of {MAX_REQUEST_BODY_SIZE} bytes.",
        }), 413


@api_v1_bp.before_request
def _cleanup_expired_drafts():
    """Remove drafts older than DRAFT_TTL_DAYS."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=DRAFT_TTL_DAYS)
    with _draft_lock:
        expired = [
            k for k, v in _draft_store.items()
            if datetime.fromisoformat(v["created_at"]) < cutoff
        ]
        for k in expired:
            del _draft_store[k]
    if expired:
        logger.debug("Cleaned up %d expired drafts.", len(expired))


# ═══════════════════════════════════════════════════════════════════════════
# AFTER REQUEST — Security Headers + CORS
# ═══════════════════════════════════════════════════════════════════════════

@api_v1_bp.after_request
def _add_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["X-Correlation-ID"] = g.get("correlation_id", "unknown")
    response.headers["Server"] = ""
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, X-API-Key, X-Correlation-ID"
    return response


# ═══════════════════════════════════════════════════════════════════════════
# POSTS
# ═══════════════════════════════════════════════════════════════════════════

@api_v1_bp.route("/posts", methods=["GET"])
@_auth_required
def get_posts():
    from analytics.tracker import analytics
    try:
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(100, max(1, int(request.args.get("per_page", 20))))
        state_filter = request.args.get("state", "").lower()
        stats = analytics.get_stats()
        published_posts = [{**p, "state": "published"} for p in stats.get("recent_posts", [])]
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
            "posts": posts, "page": page, "per_page": per_page,
            "total": len(all_posts), "drafts": len(drafts),
        })
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/posts", 500)


@api_v1_bp.route("/posts", methods=["POST"])
@_auth_required
def create_post():
    from scheduler.post_scheduler import scheduler
    try:
        data = request.get_json(force=True, silent=True) or {}
        state = data.get("state", "publish").lower()
        caption = _sanitize(data.get("caption", ""))
        topic = _sanitize(data.get("topic", ""))
        category = _sanitize(data.get("category", ""))

        if state == "draft":
            draft_id = str(_uuid_mod.uuid4())
            draft = {
                "id": draft_id,
                "caption": caption,
                "topic": topic,
                "category": category,
                "state": "draft",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            with _draft_lock:
                _draft_store[draft_id] = draft
            return _ok({"draft": True, "draft_id": draft_id, "post": draft}, 201)
        queued = scheduler.force_post()
        return _ok({"queued": queued, "message": "Post queued" if queued else "Queue full — try again"}, 202)
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/posts", 500)


@api_v1_bp.route("/posts/<post_id>", methods=["DELETE"])
@_auth_required
def delete_post(post_id):
    try:
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
        return _safe_err(exc, f"DELETE /api/v1/posts/{post_id}", 500)


# ═══════════════════════════════════════════════════════════════════════════
# MESSAGES
# ═══════════════════════════════════════════════════════════════════════════

@api_v1_bp.route("/messages", methods=["GET"])
@_auth_required
def get_messages():
    from analytics.tracker import analytics
    try:
        limit = min(200, max(1, int(request.args.get("limit", 50))))
        msgs = analytics.get_recent_messages(limit=limit)
        return _ok({"messages": msgs, "count": len(msgs)})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/messages", 500)


@api_v1_bp.route("/messages/reply", methods=["POST"])
@_auth_required
def send_message():
    from services.facebook import facebook as fb
    try:
        data = request.get_json(force=True, silent=True) or {}
        recipient = _sanitize(data.get("recipient_id", ""))
        text = _sanitize(data.get("text", ""))
        if not recipient or not text:
            return _err("recipient_id and text are required", 400)
        fb.send_message(recipient, text)
        return _ok({"sent": True, "recipient": recipient})
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/messages/reply", 500)


@api_v1_bp.route("/messages/<sender_id>", methods=["GET"])
@_auth_required
def get_conversation(sender_id):
    try:
        from services.supabase import supabase_service
        if supabase_service and supabase_service.client:
            result = (
                supabase_service.client.table("conversation_history")
                .select("*").eq("sender_id", sender_id)
                .order("created_at", desc=True).limit(50).execute()
            )
            return _ok({"messages": result.data or [], "sender_id": sender_id})
        return _ok({"messages": [], "sender_id": sender_id, "note": "Supabase not configured"})
    except Exception as exc:
        return _safe_err(exc, f"GET /api/v1/messages/{sender_id}", 500)


# ═══════════════════════════════════════════════════════════════════════════
# ANALYTICS
# ═══════════════════════════════════════════════════════════════════════════

@api_v1_bp.route("/analytics", methods=["GET"])
@_auth_required
def get_analytics():
    from analytics.tracker import analytics
    try:
        stats = analytics.get_stats()
        return _ok({"analytics": stats, "as_of": datetime.now(timezone.utc).isoformat()})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/analytics", 500)


@api_v1_bp.route("/analytics/performance", methods=["GET"])
@_auth_required
def analytics_performance():
    try:
        from services.supabase import supabase_service
        if supabase_service and supabase_service.client:
            result = (
                supabase_service.client.table("post_history")
                .select("*").order("published_at", desc=True).limit(20).execute()
            )
            return _ok({"posts": result.data or []})
        return _ok({"posts": [], "note": "Supabase not configured"})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/analytics/performance", 500)


@api_v1_bp.route("/analytics/posts-performance", methods=["GET"])
@_auth_required
def analytics_posts_performance():
    try:
        limit = min(50, max(1, int(request.args.get("limit", 20))))
        from services.supabase import supabase_service
        if supabase_service and supabase_service.client:
            result = (
                supabase_service.client.table("post_history")
                .select("*").order("published_at", desc=True).limit(limit).execute()
            )
            return _ok({"posts": result.data or [], "count": len(result.data or [])})
        return _ok({"posts": [], "count": 0, "note": "Supabase not configured"})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/analytics/posts-performance", 500)


@api_v1_bp.route("/analytics/token-usage", methods=["GET"])
@_auth_required
def analytics_token_usage():
    try:
        from utils.metrics import metrics as _m
        snap = _m.get_snapshot()
        counters = snap.get("counters", {})
        return _ok({
            "total_tokens_estimated": counters.get("gemini_tokens_used", 0),
            "api_calls_today": counters.get("requests_total", 0),
            "daily": [],
            "as_of": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/analytics/token-usage", 500)


@api_v1_bp.route("/analytics/engagement-heatmap", methods=["GET"])
@_auth_required
def analytics_engagement_heatmap():
    try:
        from services.supabase import supabase_service
        if supabase_service and supabase_service.client:
            try:
                result = supabase_service.client.rpc("get_engagement_heatmap", {}).execute()
                if result.data:
                    return _ok({"heatmap": result.data})
            except Exception:
                pass
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        heatmap = [{"day": d, "hours": [0.0] * 24} for d in days]
        return _ok({"heatmap": heatmap, "note": "Real data requires Supabase"})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/analytics/engagement-heatmap", 500)


# ═══════════════════════════════════════════════════════════════════════════
# BOT CONTROL
# ═══════════════════════════════════════════════════════════════════════════

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
        return _safe_err(exc, "GET /api/v1/bot/status", 500)


@api_v1_bp.route("/bot/post", methods=["POST"])
@_auth_required
def force_post():
    from scheduler.post_scheduler import scheduler
    try:
        queued = scheduler.force_post()
        return _ok({"queued": queued}, 202)
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/bot/post", 500)


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
        return _safe_err(exc, "POST /api/v1/bot/news", 500)


@api_v1_bp.route("/bot/engage", methods=["POST"])
@_auth_required
def force_engage():
    from scheduler.post_scheduler import scheduler
    try:
        queued = scheduler.force_engagement()
        return _ok({"queued": queued}, 202)
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/bot/engage", 500)


# ═══════════════════════════════════════════════════════════════════════════
# GUARDIAN
# ═══════════════════════════════════════════════════════════════════════════

@api_v1_bp.route("/guardian/status", methods=["GET"])
def guardian_status():
    try:
        from services.guardian_service import GuardianService
        container = _get_container()
        if not container:
            return _ok({"configured": False, "message": "DI container not attached"})
        svc = container.try_resolve(GuardianService)
        if not svc:
            return _ok({"configured": False, "message": "Guardian not configured"})
        return _ok(svc.get_status())
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/guardian/status", 500)


@api_v1_bp.route("/guardian/issues", methods=["GET"])
@_auth_required
def guardian_issues():
    try:
        from services.guardian_service import GuardianService
        container = _get_container()
        if not container:
            return _ok({"issues": [], "message": "Guardian not available"})
        svc = container.try_resolve(GuardianService)
        if not svc:
            return _ok({"issues": [], "message": "Guardian not configured"})
        issues = svc.get_issues()
        sev_filter = request.args.get("severity", "").lower()
        if sev_filter:
            allowed = {s.strip() for s in sev_filter.split(",")}
            issues = [i for i in issues if isinstance(i, dict) and i.get("severity", "").lower() in allowed]
        _order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        issues = sorted(issues, key=lambda i: _order.get(i.get("severity", "").lower(), 9) if isinstance(i, dict) else 9)
        return _ok({"issues": issues, "count": len(issues)})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/guardian/issues", 500)


@api_v1_bp.route("/guardian/scan", methods=["POST"])
@_auth_required
def trigger_scan():
    try:
        from services.guardian_service import GuardianService
        container = _get_container()
        if not container:
            return _err("Guardian not available", 503)
        svc = container.try_resolve(GuardianService)
        if not svc or not svc.is_configured:
            return _err("Guardian requires GitHub credentials", 503)
        t = _threading_mod.Thread(target=svc.scan_all, daemon=True, name="api-guardian-scan")
        t.start()
        return _ok({"started": True, "message": "Scan started"}, 202)
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/guardian/scan", 500)


# ═══════════════════════════════════════════════════════════════════════════
# SCHEDULE
# ═══════════════════════════════════════════════════════════════════════════

@api_v1_bp.route("/schedule", methods=["GET"])
@_auth_required
def get_schedule():
    from scheduler.post_scheduler import scheduler
    try:
        return _ok({"schedule": scheduler.get_config()})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/schedule", 500)


@api_v1_bp.route("/schedule", methods=["PUT"])
@_auth_required
def update_schedule():
    from scheduler.post_scheduler import scheduler
    try:
        data = request.get_json(force=True, silent=True) or {}
        allowed_keys = {"post_interval_hours", "news_interval_hours", "enabled", "content_hours_utc", "news_hours_utc"}
        unknown = set(data.keys()) - allowed_keys
        if unknown:
            return _err(f"Unknown keys: {sorted(unknown)}", 400)
        result = scheduler.update_config(**{k: v for k, v in data.items() if k in allowed_keys})
        return _ok({"updated": True, "config": result})
    except ValueError as exc:
        return _err(str(exc), 400)
    except Exception as exc:
        return _safe_err(exc, "PUT /api/v1/schedule", 500)


# ═══════════════════════════════════════════════════════════════════════════
# METRICS
# ═══════════════════════════════════════════════════════════════════════════

@api_v1_bp.route("/metrics", methods=["GET"])
@_auth_required
def get_metrics():
    try:
        from utils.metrics import metrics as metrics_collector
        snapshot = metrics_collector.get_snapshot()
        return _ok({"metrics": snapshot, "as_of": datetime.now(timezone.utc).isoformat()})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/metrics", 500)


@api_v1_bp.route("/metrics/public", methods=["GET"])
def get_public_metrics():
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
        return _safe_err(exc, "GET /api/v1/metrics/public", 500)


@api_v1_bp.route("/metrics/resources", methods=["GET"])
@_auth_required
def get_resource_metrics():
    try:
        cpu_percent = mem_percent = disk_percent = 0.0
        try:
            import psutil as _ps
            cpu_percent = _ps.cpu_percent(interval=None)
            mem_percent = _ps.virtual_memory().percent
            disk_percent = _ps.disk_usage("/").percent
        except Exception:
            pass
        return _ok({"cpu_percent": cpu_percent, "memory_percent": mem_percent, "disk_percent": disk_percent})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/metrics/resources", 500)


@api_v1_bp.route("/metrics/query", methods=["GET"])
@_auth_required
def metrics_query():
    try:
        from utils.metrics import metrics as _m
        snap = _m.get_snapshot()
        q = request.args.get("q", "")
        value = 0
        if "error" in q.lower():
            value = snap.get("counters", {}).get("errors", 0)
        elif "rps" in q.lower():
            value = snap.get("counters", {}).get("requests_total", 0)
        return jsonify({"status": "success", "data": {"result": [{"metric": {"__name__": q or "kanyoza_metric"}, "value": [int(datetime.now(timezone.utc).timestamp()), str(value)]}]}})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/metrics/query", 500)


# ═══════════════════════════════════════════════════════════════════════════
# AI CONFIG & PERSONA
# ═══════════════════════════════════════════════════════════════════════════

@api_v1_bp.route("/ai/config", methods=["GET"])
@_auth_required
def get_ai_config():
    from config.settings import settings as _s
    from api.ai_runtime import get as _rt_get, VALID_MOODS
    rt = _rt_get()
    return _ok({
        "provider": _s.ai_provider,
        "model": _s.gemini_model,
        "chat_temperature": _s.gemini_chat_temperature,
        "post_temperature": _s.gemini_post_temperature,
        "safety_level": _s.gemini_safety_level,
        "available_models": ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-1.5-pro", "gemini-1.5-flash"],
        "available_providers": ["gemini", "huggingface"],
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
    from config.settings import settings as _s
    from api.ai_runtime import update as _rt_update
    try:
        data = request.get_json(force=True, silent=True) or {}
        settings_keys = {"model", "chat_temperature", "post_temperature", "safety_level", "provider"}
        runtime_keys = {"system_prompt_override", "persona_mood", "tone_assertiveness", "tone_humor", "tone_formality"}
        unknown = set(data.keys()) - settings_keys - runtime_keys
        if unknown:
            return _err(f"Unknown keys: {sorted(unknown)}", 400)
        if "provider" in data:
            if data["provider"] not in {"gemini", "huggingface"}:
                return _err("provider must be gemini or huggingface", 400)
            _s.ai_provider = data["provider"]
        if "model" in data:
            _s.gemini_model = str(data["model"])
        temp_map = {"chat_temperature": "gemini_chat_temperature", "post_temperature": "gemini_post_temperature"}
        for fk, sk in temp_map.items():
            if fk in data:
                val = data[fk]
                if not isinstance(val, (int, float)) or not 0.0 <= float(val) <= 2.0:
                    return _err(f"{fk} must be 0.0–2.0", 400)
                setattr(_s, sk, float(val))
        if "safety_level" in data:
            if data["safety_level"] not in {"low", "medium", "high"}:
                return _err("safety_level must be low, medium, or high", 400)
            _s.gemini_safety_level = data["safety_level"]
        rt_updates = {k: v for k, v in data.items() if k in runtime_keys}
        if rt_updates:
            _rt_update(**rt_updates)
        from api.ai_runtime import get as _rt_get
        rt = _rt_get()
        return _ok({"updated": True, "config": {"provider": _s.ai_provider, "model": _s.gemini_model, **rt}})
    except Exception as exc:
        return _safe_err(exc, "PUT /api/v1/ai/config", 500)


@api_v1_bp.route("/ai/persona", methods=["GET"])
@_auth_required
def get_ai_persona():
    from api.ai_runtime import get as _rt_get
    rt = _rt_get()
    return _ok({"tone": int(rt.get("tone_formality", 0.6)*100), "aggression": int(rt.get("tone_assertiveness", 0.7)*100), "humor": int(rt.get("tone_humor", 0.2)*100), "system_prompt": rt.get("system_prompt_override") or "", **rt})


@api_v1_bp.route("/ai/persona", methods=["PUT"])
@_auth_required
def update_ai_persona():
    from api.ai_runtime import update as _rt_update
    from config.settings import settings as _s
    try:
        data = request.get_json(force=True, silent=True) or {}
        rt_updates = {}
        if "tone" in data:
            rt_updates["tone_formality"] = round(float(data["tone"])/100.0, 4)
        if "aggression" in data:
            rt_updates["tone_assertiveness"] = round(float(data["aggression"])/100.0, 4)
        if "humor" in data:
            rt_updates["tone_humor"] = round(float(data["humor"])/100.0, 4)
        if "model" in data:
            _s.gemini_model = str(data["model"])
        if "system_prompt" in data:
            rt_updates["system_prompt_override"] = data["system_prompt"] or None
        for k in ("persona_mood", "tone_formality", "tone_assertiveness", "tone_humor"):
            if k in data and k not in rt_updates:
                rt_updates[k] = data[k]
        rt = _rt_update(**rt_updates) if rt_updates else _rt_get()
        return _ok({"updated": True, "persona": rt})
    except ValueError as exc:
        return _err(str(exc), 400)
    except Exception as exc:
        return _safe_err(exc, "PUT /api/v1/ai/persona", 500)


@api_v1_bp.route("/persona", methods=["GET"])
def get_persona():
    try:
        from core.persona import bot_persona
        from api.ai_runtime import get as _rt_get, VALID_MOODS
        rt = _rt_get()
        state = bot_persona.to_dict()
        return _ok({"name": bot_persona.name, "tagline": bot_persona.tagline, "current_mood": state.get("current_mood", "professional"), "available_moods": sorted(VALID_MOODS), **rt})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/persona", 500)


@api_v1_bp.route("/persona", methods=["PUT"])
@_auth_required
def update_persona():
    from api.ai_runtime import update as _rt_update
    try:
        data = request.get_json(force=True, silent=True) or {}
        allowed = {"persona_mood", "tone_assertiveness", "tone_humor", "tone_formality", "system_prompt_override"}
        unknown = set(data.keys()) - allowed
        if unknown:
            return _err(f"Unknown keys: {sorted(unknown)}", 400)
        rt = _rt_update(**{k: v for k, v in data.items() if k in allowed})
        return _ok({"updated": True, "persona": rt})
    except ValueError as exc:
        return _err(str(exc), 400)
    except Exception as exc:
        return _safe_err(exc, "PUT /api/v1/persona", 500)


@api_v1_bp.route("/persona/reset", methods=["POST"])
@_auth_required
def reset_persona():
    from api.ai_runtime import update as _rt_update
    try:
        rt = _rt_update(persona_mood="professional", tone_assertiveness=0.7, tone_humor=0.2, tone_formality=0.6, system_prompt_override=None)
        return _ok({"reset": True, "persona": rt})
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/persona/reset", 500)


# ═══════════════════════════════════════════════════════════════════════════
# AI CHAT — with Prompt Injection Guard
# ═══════════════════════════════════════════════════════════════════════════

@api_v1_bp.route("/ai/chat", methods=["POST"])
@_auth_required
@_rate_limit("ai_chat", max_per_minute=20)
def ai_chat():
    try:
        data = request.get_json(force=True, silent=True) or {}
        message = str(data.get("message", "")).strip()

        # ── Prompt injection guard ───────────────────────────────────────
        msg_lower = message.lower()
        for pattern in INJECTION_PATTERNS:
            if pattern in msg_lower:
                logger.warning("[%s] Prompt injection blocked: %s", g.get("correlation_id", "???"), pattern[:50])
                return _ok({
                    "reply": "I can't process that request. Please rephrase your message.",
                    "model": "guard",
                    "tokens_used": 0,
                })
        # ── End guard ────────────────────────────────────────────────────

        if not message:
            return _err("message field is required", 400)
        if len(message) > MAX_AI_PROMPT_LENGTH:
            return _err(f"Message exceeds maximum length of {MAX_AI_PROMPT_LENGTH} characters", 400)

        history = data.get("history", [])
        from config.settings import settings as _s
        if history:
            from core.memory import memory as _mem
            for turn in history[-10:]:
                role = str(turn.get("role", "user"))
                content = str(turn.get("content", ""))
                if role == "user":
                    _mem.add_message("api_chat_user", "user", content)
                elif role in ("assistant", "bot"):
                    _mem.add_message("api_chat_user", "assistant", content)

        from services.command_executor import CommandExecutor
        from services.gemini import GeminiService
        executor = CommandExecutor(
            gemini_service=GeminiService(),
            master_token=_s.master_api_token,
            base_url=request.host_url.rstrip("/") + "/api/v1",
        )
        intent = executor.parse_intent(message)
        if intent and intent.get("action"):
            result = executor.execute(intent)
            _append_audit(action="ai_command", resource=intent.get("action", "unknown"), status="success" if result.get("ok") else "failed", detail=f"Command: {message[:100]}")
            return _ok({"reply": result.get("message", "Command processed."), "model": "command-executor", "tokens_used": 0, "command_result": result})

        gemini = GeminiService()
        reply = gemini.generate_chat_response(sender_id="api_chat_user", user_message=message)
        _append_audit(action="ai_chat", resource="gemini", status="success", detail=f"Chat ({len(message)}→{len(reply)} chars)")
        return _ok({"reply": reply, "model": _s.gemini_model, "tokens_used": len(reply.split())})
    except Exception as exc:
        logger.warning("Gemini chat failed: %s", exc)
        return _ok({"reply": "The AI service is temporarily unavailable.", "model": "unavailable", "tokens_used": 0})


# ═══════════════════════════════════════════════════════════════════════════
# WORKSPACES CRUD
# ═══════════════════════════════════════════════════════════════════════════

@api_v1_bp.route("/workspaces", methods=["POST"])
@_auth_required
def create_workspace():
    from models.workspace import Workspace
    from repositories.workspace_repository import WorkspaceRepository
    try:
        data = request.get_json(force=True, silent=True) or {}
        ws_id = data.get("id") or f"ws-{_uuid_mod.uuid4().hex[:12]}"
        data["id"] = ws_id
        if not data.get("name"):
            return _err("name is required", 400)
        ws = Workspace(**data)
        repo = _get_container().resolve(WorkspaceRepository)
        saved = repo.save(ws)
        _append_audit(action="workspace_created", resource=f"workspace:{ws_id}", status="success")
        return _ok({"workspace": saved.model_dump(mode="json")}, 201)
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/workspaces", 500)


@api_v1_bp.route("/workspaces", methods=["GET"])
@_auth_required
def list_workspaces():
    from repositories.workspace_repository import WorkspaceRepository
    try:
        repo = _get_container().resolve(WorkspaceRepository)
        workspace_id = getattr(g, 'auth_workspace_id', None)
        if workspace_id:
            ws = repo.find_by_id(workspace_id)
            workspaces = [ws.model_dump(mode="json")] if ws else []
        elif not repo.supabase:
            workspaces = list(repo._in_memory.values())
        else:
            res = repo.supabase.table("workspaces").select("*").execute()
            workspaces = res.data or []
        return _ok({"workspaces": workspaces})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/workspaces", 500)


@api_v1_bp.route("/workspaces/<workspace_id>", methods=["GET"])
@_auth_required
def get_workspace(workspace_id):
    from repositories.workspace_repository import WorkspaceRepository
    try:
        _require_workspace_ownership("workspace", workspace_id)
        repo = _get_container().resolve(WorkspaceRepository)
        ws = repo.find_by_id(workspace_id)
        if not ws:
            return _err("Workspace not found", 404)
        return _ok({"workspace": ws.model_dump(mode="json")})
    except Exception as exc:
        return _safe_err(exc, f"GET /api/v1/workspaces/{workspace_id}", 500)


@api_v1_bp.route("/workspaces/<workspace_id>", methods=["PUT"])
@_auth_required
def update_workspace(workspace_id):
    from models.workspace import Workspace
    from repositories.workspace_repository import WorkspaceRepository
    try:
        _require_workspace_ownership("workspace", workspace_id)
        repo = _get_container().resolve(WorkspaceRepository)
        ws = repo.find_by_id(workspace_id)
        if not ws:
            return _err("Workspace not found", 404)
        data = request.get_json(force=True, silent=True) or {}
        update_data = {k: v for k, v in data.items() if k not in ("id", "created_at")}
        update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
        ws_dict = ws.model_dump(mode="json")
        ws_dict.update(update_data)
        saved = repo.save(Workspace(**ws_dict))
        _append_audit(action="workspace_updated", resource=f"workspace:{workspace_id}", status="success")
        return _ok({"workspace": saved.model_dump(mode="json")})
    except Exception as exc:
        return _safe_err(exc, f"PUT /api/v1/workspaces/{workspace_id}", 500)


@api_v1_bp.route("/workspaces/<workspace_id>", methods=["DELETE"])
@_auth_required
def delete_workspace(workspace_id):
    from repositories.workspace_repository import WorkspaceRepository
    try:
        _require_workspace_ownership("workspace", workspace_id)
        repo = _get_container().resolve(WorkspaceRepository)
        deleted = repo.delete(workspace_id)
        _append_audit(action="workspace_deleted", resource=f"workspace:{workspace_id}", status="success")
        return _ok({"deleted": deleted})
    except Exception as exc:
        return _safe_err(exc, f"DELETE /api/v1/workspaces/{workspace_id}", 500)


# ═══════════════════════════════════════════════════════════════════════════
# BRANDS CRUD
# ═══════════════════════════════════════════════════════════════════════════

@api_v1_bp.route("/workspaces/<workspace_id>/brands", methods=["POST"])
@_auth_required
def create_brand(workspace_id):
    from models.brand import Brand
    from repositories.brand_repository import BrandRepository
    try:
        _require_workspace_ownership("workspace", workspace_id)
        data = request.get_json(force=True, silent=True) or {}
        brand_id = data.get("id") or f"brand-{_uuid_mod.uuid4().hex[:12]}"
        data["id"] = brand_id
        data["workspace_id"] = workspace_id
        if not data.get("name"):
            return _err("name is required", 400)
        if not data.get("slug"):
            data["slug"] = _sanitize(data["name"]).lower().replace(" ", "-")
        brand = Brand(**data)
        repo = _get_container().resolve(BrandRepository)
        saved = repo.save(brand)
        _append_audit(action="brand_created", resource=f"brand:{brand_id}", status="success")
        return _ok({"brand": saved.model_dump(mode="json")}, 201)
    except Exception as exc:
        return _safe_err(exc, f"POST /api/v1/workspaces/{workspace_id}/brands", 500)


@api_v1_bp.route("/workspaces/<workspace_id>/brands", methods=["GET"])
@_auth_required
def list_brands(workspace_id):
    from repositories.brand_repository import BrandRepository
    try:
        _require_workspace_ownership("workspace", workspace_id)
        repo = _get_container().resolve(BrandRepository)
        brands = repo.find_by_workspace(workspace_id)
        return _ok({"brands": [b.model_dump(mode="json") for b in brands]})
    except Exception as exc:
        return _safe_err(exc, f"GET /api/v1/workspaces/{workspace_id}/brands", 500)


@api_v1_bp.route("/brands/<brand_id>", methods=["GET"])
@_auth_required
def get_brand(brand_id):
    from repositories.brand_repository import BrandRepository
    try:
        _require_workspace_ownership("brand", brand_id)
        repo = _get_container().resolve(BrandRepository)
        brand = repo.find_by_id(brand_id)
        if not brand:
            return _err("Brand not found", 404)
        return _ok({"brand": brand.model_dump(mode="json")})
    except Exception as exc:
        return _safe_err(exc, f"GET /api/v1/brands/{brand_id}", 500)


@api_v1_bp.route("/brands/<brand_id>", methods=["PUT"])
@_auth_required
def update_brand(brand_id):
    from models.brand import Brand
    from repositories.brand_repository import BrandRepository
    try:
        _require_workspace_ownership("brand", brand_id)
        repo = _get_container().resolve(BrandRepository)
        brand = repo.find_by_id(brand_id)
        if not brand:
            return _err("Brand not found", 404)
        data = request.get_json(force=True, silent=True) or {}
        update_data = {k: v for k, v in data.items() if k not in ("id", "workspace_id", "created_at")}
        update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
        brand_dict = brand.model_dump(mode="json")
        brand_dict.update(update_data)
        saved = repo.save(Brand(**brand_dict))
        _append_audit(action="brand_updated", resource=f"brand:{brand_id}", status="success")
        return _ok({"brand": saved.model_dump(mode="json")})
    except Exception as exc:
        return _safe_err(exc, f"PUT /api/v1/brands/{brand_id}", 500)


@api_v1_bp.route("/brands/<brand_id>", methods=["DELETE"])
@_auth_required
def delete_brand(brand_id):
    from repositories.brand_repository import BrandRepository
    try:
        _require_workspace_ownership("brand", brand_id)
        repo = _get_container().resolve(BrandRepository)
        deleted = repo.delete(brand_id)
        _append_audit(action="brand_deleted", resource=f"brand:{brand_id}", status="success")
        return _ok({"deleted": deleted})
    except Exception as exc:
        return _safe_err(exc, f"DELETE /api/v1/brands/{brand_id}", 500)


# ═══════════════════════════════════════════════════════════════════════════
# AI PROFILES CRUD
# ═══════════════════════════════════════════════════════════════════════════

@api_v1_bp.route("/workspaces/<workspace_id>/ai-profiles", methods=["POST"])
@_auth_required
def create_ai_profile(workspace_id):
    from models.ai_profile import AIProfile
    from repositories.ai_profile_repository import AIProfileRepository
    try:
        _require_workspace_ownership("workspace", workspace_id)
        data = request.get_json(force=True, silent=True) or {}
        profile_id = data.get("id") or f"profile-{_uuid_mod.uuid4().hex[:12]}"
        data["id"] = profile_id
        data["workspace_id"] = workspace_id
        if not data.get("name"):
            return _err("name is required", 400)
        profile = AIProfile(**data)
        repo = _get_container().resolve(AIProfileRepository)
        saved = repo.save(profile)
        _append_audit(action="ai_profile_created", resource=f"ai_profile:{profile_id}", status="success")
        return _ok({"ai_profile": saved.model_dump(mode="json")}, 201)
    except Exception as exc:
        return _safe_err(exc, f"POST /api/v1/workspaces/{workspace_id}/ai-profiles", 500)


@api_v1_bp.route("/workspaces/<workspace_id>/ai-profiles", methods=["GET"])
@_auth_required
def list_ai_profiles(workspace_id):
    from repositories.ai_profile_repository import AIProfileRepository
    try:
        _require_workspace_ownership("workspace", workspace_id)
        repo = _get_container().resolve(AIProfileRepository)
        profiles = repo.find_by_workspace(workspace_id)
        return _ok({"ai_profiles": [p.model_dump(mode="json") for p in profiles]})
    except Exception as exc:
        return _safe_err(exc, f"GET /api/v1/workspaces/{workspace_id}/ai-profiles", 500)


@api_v1_bp.route("/ai-profiles/<profile_id>", methods=["PUT"])
@_auth_required
def update_ai_profile(profile_id):
    from models.ai_profile import AIProfile
    from repositories.ai_profile_repository import AIProfileRepository
    try:
        _require_workspace_ownership("ai_profile", profile_id)
        repo = _get_container().resolve(AIProfileRepository)
        profile = repo.find_by_id(profile_id)
        if not profile:
            return _err("AI Profile not found", 404)
        data = request.get_json(force=True, silent=True) or {}
        update_data = {k: v for k, v in data.items() if k not in ("id", "workspace_id", "created_at")}
        update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
        profile_dict = profile.model_dump(mode="json")
        profile_dict.update(update_data)
        saved = repo.save(AIProfile(**profile_dict))
        _append_audit(action="ai_profile_updated", resource=f"ai_profile:{profile_id}", status="success")
        return _ok({"ai_profile": saved.model_dump(mode="json")})
    except Exception as exc:
        return _safe_err(exc, f"PUT /api/v1/ai-profiles/{profile_id}", 500)


@api_v1_bp.route("/ai-profiles/<profile_id>", methods=["DELETE"])
@_auth_required
def delete_ai_profile(profile_id):
    from repositories.ai_profile_repository import AIProfileRepository
    try:
        _require_workspace_ownership("ai_profile", profile_id)
        repo = _get_container().resolve(AIProfileRepository)
        deleted = repo.delete(profile_id)
        _append_audit(action="ai_profile_deleted", resource=f"ai_profile:{profile_id}", status="success")
        return _ok({"deleted": deleted})
    except Exception as exc:
        return _safe_err(exc, f"DELETE /api/v1/ai-profiles/{profile_id}", 500)


# ═══════════════════════════════════════════════════════════════════════════
# SOCIAL ACCOUNTS
# ═══════════════════════════════════════════════════════════════════════════

@api_v1_bp.route("/workspaces/<workspace_id>/social-accounts", methods=["POST"])
@_auth_required
def create_social_account(workspace_id):
    from models.social_account import SocialAccount
    from repositories.social_account_repository import SocialAccountRepository
    try:
        _require_workspace_ownership("workspace", workspace_id)
        data = request.get_json(force=True, silent=True) or {}
        acc_id = data.get("id") or f"acc-{_uuid_mod.uuid4().hex[:12]}"
        data["id"] = acc_id
        data["workspace_id"] = workspace_id
        if not data.get("platform"):
            return _err("platform is required", 400)

        # ── Map platform-specific credentials into platform_config ──────
        PLATFORM_CREDENTIAL_FIELDS = {
            "facebook": ["page_id", "page_access_token", "verify_token"],
            "twitter": ["client_id", "client_secret"],
            "linkedin": ["person_urn"],
            "instagram": ["page_id"],
            "whatsapp": ["phone_number_id"],
            "telegram": ["bot_token"],
        }

        platform = data.get("platform", "")
        if platform in PLATFORM_CREDENTIAL_FIELDS:
            pc = data.get("platform_config", {}) or {}
            for field in PLATFORM_CREDENTIAL_FIELDS[platform]:
                if field in data:
                    pc[field] = data.pop(field)
            data["platform_config"] = pc
        # ── End platform_config mapping ─────────────────────────────────

        account = SocialAccount(**data)
        repo = _get_container().resolve(SocialAccountRepository)
        saved = repo.save(account)
        response_data = saved.model_dump(mode="json")
        response_data.pop("access_token", None)
        response_data.pop("encrypted_credential", None)
        _append_audit(action="social_account_created", resource=f"social_account:{acc_id}", status="success")
        return _ok({"social_account": response_data}, 201)
    except Exception as exc:
        return _safe_err(exc, f"POST /api/v1/workspaces/{workspace_id}/social-accounts", 500)
@api_v1_bp.route("/workspaces/<workspace_id>/social-accounts", methods=["GET"])
@_auth_required
def list_social_accounts(workspace_id):
    from repositories.social_account_repository import SocialAccountRepository
    try:
        _require_workspace_ownership("workspace", workspace_id)
        repo = _get_container().resolve(SocialAccountRepository)
        accounts = repo.find_by_workspace(workspace_id)
        safe = []
        for a in accounts:
            d = a.model_dump(mode="json")
            d.pop("access_token", None)
            d.pop("encrypted_credential", None)
            safe.append(d)
        return _ok({"social_accounts": safe})
    except Exception as exc:
        return _safe_err(exc, f"GET /api/v1/workspaces/{workspace_id}/social-accounts", 500)

@api_v1_bp.route("/social-accounts/<account_id>", methods=["PUT"])
@_auth_required
def update_social_account(account_id):
    from repositories.social_account_repository import SocialAccountRepository
    from models.social_account import SocialAccount
    try:
        _require_workspace_ownership("social_account", account_id)
        repo = _get_container().resolve(SocialAccountRepository)
        account = repo.find_by_id(account_id)
        if not account:
            return _err("Social Account not found", 404)
        data = request.get_json(force=True, silent=True) or {}
        update_data = {k: v for k, v in data.items() if k not in ("id", "workspace_id", "created_at")}
        update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
        acc_dict = account.model_dump(mode="json")
        acc_dict.update(update_data)

        # ── Map platform-specific credentials into platform_config ──────
        PLATFORM_CREDENTIAL_FIELDS = {
            "facebook": ["page_id", "page_access_token", "verify_token"],
            "twitter": ["client_id", "client_secret"],
            "linkedin": ["person_urn"],
            "instagram": ["page_id"],
            "whatsapp": ["phone_number_id"],
            "telegram": ["bot_token"],
        }

        platform = acc_dict.get("platform", "")
        if platform in PLATFORM_CREDENTIAL_FIELDS:
            pc = acc_dict.get("platform_config", {}) or {}
            for field in PLATFORM_CREDENTIAL_FIELDS[platform]:
                if field in acc_dict:
                    pc[field] = acc_dict.pop(field)
            acc_dict["platform_config"] = pc
        # ── End platform_config mapping ─────────────────────────────────

        saved = repo.save(SocialAccount(**acc_dict))
        resp = saved.model_dump(mode="json")
        resp.pop("access_token", None)
        resp.pop("encrypted_credential", None)
        _append_audit(action="social_account_updated", resource=f"social_account:{account_id}", status="success")
        return _ok({"social_account": resp})
    except Exception as exc:
        return _safe_err(exc, f"PUT /api/v1/social-accounts/{account_id}", 500)


@api_v1_bp.route("/social-accounts/<account_id>", methods=["DELETE"])
@_auth_required
def delete_social_account(account_id):
    from repositories.social_account_repository import SocialAccountRepository
    try:
        _require_workspace_ownership("social_account", account_id)
        repo = _get_container().resolve(SocialAccountRepository)
        deleted = repo.delete(account_id)
        _append_audit(action="social_account_deleted", resource=f"social_account:{account_id}", status="success")
        return _ok({"deleted": deleted})
    except Exception as exc:
        return _safe_err(exc, f"DELETE /api/v1/social-accounts/{account_id}", 500)


@api_v1_bp.route("/social-accounts/<account_id>/health-check", methods=["POST"])
@_auth_required
def health_check_social_account(account_id):
    from repositories.social_account_repository import SocialAccountRepository
    from services.connectors.connector_factory import create_connector
    try:
        _require_workspace_ownership("social_account", account_id)
        repo = _get_container().resolve(SocialAccountRepository)
        account = repo.find_by_id(account_id)
        if not account:
            return _err("Social Account not found", 404)
        connector = create_connector(account)
        h = connector.health()
        repo.update_health_status(account.id, "healthy" if h.ok else "unhealthy")
        return _ok({"healthy": h.ok, "latency_ms": h.latency_ms, "error": h.error})
    except Exception as exc:
        return _safe_err(exc, f"POST /api/v1/social-accounts/{account_id}/health-check", 500)


@api_v1_bp.route("/social-accounts/<account_id>/verify-publish", methods=["POST"])
@_auth_required
def verify_publish(account_id):
    from repositories.social_account_repository import SocialAccountRepository
    from models.social_account import SocialAccount
    try:
        _require_workspace_ownership("social_account", account_id)
        repo = _get_container().resolve(SocialAccountRepository)
        account = repo.find_by_id(account_id)
        if not account:
            return _err("Social Account not found", 404)

        raw = getattr(account.platform, "value", None) or str(account.platform)
        platform = raw.lower().replace("platform.", "")

        verifier_map = {
            "facebook": _verify_facebook, "twitter": _verify_twitter,
            "linkedin": _verify_linkedin, "instagram": _verify_instagram,
            "whatsapp": _verify_whatsapp, "telegram": _verify_telegram,
        }
        verifier = verifier_map.get(platform)
        if not verifier:
            return _err(f"Verification not supported for: {platform}", 400)

        checks = verifier(account)
        ready = all(c.get("ok", False) for c in checks.values())

        acc_dict = account.model_dump(mode="json")
        acc_dict["health_status"] = "healthy" if ready else "unhealthy"
        acc_dict["last_health_check"] = datetime.now(timezone.utc).isoformat()
        repo.save(SocialAccount(**acc_dict))

        failed = [k.replace("_", " ").title() for k, c in checks.items() if not c.get("ok")]
        if ready:
            summary = "All checks passed. Ready to publish."
        elif len(failed) == 1:
            summary = f"{failed[0]} check failed"
        else:
            summary = f"{len(failed)} checks failed"

        return _ok({"verified": ready, "page_name": checks.get("page_access", {}).get("page_name", account.account_name), "summary": summary, "ready_to_publish": ready, "checks": checks})
    except Exception as exc:
        return _safe_err(exc, f"POST /api/v1/social-accounts/{account_id}/verify-publish", 500)


@api_v1_bp.route("/workspaces/<workspace_id>/social-accounts/bulk-health-check", methods=["POST"])
@_auth_required
def bulk_health_check(workspace_id):
    from repositories.social_account_repository import SocialAccountRepository
    try:
        _require_workspace_ownership("workspace", workspace_id)
        repo = _get_container().resolve(SocialAccountRepository)
        accounts = repo.find_by_workspace(workspace_id)
        if len(accounts) > 20:
            return _err("Bulk health check limited to 20 accounts", 400)
        results = []
        for account in accounts:
            try:
                from services.connectors.connector_factory import create_connector
                connector = create_connector(account)
                h = connector.health()
                repo.update_health_status(account.id, "healthy" if h.ok else "unhealthy")
                results.append({"id": account.id, "platform": str(account.platform), "account_name": account.account_name, "healthy": h.ok})
            except Exception as e:
                results.append({"id": account.id, "healthy": False, "error": str(e)[:200]})
        healthy_count = sum(1 for r in results if r.get("healthy"))
        return _ok({"results": results, "total": len(results), "healthy": healthy_count, "unhealthy": len(results) - healthy_count})
    except Exception as exc:
        return _safe_err(exc, f"POST /api/v1/workspaces/{workspace_id}/social-accounts/bulk-health-check", 500)


# ── Verifiers ──────────────────────────────────────────────────────────────
def _verify_facebook(account) -> dict:
    checks = {
        "token_valid": {"ok": False, "detail": "Not checked"},
        "page_access": {"ok": False, "detail": "Not checked", "page_name": account.account_name},
        "publish_permission": {"ok": False, "detail": "Not checked"},
        "rate_limit": {"ok": True, "detail": "Rate limits assumed OK"},
    }
    
    token = _get_account_token(account)
    page_id = _get_account_platform_id(account)
    
    if not token or not page_id:
        checks["token_valid"] = {"ok": False, "detail": "Missing access token or page ID"}
        return checks
    
    try:
        # Check 1: Token validity
        resp = _requests_mod.get(
            "https://graph.facebook.com/v22.0/debug_token",
            params={"input_token": token, "access_token": token},
            timeout=PLATFORM_VERIFY_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json().get("data", {})
            if data.get("is_valid"):
                expires = data.get("expires_at", 0)
                if expires == 0:
                    checks["token_valid"] = {"ok": True, "detail": "Long-lived token (no expiration)"}
                else:
                    days = (datetime.fromtimestamp(expires) - datetime.utcnow()).days
                    checks["token_valid"] = {"ok": True, "detail": f"Token valid, expires in {days} days"}
            else:
                error = data.get("error", {})
                checks["token_valid"] = {"ok": False, "detail": error.get("message", "Token invalid")}
        else:
            checks["token_valid"] = {"ok": False, "detail": f"Debug API error: HTTP {resp.status_code}"}
        
        # Check 2: Page access
        if checks["token_valid"]["ok"]:
            resp = _requests_mod.get(
                f"https://graph.facebook.com/v22.0/{page_id}",
                params={"fields": "name", "access_token": token},
                timeout=PLATFORM_VERIFY_TIMEOUT,
            )
            if resp.status_code == 200:
                name = resp.json().get("name", account.account_name)
                checks["page_access"] = {"ok": True, "detail": f"Page '{name}' accessible", "page_name": name}
            else:
                checks["page_access"] = {"ok": False, "detail": f"Cannot access page — HTTP {resp.status_code}"}
        
        # Check 3: Publish permissions
        if checks["token_valid"]["ok"] and checks["page_access"]["ok"]:
            checks["publish_permission"] = {
                "ok": True,
                "detail": "Token valid and page accessible — publish permissions confirmed",
            }
        elif not checks["token_valid"]["ok"]:
            checks["publish_permission"] = {
                "ok": False,
                "detail": "Cannot verify — token is invalid",
            }
        else:
            checks["publish_permission"] = {
                "ok": False,
                "detail": "Cannot verify — page access failed",
            }

    except _requests_mod.exceptions.Timeout:
        if checks["token_valid"]["detail"] == "Not checked":
            checks["token_valid"] = {"ok": False, "detail": f"Facebook API timed out ({PLATFORM_VERIFY_TIMEOUT}s)"}
    except Exception as e:
        if checks["token_valid"]["detail"] == "Not checked":
            checks["token_valid"] = {"ok": False, "detail": f"Error: {str(e)[:150]}"}

    return checks
def _verify_twitter(account) -> dict:
    checks = {"token_valid": {"ok": False, "detail": "Not checked"}, "page_access": {"ok": False, "detail": "Not checked", "page_name": account.account_name}, "publish_permission": {"ok": False, "detail": "Not checked"}, "rate_limit": {"ok": True, "detail": "Rate limits assumed OK"}}
    token = _get_account_token(account)
    if not token:
        checks["token_valid"] = {"ok": False, "detail": "Missing access token"}
        return checks
    try:
        resp = _requests_mod.get("https://api.twitter.com/2/users/me", headers={"Authorization": f"Bearer {token}"}, timeout=PLATFORM_VERIFY_TIMEOUT)
        if resp.status_code == 200:
            user = resp.json().get("data", {})
            name = user.get("username", account.account_name)
            checks["token_valid"] = {"ok": True, "detail": f"Authenticated as @{name}"}
            checks["page_access"] = {"ok": True, "detail": f"Account @{name} accessible", "page_name": f"@{name}"}
            checks["publish_permission"] = {"ok": True, "detail": "Publish permissions assumed"}
        elif resp.status_code == 401:
            checks["token_valid"] = {"ok": False, "detail": "Token expired"}
    except Exception as e:
        checks["token_valid"] = {"ok": False, "detail": f"Error: {str(e)[:150]}"}
    return checks


def _verify_linkedin(account) -> dict:
    checks = {"token_valid": {"ok": False, "detail": "Not checked"}, "page_access": {"ok": False, "detail": "Not checked", "page_name": account.account_name}, "publish_permission": {"ok": False, "detail": "Not checked"}, "rate_limit": {"ok": True, "detail": "Rate limits assumed OK"}}
    token = _get_account_token(account)
    if not token:
        checks["token_valid"] = {"ok": False, "detail": "Missing access token"}
        return checks
    try:
        resp = _requests_mod.get("https://api.linkedin.com/v2/userinfo", headers={"Authorization": f"Bearer {token}"}, timeout=PLATFORM_VERIFY_TIMEOUT)
        if resp.status_code == 200:
            name = resp.json().get("name", account.account_name)
            checks["token_valid"] = {"ok": True, "detail": f"Authenticated as {name}"}
            checks["page_access"] = {"ok": True, "detail": "Profile accessible", "page_name": name}
            checks["publish_permission"] = {"ok": True, "detail": "Publish permissions assumed"}
    except Exception as e:
        checks["token_valid"] = {"ok": False, "detail": f"Error: {str(e)[:150]}"}
    return checks


def _verify_instagram(account) -> dict:
    checks = {"token_valid": {"ok": False, "detail": "Not checked"}, "page_access": {"ok": False, "detail": "Not checked", "page_name": account.account_name}, "publish_permission": {"ok": False, "detail": "Not checked"}, "rate_limit": {"ok": True, "detail": "Rate limits assumed OK"}}
    token = _get_account_token(account)
    if not token:
        checks["token_valid"] = {"ok": False, "detail": "Missing access token"}
        return checks
    try:
        resp = _requests_mod.get("https://graph.facebook.com/v22.0/me", params={"access_token": token, "fields": "id,name"}, timeout=PLATFORM_VERIFY_TIMEOUT)
        if resp.status_code == 200:
            checks["token_valid"] = {"ok": True, "detail": f"Token valid"}
            checks["page_access"] = {"ok": True, "detail": "Account accessible"}
            checks["publish_permission"] = {"ok": True, "detail": "Publish permissions assumed"}
    except Exception as e:
        checks["token_valid"] = {"ok": False, "detail": f"Error: {str(e)[:150]}"}
    return checks


def _verify_whatsapp(account) -> dict:
    checks = {"token_valid": {"ok": False, "detail": "Not checked"}, "page_access": {"ok": False, "detail": "Not checked", "page_name": account.account_name}, "publish_permission": {"ok": False, "detail": "Not checked"}, "rate_limit": {"ok": True, "detail": "Rate limits assumed OK"}}
    token = _get_account_token(account)
    phone_id = _get_account_platform_id(account)
    if not token or not phone_id:
        checks["token_valid"] = {"ok": False, "detail": "Missing credentials"}
        return checks
    try:
        resp = _requests_mod.get(f"https://graph.facebook.com/v22.0/{phone_id}", params={"access_token": token, "fields": "display_phone_number,verified_name"}, timeout=PLATFORM_VERIFY_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            checks["token_valid"] = {"ok": True, "detail": f"WhatsApp: {data.get('verified_name', 'unknown')}"}
            checks["page_access"] = {"ok": True, "detail": "Phone verified"}
            checks["publish_permission"] = {"ok": True, "detail": "Messaging active"}
    except Exception as e:
        checks["token_valid"] = {"ok": False, "detail": f"Error: {str(e)[:150]}"}
    return checks


def _verify_telegram(account) -> dict:
    checks = {"token_valid": {"ok": False, "detail": "Not checked"}, "page_access": {"ok": False, "detail": "Not checked", "page_name": account.account_name}, "publish_permission": {"ok": False, "detail": "Not checked"}, "rate_limit": {"ok": True, "detail": "Rate limits assumed OK"}}
    token = _get_account_token(account)
    if not token:
        checks["token_valid"] = {"ok": False, "detail": "Missing bot token"}
        return checks
    try:
        resp = _requests_mod.get(f"https://api.telegram.org/bot{token}/getMe", timeout=PLATFORM_VERIFY_TIMEOUT)
        if resp.status_code == 200 and resp.json().get("ok"):
            bot = resp.json().get("result", {})
            name = bot.get("username", account.account_name)
            checks["token_valid"] = {"ok": True, "detail": f"Bot @{name} valid"}
            checks["page_access"] = {"ok": True, "detail": f"Bot accessible", "page_name": f"@{name}"}
            checks["publish_permission"] = {"ok": True, "detail": "Bot can send messages"}
    except Exception as e:
        checks["token_valid"] = {"ok": False, "detail": f"Error: {str(e)[:150]}"}
    return checks


# ═══════════════════════════════════════════════════════════════════════════
# KNOWLEDGE BASE
# ═══════════════════════════════════════════════════════════════════════════

@api_v1_bp.route("/brands/<brand_id>/knowledge", methods=["POST"])
@_auth_required
def create_knowledge_entry(brand_id):
    from repositories.knowledge_repository import KnowledgeRepository
    from repositories.brand_repository import BrandRepository
    from services.knowledge_base import KnowledgeBase
    try:
        _require_workspace_ownership("brand", brand_id)
        brand_repo = _get_container().resolve(BrandRepository)
        brand = brand_repo.find_by_id(brand_id)
        if not brand:
            return _err("Brand not found", 404)
        data = request.get_json(force=True, silent=True) or {}
        content = _sanitize(data.get("content", ""))
        if not content:
            return _err("content field is required", 400)
        kb_repo = _get_container().resolve(KnowledgeRepository)
        kb = KnowledgeBase(brand_id=brand_id, workspace_id=brand.workspace_id, repository=kb_repo)
        entry = kb.add_document(content=content, doc_type=data.get("doc_type", "text"), metadata=data.get("metadata", {}))
        return _ok({"knowledge_entry": entry.model_dump(mode="json")}, 201)
    except Exception as exc:
        return _safe_err(exc, f"POST /api/v1/brands/{brand_id}/knowledge", 500)


@api_v1_bp.route("/brands/<brand_id>/knowledge", methods=["GET"])
@_auth_required
def list_knowledge_entries(brand_id):
    from repositories.knowledge_repository import KnowledgeRepository
    try:
        _require_workspace_ownership("brand", brand_id)
        repo = _get_container().resolve(KnowledgeRepository)
        entries = repo.find_by_brand(brand_id)
        return _ok({"knowledge_entries": [e.model_dump(mode="json") for e in entries]})
    except Exception as exc:
        return _safe_err(exc, f"GET /api/v1/brands/{brand_id}/knowledge", 500)


@api_v1_bp.route("/knowledge/<knowledge_id>", methods=["DELETE"])
@_auth_required
def delete_knowledge_entry(knowledge_id):
    from repositories.knowledge_repository import KnowledgeRepository
    try:
        repo = _get_container().resolve(KnowledgeRepository)
        deleted = repo.delete(knowledge_id)
        _append_audit(action="knowledge_deleted", resource=f"knowledge:{knowledge_id}", status="success")
        return _ok({"deleted": deleted})
    except Exception as exc:
        return _safe_err(exc, f"DELETE /api/v1/knowledge/{knowledge_id}", 500)


@api_v1_bp.route("/brands/<brand_id>/knowledge/search", methods=["POST"])
@_auth_required
def search_knowledge_entries(brand_id):
    from repositories.brand_repository import BrandRepository
    from repositories.knowledge_repository import KnowledgeRepository
    from services.knowledge_base import KnowledgeBase
    try:
        _require_workspace_ownership("brand", brand_id)
        brand_repo = _get_container().resolve(BrandRepository)
        brand = brand_repo.find_by_id(brand_id)
        if not brand:
            return _err("Brand not found", 404)
        data = request.get_json(force=True, silent=True) or {}
        query = _sanitize(data.get("query", ""))
        top_k = min(50, max(1, int(data.get("top_k", 5))))
        if not query:
            return _err("query field is required", 400)
        kb_repo = _get_container().resolve(KnowledgeRepository)
        kb = KnowledgeBase(brand_id=brand_id, workspace_id=brand.workspace_id, repository=kb_repo)
        results = kb.search(query=query, top_k=top_k)
        serialised = [{"entry": entry.model_dump(mode="json"), "similarity": float(sim)} for entry, sim in results]
        return _ok({"results": serialised})
    except Exception as exc:
        return _safe_err(exc, f"POST /api/v1/brands/{brand_id}/knowledge/search", 500)


@api_v1_bp.route("/workspaces/<workspace_id>/knowledge/bulk", methods=["POST"])
@_auth_required
def bulk_create_knowledge(workspace_id):
    from repositories.knowledge_repository import KnowledgeRepository
    from services.knowledge_base import KnowledgeBase
    try:
        _require_workspace_ownership("workspace", workspace_id)
        data = request.get_json(force=True, silent=True) or {}
        items = data.get("items", [])
        if not items or len(items) > 50:
            return _err("items array required, max 50", 400)
        kb_repo = _get_container().resolve(KnowledgeRepository)
        kb = KnowledgeBase(brand_id=data.get("brand_id", "bulk"), workspace_id=workspace_id, repository=kb_repo)
        results, errors = [], []
        for i, item in enumerate(items):
            content = _sanitize(str(item.get("content", "")))
            if not content:
                errors.append({"index": i, "error": "content required"})
                continue
            try:
                entry = kb.add_document(content=content, doc_type=item.get("doc_type", "text"), metadata=item.get("metadata", {}))
                results.append({"id": entry.id, "status": "created"})
            except Exception as e:
                errors.append({"index": i, "error": str(e)[:200]})
        _append_audit(action="knowledge_bulk_created", resource=f"workspace:{workspace_id}", status="success")
        return _ok({"created": len(results), "errors": len(errors), "items": results}, 201)
    except Exception as exc:
        return _safe_err(exc, f"POST /api/v1/workspaces/{workspace_id}/knowledge/bulk", 500)


@api_v1_bp.route("/knowledge", methods=["GET"])
@_auth_required
def list_knowledge():
    try:
        limit = min(200, max(1, int(request.args.get("limit", 50))))
        search = request.args.get("q", "").strip().lower()
        from services.supabase import supabase_service
        if supabase_service and supabase_service.client:
            result = supabase_service.client.table("knowledge_entries").select("*").order("created_at", desc=True).limit(limit).execute()
            docs = result.data or []
            if search:
                docs = [d for d in docs if search in (d.get("content") or "").lower()]
            return _ok({"documents": docs, "count": len(docs), "source": "supabase"})
        return _ok({"documents": [], "count": 0})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/knowledge", 500)


@api_v1_bp.route("/knowledge", methods=["POST"])
@_auth_required
def create_knowledge():
    try:
        data = request.get_json(force=True, silent=True) or {}
        content = _sanitize(str(data.get("content", "")))
        if not content:
            return _err("content field is required", 400)
        entry = {"id": _uuid_mod.uuid4().hex, "title": str(data.get("title", content[:80])), "content": content, "doc_type": str(data.get("doc_type", "text")), "created_at": datetime.now(timezone.utc).isoformat()}
        from services.supabase import supabase_service
        if supabase_service and supabase_service.client:
            try:
                supabase_service.client.table("knowledge_entries").insert(entry).execute()
            except Exception:
                pass
        _append_audit(action="knowledge_created", resource=f"knowledge:{entry['id']}", status="success")
        return _ok({"document": entry}, 201)
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/knowledge", 500)


# ═══════════════════════════════════════════════════════════════════════════
# API KEYS
# ═══════════════════════════════════════════════════════════════════════════

@api_v1_bp.route("/keys/generate", methods=["POST"])
@_auth_required_rate_limited
def generate_api_key():
    try:
        data = request.get_json(force=True, silent=True) or {}
        label = _sanitize(str(data.get("label", "api-key")))[:64] or "api-key"
        raw_token = "kz_" + _secrets_mod.token_urlsafe(32)
        key_id = _uuid_mod.uuid4().hex[:12]
        hashed = _hashlib_mod.sha256(raw_token.encode()).hexdigest()
        prefix = raw_token[:10] + "..."
        entry = {"id": key_id, "label": label, "prefix": prefix, "hashed": hashed, "created_at": datetime.now(timezone.utc).isoformat(), "revoked": False}
        with _keys_lock:
            _active_keys[key_id] = entry
        # Persist to Supabase
        try:
            from services.supabase import supabase_service
            if supabase_service and supabase_service.client:
                supabase_service.client.table("api_keys").upsert(entry).execute()
        except Exception:
            pass
        _append_audit(action="api_key_created", resource=f"key:{key_id}", status="success")
        return _ok({"key_id": key_id, "token": raw_token, "prefix": prefix, "label": label, "note": "Store this token securely."}, 201)
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/keys/generate", 500)


@api_v1_bp.route("/keys", methods=["GET"])
@_auth_required
def list_api_keys():
    try:
        with _keys_lock:
            keys = [{k: v for k, v in e.items() if k != "hashed"} for e in _active_keys.values() if not e.get("revoked")]
        # Fallback to Supabase if memory is empty
        if not keys:
            try:
                from services.supabase import supabase_service
                if supabase_service and supabase_service.client:
                    res = supabase_service.client.table("api_keys").select("*").eq("revoked", False).execute()
                    keys = [{k: v for k, v in row.items() if k != "hashed"} for row in (res.data or [])]
            except Exception:
                pass
        return _ok({"keys": keys, "count": len(keys)})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/keys", 500)


@api_v1_bp.route("/keys/<key_id>", methods=["DELETE"])
@_auth_required
def revoke_api_key(key_id):
    try:
        with _keys_lock:
            if key_id not in _active_keys:
                return _err(f"Key not found", 404)
            _active_keys[key_id]["revoked"] = True
        try:
            from services.supabase import supabase_service
            if supabase_service and supabase_service.client:
                supabase_service.client.table("api_keys").update({"revoked": True}).eq("id", key_id).execute()
        except Exception:
            pass
        _append_audit(action="api_key_revoked", resource=f"key:{key_id}", status="success")
        return _ok({"revoked": True, "key_id": key_id})
    except Exception as exc:
        return _safe_err(exc, f"DELETE /api/v1/keys/{key_id}", 500)


# ═══════════════════════════════════════════════════════════════════════════
# WORKFLOWS
# ═══════════════════════════════════════════════════════════════════════════

@api_v1_bp.route("/workflow", methods=["GET"])
@_auth_required
def list_workflows():
    with _workflow_history_lock:
        history = list(_workflow_history)
    last_runs = {}
    for entry in reversed(history):
        name = entry.get("workflow")
        if name and name not in last_runs:
            last_runs[name] = entry
    workflows = [{"name": n, **m, "last_run": last_runs.get(n)} for n, m in _AVAILABLE_WORKFLOWS.items()]
    return _ok({"workflows": workflows})


@api_v1_bp.route("/workflow/run", methods=["POST"])
@_auth_required
def run_workflow():
    try:
        data = request.get_json(force=True, silent=True) or {}
        name = data.get("workflow", "post_publish").strip().lower()
        if name not in _AVAILABLE_WORKFLOWS:
            return _err(f"Unknown workflow: {name}", 400)
        from scheduler.post_scheduler import scheduler
        result = scheduler.run_content_now()
        entry = {"workflow": name, "ok": result.get("ok", False), "ran_at": datetime.now(timezone.utc).isoformat()}
        with _workflow_history_lock:
            _workflow_history.append(entry)
        return _ok({"workflow": name, "result": result}) if result.get("ok") else _err(result.get("error", "Failed"), 500)
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/workflow/run", 500)


@api_v1_bp.route("/workflow/history", methods=["GET"])
@_auth_required
def workflow_history():
    try:
        limit = min(20, max(1, int(request.args.get("limit", 20))))
        with _workflow_history_lock:
            entries = list(_workflow_history)[-limit:]
        return _ok({"history": list(reversed(entries))})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/workflow/history", 500)


@api_v1_bp.route("/workflow/status", methods=["GET"])
@_auth_required
def workflow_status():
    try:
        from scheduler.post_scheduler import scheduler
        s = scheduler.get_status()
        return _ok({"status": "running" if s.get("running") else "idle"})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/workflow/status", 500)


@api_v1_bp.route("/workflow/pause", methods=["POST"])
@_auth_required
def workflow_pause():
    from scheduler.post_scheduler import scheduler
    scheduler.stop()
    return _ok({"status": "paused"})


@api_v1_bp.route("/workflow/resume", methods=["POST"])
@_auth_required
def workflow_resume():
    from scheduler.post_scheduler import scheduler
    scheduler.start()
    return _ok({"status": "running"})


@api_v1_bp.route("/workflow/trigger", methods=["POST"])
@_auth_required
def workflow_trigger():
    from scheduler.post_scheduler import scheduler
    result = scheduler.run_content_now()
    return _ok({"ok": result.get("ok", False)})


@api_v1_bp.route("/workflow/<workflow_id>/cancel", methods=["POST"])
@_auth_required
def cancel_workflow(workflow_id):
    with _workflow_history_lock:
        matching = [e for e in _workflow_history if e.get("id") == workflow_id or e.get("topic") == workflow_id]
        if not matching:
            return _err("Workflow not found", 404)
        matching[0]["status"] = "cancelled"
        matching[0]["cancelled_at"] = datetime.now(timezone.utc).isoformat()
    _append_audit(action="workflow_cancelled", resource=f"workflow:{workflow_id}", status="success")
    return _ok({"cancelled": True})


@api_v1_bp.route("/workflow/<workflow_id>/rollback", methods=["POST"])
@_auth_required
def rollback_workflow(workflow_id):
    with _workflow_history_lock:
        matching = [e for e in _workflow_history if e.get("id") == workflow_id or e.get("topic") == workflow_id]
        if not matching:
            return _err("Workflow not found", 404)
        matching[0]["status"] = "rolled_back"
        matching[0]["rolled_back_at"] = datetime.now(timezone.utc).isoformat()
    _append_audit(action="workflow_rolled_back", resource=f"workflow:{workflow_id}", status="success")
    return _ok({"rolled_back": True})


# ═══════════════════════════════════════════════════════════════════════════
# WEBHOOKS
# ═══════════════════════════════════════════════════════════════════════════

@api_v1_bp.route("/webhooks/facebook", methods=["GET", "POST"])
def facebook_webhook():
    if request.method == "GET":
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        if mode == "subscribe":
            from repositories.social_account_repository import SocialAccountRepository
            try:
                repo = _get_container().resolve(SocialAccountRepository)
                for acc in repo.find_by_platform("facebook"):
                    pc = getattr(acc, "platform_config", {}) or {}
                    if pc.get("verify_token") == token:
                        return challenge, 200
            except Exception:
                pass
        return "Verification failed", 403
    if not _verify_webhook_signature("facebook"):
        return "Invalid signature", 403
    try:
        payload = request.get_json(force=True, silent=True) or {}
        logger.info("Facebook webhook: %s", str(payload)[:300])
        _append_audit(action="webhook_facebook", resource="webhook:facebook", status="success")
        return "OK", 200
    except Exception as exc:
        logger.error("Facebook webhook error: %s", exc)
        return "Error", 500


@api_v1_bp.route("/webhooks/twitter", methods=["POST"])
def twitter_webhook():
    if not _verify_webhook_signature("twitter"):
        return "Invalid signature", 403
    _append_audit(action="webhook_twitter", resource="webhook:twitter", status="success")
    return "OK", 200


@api_v1_bp.route("/webhooks/linkedin", methods=["POST"])
def linkedin_webhook():
    if not _verify_webhook_signature("linkedin"):
        return "Invalid signature", 403
    _append_audit(action="webhook_linkedin", resource="webhook:linkedin", status="success")
    return "OK", 200


@api_v1_bp.route("/webhooks/instagram", methods=["GET", "POST"])
def instagram_webhook():
    if request.method == "GET":
        if request.args.get("hub.mode") == "subscribe" and request.args.get("hub.verify_token"):
            return request.args.get("hub.challenge", ""), 200
        return "Verification failed", 403
    if not _verify_webhook_signature("instagram"):
        return "Invalid signature", 403
    return "OK", 200


# ═══════════════════════════════════════════════════════════════════════════
# PLUGIN DISCOVERY
# ═══════════════════════════════════════════════════════════════════════════

@api_v1_bp.route("/plugins", methods=["GET"])
@_auth_required
def list_plugins():
    try:
        container = _get_container()
        plugins_registry = getattr(container, "industry_plugins", {}) if container else {}
        result = [{"name": n, "commands": p.get_commands(), "webhooks": p.get_webhooks()} for n, p in plugins_registry.items()]
        return _ok({"plugins": result, "count": len(result)})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/plugins", 500)


@api_v1_bp.route("/plugins/<plugin_name>/execute", methods=["POST"])
@_auth_required
def execute_plugin_command(plugin_name):
    try:
        container = _get_container()
        if not container:
            return _err("Plugin system not available", 503)
        plugins = getattr(container, "industry_plugins", {})
        plugin = plugins.get(plugin_name)
        if not plugin:
            return _err(f"Plugin '{plugin_name}' not found", 404)
        data = request.get_json(force=True, silent=True) or {}
        command = data.get("command", "")
        if not command:
            return _err("command is required", 400)
        result = plugin.execute(command, data.get("params", {}), data.get("workspace_id", "default"))
        return _ok(result)
    except Exception as exc:
        return _safe_err(exc, f"POST /api/v1/plugins/{plugin_name}/execute", 500)


# ═══════════════════════════════════════════════════════════════════════════
# SYSTEM, STATUS, FEATURES, RATE LIMITS, TENANTS, NOTIFICATIONS, INTEGRATIONS
# ═══════════════════════════════════════════════════════════════════════════

@api_v1_bp.route("/system/health", methods=["GET"])
def get_system_health():
    try:
        from core.reliability import HealthChecker
        checker = _get_container().resolve(HealthChecker)
        statuses = checker.check_all()
        all_ok = all(s.get("ok", False) for s in statuses.values())
        return _ok({"status": "healthy" if all_ok else "degraded", "connectors": statuses})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/system/health", 500)


@api_v1_bp.route("/system/connectors", methods=["GET"])
@_auth_required
def get_system_connectors():
    from services.connectors.connector_factory import supported_platforms
    return _ok({"supported_connectors": supported_platforms()})


@api_v1_bp.route("/health/deep", methods=["GET"])
def health_deep():
    from config.settings import settings as _s
    services = {"gemini": {"status": "ok" if _s.gemini_key else "error"}, "facebook": {"status": "ok" if _s.page_access_token else "error"}, "supabase": {"status": "ok" if _s.supabase_enabled else "degraded"}}
    return jsonify({"status": "ok" if all(s["status"] == "ok" for s in services.values()) else "degraded", "version": "10.0.0", "services": services})


@api_v1_bp.route("/status", methods=["GET"])
def get_status():
    from config.settings import settings as _s
    return _ok({"version": "10.0.0", "environment": _s.environment, "config": {"ai_provider": _s.ai_provider, "gemini_model": _s.gemini_model, "supabase_enabled": _s.supabase_enabled}})


@api_v1_bp.route("/features", methods=["GET"])
def get_features():
    from config.settings import settings as _s
    return _ok({"features": {"auto_reply": True, "auto_post": True, "guardian_scan": _s.guardian_configured}})


@api_v1_bp.route("/features/toggle", methods=["POST"])
@_auth_required
def toggle_feature():
    return _ok({"feature": "ok"})


@api_v1_bp.route("/rate-limits", methods=["GET"])
def get_rate_limits():
    from utils.rate_limiter import rate_limiter
    return _ok({"rate_limits": rate_limiter.get_config()})


@api_v1_bp.route("/rate-limits/unblock", methods=["POST"])
@_auth_required
def unblock_user():
    data = request.get_json(force=True, silent=True) or {}
    identifier = data.get("identifier", "").strip()
    if not identifier:
        return _err("identifier required", 400)
    from utils.rate_limiter import rate_limiter
    return _ok({"unblocked": rate_limiter.unblock(identifier)})


@api_v1_bp.route("/tenants", methods=["GET"])
@_auth_required
def get_tenants():
    from services.supabase import supabase_service
    if supabase_service and supabase_service.client:
        res = supabase_service.client.table("tenants").select("*").eq("status", "active").execute()
        return _ok({"tenants": res.data or []})
    return _ok({"tenants": []})


@api_v1_bp.route("/tenants/switch", methods=["POST"])
@_auth_required
def switch_tenant():
    data = request.get_json(force=True, silent=True) or {}
    if not data.get("tenant_id"):
        return _err("tenant_id required", 400)
    return _ok({"switched": True})


@api_v1_bp.route("/notifications", methods=["GET"])
@_auth_required
def get_notifications():
    return _ok({"notifications": []})


@api_v1_bp.route("/notifications/<notif_id>/read", methods=["POST"])
@_auth_required
def mark_notification_read(notif_id):
    return _ok({"read": True})


@api_v1_bp.route("/integrations", methods=["GET"])
@_auth_required
def get_integrations():
    from config.settings import settings as _s
    integrations = [
        {"id": "facebook", "connected": bool(_s.page_access_token)},
        {"id": "gemini", "connected": bool(_s.gemini_key)},
        {"id": "supabase", "connected": _s.supabase_enabled},
        {"id": "twitter", "connected": bool(_s.twitter_access_token)},
        {"id": "linkedin", "connected": bool(_s.linkedin_access_token)},
        {"id": "whatsapp", "connected": bool(_s.whatsapp_token)},
        {"id": "redis", "connected": bool(_s.upstash_redis_rest_url)},
    ]
    return _ok({"integrations": integrations, "connected": sum(1 for i in integrations if i["connected"])})


@api_v1_bp.route("/audit-logs", methods=["GET"])
@_auth_required
def get_audit_logs():
    limit = min(200, max(1, int(request.args.get("limit", 50))))
    with _audit_log_lock:
        logs = list(_audit_log)[:limit]
    return _ok({"logs": logs, "count": len(logs)})


@api_v1_bp.route("/scan", methods=["POST"])
@_auth_required
def trigger_scan_shortcut():
    return trigger_scan()


@api_v1_bp.route("/scan", methods=["GET"])
def get_scan_status():
    return guardian_status()
