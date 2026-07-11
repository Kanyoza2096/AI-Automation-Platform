"""
Kanyoza Systems AI Platform v10 — REST API v1 Blueprint (M8).

All versioned endpoints live at /api/v1/*.
Protected endpoints require one of:
  - Authorization: Bearer <MASTER_API_TOKEN>   (preferred — frontend/dashboard)
  - X-API-Key: <APP_SECRET>                    (admin key)
  - X-API-Key: <OWNER_PSID>                    (owner PSID fallback)

Endpoints — 83 total
-------------------
Posts, Messages, Analytics, Bot, Guardian, Schedule, Metrics,
AI Config & Persona, Workspaces, Brands, AI Profiles, Social Accounts,
Knowledge Base, API Keys, Rate Limits, Workflows, System Health,
Webhooks, Notifications, Integrations, Audit Logs, Tenants, Features
"""

from __future__ import annotations

import collections as _collections_mod
import hashlib as _hashlib_mod
import hmac
import json as _json_mod
import logging
import secrets as _secrets_mod
import threading as _threading_mod
import uuid as _uuid_mod
from datetime import datetime, timezone
from functools import wraps

import requests as _requests_mod
from flask import Blueprint, abort, g, jsonify, request

logger = logging.getLogger("kanyoza.api.v1")

api_v1_bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")

# ── Security Constants ─────────────────────────────────────────────────────
MAX_REQUEST_BODY_SIZE = 1_048_576   # 1 MB
MAX_AI_PROMPT_LENGTH  = 4000       # 4000 characters
PLATFORM_VERIFY_TIMEOUT = 8        # seconds — matches frontend AbortSignal

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
    logger.error("%s error: %s", endpoint, exc, exc_info=True)
    from config.settings import settings as _s
    if getattr(_s, "debug", False):
        return _err(str(exc), status)
    return _err("An internal error occurred. Please try again later.", status)


def _append_audit(action: str, resource: str, user: str = "system",
                   status: str = "success", detail: str = "") -> None:
    entry = {
        "id": _uuid_mod.uuid4().hex[:16],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "user": user,
        "action": action,
        "resource": resource,
        "status": status,
        "detail": detail,
    }
    with _audit_log_lock:
        _audit_log.appendleft(entry)


# ── Credential Helpers for Verifiers ───────────────────────────────────────

def _get_account_token(account) -> str:
    """Extract access token from account fields or platform_config dict."""
    token = getattr(account, "access_token", "") or ""
    if not token:
        pc = getattr(account, "platform_config", {}) or {}
        token = pc.get("page_access_token") or pc.get("access_token") or pc.get("bot_token") or ""
    return token


def _get_account_platform_id(account) -> str:
    """Extract platform account ID from account fields or platform_config dict."""
    pid = getattr(account, "platform_account_id", "") or ""
    if not pid:
        pc = getattr(account, "platform_config", {}) or {}
        pid = pc.get("page_id") or pc.get("phone_number_id") or pc.get("person_urn") or ""
    return pid


# ═══════════════════════════════════════════════════════════════════════════
# BEFORE REQUEST
# ═══════════════════════════════════════════════════════════════════════════

@api_v1_bp.before_request
def _enforce_request_size_limit():
    content_length = request.content_length
    if content_length and content_length > MAX_REQUEST_BODY_SIZE:
        return jsonify({
            "ok": False,
            "error": "payload_too_large",
            "message": f"Request body exceeds maximum size of {MAX_REQUEST_BODY_SIZE} bytes.",
        }), 413


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
        if state == "draft":
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
        recipient = data.get("recipient_id", "").strip()
        text = data.get("text", "").strip()
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
        repo_filter = request.args.get("repo", "").strip()
        if repo_filter:
            issues = [i for i in issues if isinstance(i, dict) and i.get("repo", "") == repo_filter]
        _order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        issues = sorted(issues, key=lambda i: _order.get(i.get("severity", "").lower(), 9) if isinstance(i, dict) else 9)
        return _ok({
            "issues": issues, "count": len(issues),
            "auto_fix_enabled": getattr(__import__("config.settings", fromlist=["settings"]).settings, "guardian_auto_fix", False),
        })
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/guardian/issues", 500)


@api_v1_bp.route("/guardian/scan", methods=["POST"])
@_auth_required
def trigger_scan():
    try:
        from services.guardian_service import GuardianService
        container = _get_container()
        if not container:
            return _err("Guardian not available — DI container not attached", 503)
        svc = container.try_resolve(GuardianService)
        if not svc:
            return _err("Guardian not configured", 503)
        if not svc.is_configured:
            return _err("Guardian is not configured (missing GitHub credentials)", 503)
        t = _threading_mod.Thread(target=svc.scan_all, daemon=True, name="api-guardian-scan")
        t.start()
        return _ok({"started": True, "message": "Scan started in background"}, 202)
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
        cpu_percent = 0.0
        mem_percent = 0.0
        disk_percent = 0.0
        net_in_kbps = 0.0
        net_out_kbps = 0.0
        try:
            import psutil as _ps
            cpu_percent = _ps.cpu_percent(interval=None)
            mem_percent = _ps.virtual_memory().percent
            disk_percent = _ps.disk_usage("/").percent
            net_io = _ps.net_io_counters()
            net_in_kbps = round(net_io.bytes_recv / 1024, 1)
            net_out_kbps = round(net_io.bytes_sent / 1024, 1)
        except Exception:
            pass
        return _ok({
            "cpu_percent": cpu_percent,
            "memory_percent": mem_percent,
            "disk_percent": disk_percent,
            "network_in_kbps": net_in_kbps,
            "network_out_kbps": net_out_kbps,
            "workers_active": 1,
            "queue_depth": 0,
        })
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
            valid = {"gemini", "huggingface"}
            if data["provider"] not in valid:
                return _err(f"provider must be one of {sorted(valid)}", 400)
            _s.ai_provider = data["provider"]
        if "model" in data:
            _s.gemini_model = str(data["model"])
        temp_map = {"chat_temperature": "gemini_chat_temperature", "post_temperature": "gemini_post_temperature"}
        for frontend_key, settings_key in temp_map.items():
            if frontend_key in data:
                val = data[frontend_key]
                if not isinstance(val, (int, float)) or not 0.0 <= float(val) <= 2.0:
                    return _err(f"{frontend_key} must be a float in 0.0–2.0", 400)
                setattr(_s, settings_key, float(val))
        if "safety_level" in data:
            valid = {"low", "medium", "high"}
            if data["safety_level"] not in valid:
                return _err(f"safety_level must be one of {sorted(valid)}", 400)
            _s.gemini_safety_level = data["safety_level"]
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
        return _safe_err(exc, "PUT /api/v1/ai/config", 500)


@api_v1_bp.route("/ai/persona", methods=["GET"])
@_auth_required
def get_ai_persona():
    from api.ai_runtime import get as _rt_get
    from config.settings import settings as _s
    rt = _rt_get()
    return _ok({
        "tone": int(round(rt.get("tone_formality", 0.6) * 100)),
        "aggression": int(round(rt.get("tone_assertiveness", 0.7) * 100)),
        "humor": int(round(rt.get("tone_humor", 0.2) * 100)),
        "model": getattr(_s, "gemini_model", "gemini-2.5-flash"),
        "system_prompt": rt.get("system_prompt_override") or "",
        "persona_mood": rt.get("persona_mood", "professional"),
        "tone_formality": rt.get("tone_formality", 0.6),
        "tone_assertiveness": rt.get("tone_assertiveness", 0.7),
        "tone_humor": rt.get("tone_humor", 0.2),
    })


@api_v1_bp.route("/ai/persona", methods=["PUT"])
@_auth_required
def update_ai_persona():
    from api.ai_runtime import update as _rt_update
    from config.settings import settings as _s
    try:
        data = request.get_json(force=True, silent=True) or {}
        rt_updates: dict = {}
        if "tone" in data:
            val = data["tone"]
            if not isinstance(val, (int, float)) or not 0 <= val <= 100:
                return _err("tone must be an integer in 0–100", 400)
            rt_updates["tone_formality"] = round(float(val) / 100.0, 4)
        if "aggression" in data:
            val = data["aggression"]
            if not isinstance(val, (int, float)) or not 0 <= val <= 100:
                return _err("aggression must be an integer in 0–100", 400)
            rt_updates["tone_assertiveness"] = round(float(val) / 100.0, 4)
        if "humor" in data:
            val = data["humor"]
            if not isinstance(val, (int, float)) or not 0 <= val <= 100:
                return _err("humor must be an integer in 0–100", 400)
            rt_updates["tone_humor"] = round(float(val) / 100.0, 4)
        for _model_key in ("model", "gemini_model"):
            if _model_key in data:
                _s.gemini_model = str(data[_model_key])
                break
        if "system_prompt" in data:
            rt_updates["system_prompt_override"] = data["system_prompt"] or None
        for internal_key in ("tone_formality", "tone_assertiveness", "tone_humor", "persona_mood", "system_prompt_override"):
            if internal_key in data and internal_key not in rt_updates:
                rt_updates[internal_key] = data[internal_key]
        if rt_updates:
            rt = _rt_update(**rt_updates)
        else:
            from api.ai_runtime import get as _rt_get
            rt = _rt_get()
        return _ok({"updated": True, "ok": True, "persona": rt})
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
        rt = _rt_update(
            persona_mood="professional",
            tone_assertiveness=0.7,
            tone_humor=0.2,
            tone_formality=0.6,
            system_prompt_override=None,
        )
        return _ok({"reset": True, "persona": rt})
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/persona/reset", 500)


# ═══════════════════════════════════════════════════════════════════════════
# AI CHAT
# ═══════════════════════════════════════════════════════════════════════════

@api_v1_bp.route("/ai/chat", methods=["POST"])
@_auth_required
@_rate_limit("ai_chat", max_per_minute=20)
def ai_chat():
    try:
        data = request.get_json(force=True, silent=True) or {}
        message = str(data.get("message", "")).strip()
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
            _append_audit(
                action="ai_command",
                resource=intent.get("action", "unknown"),
                status="success" if result.get("ok") else "failed",
                detail=f"Command: {message[:100]}",
            )
            return _ok({
                "reply": result.get("message", "Command processed."),
                "model": "command-executor",
                "tokens_used": 0,
                "command_result": result,
            })
        gemini = GeminiService()
        reply = gemini.generate_chat_response(sender_id="api_chat_user", user_message=message)
        _append_audit(
            action="ai_chat", resource="gemini", status="success",
            detail=f"Chat ({len(message)} chars in, {len(reply)} chars out)",
        )
        return _ok({
            "reply": reply,
            "model": _s.gemini_model,
            "tokens_used": len(reply.split()),
        })
    except Exception as exc:
        logger.warning("Gemini chat failed: %s", exc)
        return _ok({
            "reply": "The AI service is temporarily unavailable. Check your GEMINI_KEY configuration.",
            "model": "unavailable",
            "tokens_used": 0,
        })


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
        _append_audit(action="workspace_created", resource=f"workspace:{ws_id}", status="success", detail=f"Workspace '{ws.name}' created")
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
        updated_ws = Workspace(**ws_dict)
        saved = repo.save(updated_ws)
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
            data["slug"] = data["name"].lower().replace(" ", "-")
        if "hashtags" in data and isinstance(data["hashtags"], str):
            data["hashtags"] = [h.strip() for h in data["hashtags"].replace("#", "").split() if h.strip()]
        if "colors" in data and isinstance(data["colors"], str):
            try:
                data["colors"] = _json_mod.loads(data["colors"])
            except _json_mod.JSONDecodeError:
                data["colors"] = {}
        if "posting_schedule" in data and isinstance(data["posting_schedule"], str):
            try:
                data["posting_schedule"] = _json_mod.loads(data["posting_schedule"])
            except _json_mod.JSONDecodeError:
                data["posting_schedule"] = {}
        brand = Brand(**data)
        repo = _get_container().resolve(BrandRepository)
        saved = repo.save(brand)
        _append_audit(action="brand_created", resource=f"brand:{brand_id}", status="success", detail=f"Brand '{brand.name}' created")
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
        updated_brand = Brand(**brand_dict)
        saved = repo.save(updated_brand)
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
        complexity_map = {"simple": "beginner", "moderate": "intermediate", "advanced": "advanced", "expert": "expert"}
        if data.get("complexity"):
            data["complexity"] = complexity_map.get(data["complexity"].lower(), data["complexity"])
        emoji_map = {"none": "none", "low": "low", "medium": "medium", "high": "high"}
        if data.get("emoji_level"):
            data["emoji_level"] = emoji_map.get(data["emoji_level"].lower(), data["emoji_level"])
        profile = AIProfile(**data)
        repo = _get_container().resolve(AIProfileRepository)
        saved = repo.save(profile)
        _append_audit(action="ai_profile_created", resource=f"ai_profile:{profile_id}", status="success", detail=f"AI Profile '{profile.name}' created")
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
        updated_profile = AIProfile(**profile_dict)
        saved = repo.save(updated_profile)
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
# SOCIAL ACCOUNTS CRUD
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
        account = SocialAccount(**data)
        repo = _get_container().resolve(SocialAccountRepository)
        saved = repo.save(account)
        response_data = saved.model_dump(mode="json")
        response_data.pop("access_token", None)
        response_data.pop("encrypted_credential", None)
        _append_audit(action="social_account_created", resource=f"social_account:{acc_id}", status="success", detail=f"Social account for '{account.platform}' created")
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
        safe_accounts = []
        for a in accounts:
            a_dict = a.model_dump(mode="json")
            a_dict.pop("access_token", None)
            a_dict.pop("encrypted_credential", None)
            safe_accounts.append(a_dict)
        return _ok({"social_accounts": safe_accounts})
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
        account_dict = account.model_dump(mode="json")
        account_dict.update(update_data)
        updated_account = SocialAccount(**account_dict)
        saved = repo.save(updated_account)
        response_data = saved.model_dump(mode="json")
        response_data.pop("access_token", None)
        response_data.pop("encrypted_credential", None)
        _append_audit(action="social_account_updated", resource=f"social_account:{account_id}", status="success")
        return _ok({"social_account": response_data})
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


# ═══════════════════════════════════════════════════════════════════════════
# SOCIAL ACCOUNTS — HEALTH CHECK
# ═══════════════════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════════════════
# SOCIAL ACCOUNTS — VERIFY PUBLISH (ALL PLATFORMS)
# ═══════════════════════════════════════════════════════════════════════════

@api_v1_bp.route("/social-accounts/<account_id>/verify-publish", methods=["POST"])
@_auth_required
def verify_publish(account_id):
    """
    Verify publish readiness WITHOUT posting anything.
    Tests token validity, page/account access, and publish permissions.
    Supports all 6 platforms: facebook, twitter, linkedin, instagram, whatsapp, telegram.
    """
    from repositories.social_account_repository import SocialAccountRepository
    from models.social_account import SocialAccount
    try:
        _require_workspace_ownership("social_account", account_id)
        repo = _get_container().resolve(SocialAccountRepository)
        account = repo.find_by_id(account_id)
        if not account:
            return _err("Social Account not found", 404)

        platform = str(account.platform).lower()
        verifier_map = {
            "facebook":  _verify_facebook,
            "twitter":   _verify_twitter,
            "linkedin":  _verify_linkedin,
            "instagram": _verify_instagram,
            "whatsapp":  _verify_whatsapp,
            "telegram":  _verify_telegram,
        }

        verifier = verifier_map.get(platform)
        if not verifier:
            return _err(f"Verification not supported for platform: {platform}", 400)

        checks = verifier(account)
        ready = all(c.get("ok", False) for c in checks.values())

        # Update health status
        account_dict = account.model_dump(mode="json")
        account_dict["health_status"] = "healthy" if ready else "unhealthy"
        account_dict["last_health_check"] = datetime.now(timezone.utc).isoformat()
        updated = SocialAccount(**account_dict)
        repo.save(updated)

        # Generate summary
        failed = [k.replace("_", " ").title() for k, c in checks.items() if not c.get("ok")]
        if ready:
            summary = "All checks passed. Ready to publish."
        elif len(failed) == 1:
            summary = f"{failed[0]} check failed"
        elif len(failed) == 2:
            summary = f"{failed[0]} and {failed[1]} checks failed"
        else:
            summary = f"{len(failed)} checks failed: {', '.join(failed[:2])}..."

        return _ok({
            "verified": ready,
            "page_name": checks.get("page_access", {}).get("page_name", account.account_name),
            "summary": summary,
            "ready_to_publish": ready,
            "checks": checks,
        })
    except Exception as exc:
        return _safe_err(exc, f"POST /api/v1/social-accounts/{account_id}/verify-publish", 500)


# ═══════════════════════════════════════════════════════════════════════════
# SOCIAL ACCOUNTS — BULK HEALTH CHECK
# ═══════════════════════════════════════════════════════════════════════════

@api_v1_bp.route("/workspaces/<workspace_id>/social-accounts/bulk-health-check", methods=["POST"])
@_auth_required
def bulk_health_check(workspace_id):
    """Run health checks on all social accounts in a workspace. Max 20 accounts."""
    from repositories.social_account_repository import SocialAccountRepository
    try:
        _require_workspace_ownership("workspace", workspace_id)
        repo = _get_container().resolve(SocialAccountRepository)
        accounts = repo.find_by_workspace(workspace_id)

        if len(accounts) > 20:
            return _err("Bulk health check limited to 20 accounts.", 400)

        results = []
        for account in accounts:
            try:
                from services.connectors.connector_factory import create_connector
                connector = create_connector(account)
                h = connector.health()
                repo.update_health_status(account.id, "healthy" if h.ok else "unhealthy")
                results.append({
                    "id": account.id,
                    "platform": str(account.platform),
                    "account_name": account.account_name,
                    "healthy": h.ok,
                    "latency_ms": h.latency_ms,
                    "error": h.error,
                })
            except Exception as e:
                results.append({
                    "id": account.id,
                    "platform": str(account.platform),
                    "account_name": account.account_name,
                    "healthy": False,
                    "error": str(e)[:200],
                })

        healthy_count = sum(1 for r in results if r.get("healthy"))
        return _ok({
            "results": results,
            "total": len(results),
            "healthy": healthy_count,
            "unhealthy": len(results) - healthy_count,
        })
    except Exception as exc:
        return _safe_err(exc, f"POST /api/v1/workspaces/{workspace_id}/social-accounts/bulk-health-check", 500)


# ═══════════════════════════════════════════════════════════════════════════
# PLATFORM VERIFIERS
# ═══════════════════════════════════════════════════════════════════════════

def _verify_facebook(account) -> dict:
    """Verify Facebook Page token, page access, and publish permissions."""
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
                checks["token_valid"] = {"ok": False, "detail": data.get("error", {}).get("message", "Token invalid")}
        else:
            checks["token_valid"] = {"ok": False, "detail": f"API error: HTTP {resp.status_code}"}

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
                checks["page_access"] = {"ok": False, "detail": "Cannot access page — token may be revoked"}

        if token:
            resp = _requests_mod.get(
                "https://graph.facebook.com/v22.0/me/permissions",
                params={"access_token": token},
                timeout=PLATFORM_VERIFY_TIMEOUT,
            )
            if resp.status_code == 200:
                perms = {p["permission"]: p["status"] for p in resp.json().get("data", [])}
                required = ["pages_manage_posts", "pages_read_engagement"]
                missing = [p for p in required if perms.get(p) != "granted"]
                if not missing:
                    checks["publish_permission"] = {"ok": True, "detail": "Required permissions granted"}
                else:
                    checks["publish_permission"] = {"ok": False, "detail": f"Missing: {', '.join(missing)}"}
    except _requests_mod.exceptions.Timeout:
        checks["token_valid"] = {"ok": False, "detail": f"Facebook API timed out ({PLATFORM_VERIFY_TIMEOUT}s)"}
    except Exception as e:
        checks["token_valid"] = {"ok": False, "detail": f"Error: {str(e)[:150]}"}

    return checks


def _verify_twitter(account) -> dict:
    """Verify Twitter/X OAuth 2.0 token and publish permissions."""
    checks = {
        "token_valid": {"ok": False, "detail": "Not checked"},
        "page_access": {"ok": False, "detail": "Not checked", "page_name": account.account_name},
        "publish_permission": {"ok": False, "detail": "Not checked"},
        "rate_limit": {"ok": True, "detail": "Rate limits assumed OK"},
    }

    token = _get_account_token(account)
    if not token:
        checks["token_valid"] = {"ok": False, "detail": "Missing access token"}
        return checks

    try:
        resp = _requests_mod.get(
            "https://api.twitter.com/2/users/me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=PLATFORM_VERIFY_TIMEOUT,
        )
        if resp.status_code == 200:
            user = resp.json().get("data", {})
            name = user.get("username", account.account_name)
            checks["token_valid"] = {"ok": True, "detail": f"Authenticated as @{name}"}
            checks["page_access"] = {"ok": True, "detail": f"Account @{name} accessible", "page_name": f"@{name}"}
            checks["publish_permission"] = {"ok": True, "detail": "Tweet write permission assumed from OAuth scope"}
        elif resp.status_code == 401:
            checks["token_valid"] = {"ok": False, "detail": "Token expired or revoked"}
        else:
            checks["token_valid"] = {"ok": False, "detail": f"API error: HTTP {resp.status_code}"}
    except _requests_mod.exceptions.Timeout:
        checks["token_valid"] = {"ok": False, "detail": f"Twitter API timed out ({PLATFORM_VERIFY_TIMEOUT}s)"}
    except Exception as e:
        checks["token_valid"] = {"ok": False, "detail": f"Error: {str(e)[:150]}"}

    return checks


def _verify_linkedin(account) -> dict:
    """Verify LinkedIn OAuth token and organization access."""
    checks = {
        "token_valid": {"ok": False, "detail": "Not checked"},
        "page_access": {"ok": False, "detail": "Not checked", "page_name": account.account_name},
        "publish_permission": {"ok": False, "detail": "Not checked"},
        "rate_limit": {"ok": True, "detail": "Rate limits assumed OK"},
    }

    token = _get_account_token(account)
    if not token:
        checks["token_valid"] = {"ok": False, "detail": "Missing access token"}
        return checks

    try:
        resp = _requests_mod.get(
            "https://api.linkedin.com/v2/userinfo",
            headers={"Authorization": f"Bearer {token}"},
            timeout=PLATFORM_VERIFY_TIMEOUT,
        )
        if resp.status_code == 200:
            user = resp.json()
            name = user.get("name", account.account_name)
            checks["token_valid"] = {"ok": True, "detail": f"Authenticated as {name}"}
            checks["page_access"] = {"ok": True, "detail": "Profile accessible", "page_name": name}
            checks["publish_permission"] = {"ok": True, "detail": "Publish permissions assumed from OAuth scope"}
        elif resp.status_code == 401:
            checks["token_valid"] = {"ok": False, "detail": "Token expired or revoked"}
        else:
            checks["token_valid"] = {"ok": False, "detail": f"API error: HTTP {resp.status_code}"}
    except _requests_mod.exceptions.Timeout:
        checks["token_valid"] = {"ok": False, "detail": f"LinkedIn API timed out ({PLATFORM_VERIFY_TIMEOUT}s)"}
    except Exception as e:
        checks["token_valid"] = {"ok": False, "detail": f"Error: {str(e)[:150]}"}

    return checks


def _verify_instagram(account) -> dict:
    """Verify Instagram Business account via Facebook Graph API."""
    checks = {
        "token_valid": {"ok": False, "detail": "Not checked"},
        "page_access": {"ok": False, "detail": "Not checked", "page_name": account.account_name},
        "publish_permission": {"ok": False, "detail": "Not checked"},
        "rate_limit": {"ok": True, "detail": "Rate limits assumed OK"},
    }

    token = _get_account_token(account)
    ig_id = _get_account_platform_id(account)

    if not token:
        checks["token_valid"] = {"ok": False, "detail": "Missing access token"}
        return checks

    try:
        resp = _requests_mod.get(
            "https://graph.facebook.com/v22.0/me",
            params={"access_token": token, "fields": "id,name"},
            timeout=PLATFORM_VERIFY_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            checks["token_valid"] = {"ok": True, "detail": f"Token valid for {data.get('name', 'user')}"}
            if ig_id:
                resp2 = _requests_mod.get(
                    f"https://graph.facebook.com/v22.0/{ig_id}",
                    params={"fields": "username", "access_token": token},
                    timeout=PLATFORM_VERIFY_TIMEOUT,
                )
                if resp2.status_code == 200:
                    name = resp2.json().get("username", account.account_name)
                    checks["page_access"] = {"ok": True, "detail": f"Instagram @{name} accessible", "page_name": f"@{name}"}
                    checks["publish_permission"] = {"ok": True, "detail": "Content publish permissions assumed"}
                else:
                    checks["page_access"] = {"ok": False, "detail": "Instagram account not found or not linked"}
            else:
                checks["page_access"] = {"ok": True, "detail": "Token valid (no specific page checked)"}
                checks["publish_permission"] = {"ok": True, "detail": "Publish permissions assumed"}
        else:
            checks["token_valid"] = {"ok": False, "detail": f"API error: HTTP {resp.status_code}"}
    except _requests_mod.exceptions.Timeout:
        checks["token_valid"] = {"ok": False, "detail": f"Instagram API timed out ({PLATFORM_VERIFY_TIMEOUT}s)"}
    except Exception as e:
        checks["token_valid"] = {"ok": False, "detail": f"Error: {str(e)[:150]}"}

    return checks


def _verify_whatsapp(account) -> dict:
    """Verify WhatsApp Cloud API token and phone number."""
    checks = {
        "token_valid": {"ok": False, "detail": "Not checked"},
        "page_access": {"ok": False, "detail": "Not checked", "page_name": account.account_name},
        "publish_permission": {"ok": False, "detail": "Not checked"},
        "rate_limit": {"ok": True, "detail": "Rate limits assumed OK"},
    }

    token = _get_account_token(account)
    phone_id = _get_account_platform_id(account)

    if not token or not phone_id:
        checks["token_valid"] = {"ok": False, "detail": "Missing access token or phone number ID"}
        return checks

    try:
        resp = _requests_mod.get(
            f"https://graph.facebook.com/v22.0/{phone_id}",
            params={"access_token": token, "fields": "display_phone_number,verified_name"},
            timeout=PLATFORM_VERIFY_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            name = data.get("verified_name", account.account_name)
            checks["token_valid"] = {"ok": True, "detail": f"WhatsApp Business: {name}"}
            checks["page_access"] = {"ok": True, "detail": f"Phone {data.get('display_phone_number', 'unknown')} verified", "page_name": name}
            checks["publish_permission"] = {"ok": True, "detail": "Messaging permissions active"}
        elif resp.status_code == 401:
            checks["token_valid"] = {"ok": False, "detail": "Token expired or invalid"}
        else:
            checks["token_valid"] = {"ok": False, "detail": f"API error: HTTP {resp.status_code}"}
    except _requests_mod.exceptions.Timeout:
        checks["token_valid"] = {"ok": False, "detail": f"WhatsApp API timed out ({PLATFORM_VERIFY_TIMEOUT}s)"}
    except Exception as e:
        checks["token_valid"] = {"ok": False, "detail": f"Error: {str(e)[:150]}"}

    return checks


def _verify_telegram(account) -> dict:
    """Verify Telegram Bot token."""
    checks = {
        "token_valid": {"ok": False, "detail": "Not checked"},
        "page_access": {"ok": False, "detail": "Not checked", "page_name": account.account_name},
        "publish_permission": {"ok": False, "detail": "Not checked"},
        "rate_limit": {"ok": True, "detail": "Rate limits assumed OK"},
    }

    token = _get_account_token(account)
    if not token:
        checks["token_valid"] = {"ok": False, "detail": "Missing bot token"}
        return checks

    try:
        resp = _requests_mod.get(
            f"https://api.telegram.org/bot{token}/getMe",
            timeout=PLATFORM_VERIFY_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("ok"):
                bot = data.get("result", {})
                name = bot.get("username", account.account_name)
                checks["token_valid"] = {"ok": True, "detail": f"Bot @{name} is valid"}
                checks["page_access"] = {"ok": True, "detail": f"Bot @{name} accessible", "page_name": f"@{name}"}
                checks["publish_permission"] = {"ok": True, "detail": "Bot can send messages"}
            else:
                checks["token_valid"] = {"ok": False, "detail": "Invalid bot token"}
        else:
            checks["token_valid"] = {"ok": False, "detail": f"API error: HTTP {resp.status_code}"}
    except _requests_mod.exceptions.Timeout:
        checks["token_valid"] = {"ok": False, "detail": f"Telegram API timed out ({PLATFORM_VERIFY_TIMEOUT}s)"}
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
        query = data.get("query", "").strip()
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


# ─── BULK KNOWLEDGE UPLOAD ─────────────────────────────────────────────────

@api_v1_bp.route("/workspaces/<workspace_id>/knowledge/bulk", methods=["POST"])
@_auth_required
def bulk_create_knowledge(workspace_id):
    """Create up to 50 knowledge entries in a single request."""
    from repositories.knowledge_repository import KnowledgeRepository
    from services.knowledge_base import KnowledgeBase
    try:
        _require_workspace_ownership("workspace", workspace_id)
        data = request.get_json(force=True, silent=True) or {}
        items = data.get("items", [])
        brand_id = data.get("brand_id")

        if not items:
            return _err("items array is required", 400)
        if len(items) > 50:
            return _err("Maximum 50 items per bulk request", 400)

        kb_repo = _get_container().resolve(KnowledgeRepository)
        kb = KnowledgeBase(brand_id=brand_id or "bulk", workspace_id=workspace_id, repository=kb_repo)

        results = []
        errors = []
        for i, item in enumerate(items):
            content = str(item.get("content", "")).strip()
            if not content:
                errors.append({"index": i, "error": "content is required"})
                continue
            try:
                entry = kb.add_document(
                    content=content,
                    doc_type=item.get("doc_type", "text"),
                    metadata=item.get("metadata", {}),
                )
                results.append({"id": entry.id, "status": "created"})
            except Exception as e:
                errors.append({"index": i, "error": str(e)[:200]})

        _append_audit(
            action="knowledge_bulk_created",
            resource=f"workspace:{workspace_id}",
            status="success",
            detail=f"Bulk upload: {len(results)} created, {len(errors)} failed",
        )

        return _ok({
            "created": len(results),
            "errors": len(errors),
            "items": results,
            "error_details": errors if errors else None,
        }, 201)
    except Exception as exc:
        return _safe_err(exc, f"POST /api/v1/workspaces/{workspace_id}/knowledge/bulk", 500)


# ═══════════════════════════════════════════════════════════════════════════
# FLAT KNOWLEDGE ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

@api_v1_bp.route("/knowledge", methods=["GET"])
@_auth_required
def list_knowledge():
    try:
        limit = min(200, max(1, int(request.args.get("limit", 50))))
        search = request.args.get("q", "").strip().lower()
        from services.supabase import supabase_service
        if supabase_service and supabase_service.client:
            try:
                result = (
                    supabase_service.client.table("knowledge_entries")
                    .select("*").order("created_at", desc=True).limit(limit).execute()
                )
                docs = result.data or []
                if search:
                    docs = [d for d in docs if search in (d.get("content") or "").lower() or search in (d.get("title") or "").lower()]
                return _ok({"documents": docs, "count": len(docs), "source": "supabase"})
            except Exception:
                pass
        return _ok({"documents": [], "count": 0, "source": "supabase_unavailable", "note": "Configure Supabase to persist knowledge entries."})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/knowledge", 500)


@api_v1_bp.route("/knowledge", methods=["POST"])
@_auth_required
def create_knowledge():
    try:
        data = request.get_json(force=True, silent=True) or {}
        content = str(data.get("content", "")).strip()
        if not content:
            return _err("content field is required", 400)
        entry = {
            "id": _uuid_mod.uuid4().hex,
            "title": str(data.get("title", content[:80])),
            "content": content,
            "doc_type": str(data.get("doc_type", "text")),
            "category": str(data.get("category", "General")),
            "tags": data.get("tags", []),
            "brand_id": data.get("brand_id", ""),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        from services.supabase import supabase_service
        if supabase_service and supabase_service.client:
            try:
                supabase_service.client.table("knowledge_entries").insert(entry).execute()
                logger.info("Knowledge entry saved to Supabase: %s", entry["id"])
            except Exception as exc:
                logger.error("Supabase knowledge insert failed: %s", exc)
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
        label = str(data.get("label", "api-key")).strip()[:64] or "api-key"
        raw_token = "kz_" + _secrets_mod.token_urlsafe(32)
        key_id = _uuid_mod.uuid4().hex[:12]
        hashed = _hashlib_mod.sha256(raw_token.encode()).hexdigest()
        prefix = raw_token[:10] + "..."
        entry = {
            "id": key_id, "label": label, "prefix": prefix, "hashed": hashed,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_used": None, "revoked": False,
        }
        with _keys_lock:
            _active_keys[key_id] = entry
        _append_audit(action="api_key_created", resource=f"key:{key_id}", user=g.get("auth_method", "unknown"), status="success", detail=f"API key '{label}' created")
        return _ok({
            "key_id": key_id, "token": raw_token, "prefix": prefix, "label": label,
            "created_at": entry["created_at"],
            "note": "Store this token securely — it will not be shown again.",
        }, 201)
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/keys/generate", 500)


@api_v1_bp.route("/keys", methods=["GET"])
@_auth_required
def list_api_keys():
    try:
        with _keys_lock:
            keys = [{k: v for k, v in entry.items() if k != "hashed"} for entry in _active_keys.values() if not entry.get("revoked")]
        return _ok({"keys": keys, "count": len(keys)})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/keys", 500)


@api_v1_bp.route("/keys/<key_id>", methods=["DELETE"])
@_auth_required
def revoke_api_key(key_id: str):
    try:
        with _keys_lock:
            entry = _active_keys.get(key_id)
            if not entry:
                return _err(f"Key '{key_id}' not found", 404)
            _active_keys[key_id]["revoked"] = True
        _append_audit(action="api_key_revoked", resource=f"key:{key_id}", user=g.get("auth_method", "unknown"), status="success", detail="API key revoked")
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
    last_runs: dict = {}
    for entry in reversed(history):
        name = entry.get("workflow")
        if name and name not in last_runs:
            last_runs[name] = entry
    workflows = [{"name": name, **meta, "last_run": last_runs.get(name)} for name, meta in _AVAILABLE_WORKFLOWS.items()]
    return _ok({"workflows": workflows, "count": len(workflows)})


@api_v1_bp.route("/workflow/run", methods=["POST"])
@_auth_required
def run_workflow():
    try:
        data = request.get_json(force=True, silent=True) or {}
        name = data.get("workflow", "post_publish").strip().lower()
        if name not in _AVAILABLE_WORKFLOWS:
            return _err(f"Unknown workflow '{name}'. Available: {sorted(_AVAILABLE_WORKFLOWS.keys())}", 400)
        if name == "post_publish":
            from scheduler.post_scheduler import scheduler
            result = scheduler.run_content_now()
            entry = {
                "workflow": name, "triggered_by": "api",
                "ok": result.get("ok", False), "duration_ms": result.get("duration_ms"),
                "topic": result.get("topic"), "platforms": result.get("platforms"),
                "error": result.get("error"),
                "ran_at": datetime.now(timezone.utc).isoformat(),
            }
            with _workflow_history_lock:
                _workflow_history.append(entry)
            if result.get("ok"):
                return _ok({"workflow": name, "result": result, "ran_at": entry["ran_at"]})
            return _err(result.get("error", "Workflow failed"), 500)
        return _err(f"Workflow '{name}' runner not implemented", 501)
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/workflow/run", 500)


@api_v1_bp.route("/workflow/history", methods=["GET"])
@_auth_required
def workflow_history():
    try:
        limit = min(20, max(1, int(request.args.get("limit", 20))))
        with _workflow_history_lock:
            entries = list(_workflow_history)[-limit:]
        return _ok({"history": list(reversed(entries)), "count": len(entries)})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/workflow/history", 500)


@api_v1_bp.route("/workflow/status", methods=["GET"])
@_auth_required
def workflow_status():
    try:
        from scheduler.post_scheduler import scheduler
        sched = scheduler.get_status()
        running = sched.get("running", False)
        return _ok({"status": "running" if running else "idle", "progress": 100 if running else 0, "current_step": "Scheduler active" if running else "Idle"})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/workflow/status", 500)


@api_v1_bp.route("/workflow/pause", methods=["POST"])
@_auth_required
def workflow_pause():
    try:
        from scheduler.post_scheduler import scheduler
        scheduler.stop()
        return _ok({"ok": True, "status": "paused"})
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/workflow/pause", 500)


@api_v1_bp.route("/workflow/resume", methods=["POST"])
@_auth_required
def workflow_resume():
    try:
        from scheduler.post_scheduler import scheduler
        scheduler.start()
        return _ok({"ok": True, "status": "running"})
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/workflow/resume", 500)


@api_v1_bp.route("/workflow/trigger", methods=["POST"])
@_auth_required
def workflow_trigger():
    try:
        from scheduler.post_scheduler import scheduler
        result = scheduler.run_content_now()
        return _ok({"ok": result.get("ok", False), "job_id": result.get("topic", "manual")})
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/workflow/trigger", 500)


@api_v1_bp.route("/workflow/<workflow_id>/cancel", methods=["POST"])
@_auth_required
def cancel_workflow(workflow_id):
    """Cancel a running or paused workflow."""
    try:
        with _workflow_history_lock:
            matching = [e for e in _workflow_history if e.get("id") == workflow_id or e.get("topic") == workflow_id]
            if not matching:
                return _err(f"Workflow '{workflow_id}' not found", 404)
            entry = matching[0]

        if entry.get("status") not in ("running", "paused", None):
            return _err(f"Cannot cancel workflow with status: {entry.get('status', 'unknown')}", 400)

        entry["status"] = "cancelled"
        entry["cancelled_at"] = datetime.now(timezone.utc).isoformat()

        _append_audit(action="workflow_cancelled", resource=f"workflow:{workflow_id}", status="success", detail="Workflow cancelled by user")

        return _ok({"cancelled": True, "workflow_id": workflow_id, "status": "cancelled"})
    except Exception as exc:
        return _safe_err(exc, f"POST /api/v1/workflow/{workflow_id}/cancel", 500)


@api_v1_bp.route("/workflow/<workflow_id>/rollback", methods=["POST"])
@_auth_required
def rollback_workflow(workflow_id):
    """Force rollback of a workflow."""
    try:
        with _workflow_history_lock:
            matching = [e for e in _workflow_history if e.get("id") == workflow_id or e.get("topic") == workflow_id]
            if not matching:
                return _err(f"Workflow '{workflow_id}' not found", 404)
            entry = matching[0]

        entry["status"] = "rolled_back"
        entry["rolled_back_at"] = datetime.now(timezone.utc).isoformat()

        _append_audit(action="workflow_rolled_back", resource=f"workflow:{workflow_id}", status="success", detail="Workflow rolled back")

        return _ok({"rolled_back": True, "workflow_id": workflow_id, "status": "rolled_back"})
    except Exception as exc:
        return _safe_err(exc, f"POST /api/v1/workflow/{workflow_id}/rollback", 500)


# ═══════════════════════════════════════════════════════════════════════════
# WEBHOOK RECEIVERS
# ═══════════════════════════════════════════════════════════════════════════

@api_v1_bp.route("/webhooks/facebook", methods=["GET", "POST"])
def facebook_webhook():
    """Facebook Graph API webhook — verification challenge + real-time events."""
    if request.method == "GET":
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")

        if mode == "subscribe":
            from repositories.social_account_repository import SocialAccountRepository
            try:
                repo = _get_container().resolve(SocialAccountRepository)
                accounts = repo.find_by_platform("facebook")
                for acc in accounts:
                    pc = getattr(acc, "platform_config", {}) or {}
                    if pc.get("verify_token") == token:
                        logger.info("Facebook webhook verified for account %s", acc.id)
                        return challenge, 200
            except Exception:
                pass

        logger.warning("Facebook webhook verification failed")
        return "Verification failed", 403

    # POST — process events
    try:
        payload = request.get_json(force=True, silent=True) or {}
        logger.info("Facebook webhook received: %s", str(payload)[:500])

        for entry in payload.get("entry", []):
            page_id = entry.get("id")
            for change in entry.get("changes", []):
                if change.get("field") == "feed":
                    try:
                        container = _get_container()
                        if container:
                            from core.redis_client import UpstashRedisClient
                            redis = container.resolve(UpstashRedisClient)
                            redis.delete(f"social_account:facebook:{page_id}:health")
                    except Exception:
                        pass

        _append_audit(action="webhook_facebook", resource="webhook:facebook", status="success", detail=f"Received {len(payload.get('entry', []))} entries")
        return "OK", 200
    except Exception as exc:
        logger.error("Facebook webhook error: %s", exc)
        return "Error processing webhook", 500


@api_v1_bp.route("/webhooks/twitter", methods=["POST"])
def twitter_webhook():
    """Twitter/X CRC and event receiver."""
    try:
        payload = request.get_json(force=True, silent=True) or {}
        logger.info("Twitter webhook received: %s", str(payload)[:500])
        _append_audit(action="webhook_twitter", resource="webhook:twitter", status="success", detail="Twitter webhook processed")
        return "OK", 200
    except Exception as exc:
        logger.error("Twitter webhook error: %s", exc)
        return "Error processing webhook", 500


@api_v1_bp.route("/webhooks/linkedin", methods=["POST"])
def linkedin_webhook():
    """LinkedIn organization webhook receiver."""
    try:
        payload = request.get_json(force=True, silent=True) or {}
        logger.info("LinkedIn webhook received: %s", str(payload)[:500])
        _append_audit(action="webhook_linkedin", resource="webhook:linkedin", status="success", detail="LinkedIn webhook processed")
        return "OK", 200
    except Exception as exc:
        logger.error("LinkedIn webhook error: %s", exc)
        return "Error processing webhook", 500


@api_v1_bp.route("/webhooks/instagram", methods=["GET", "POST"])
def instagram_webhook():
    """Instagram Graph API webhook (via Facebook)."""
    if request.method == "GET":
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        if mode == "subscribe" and token:
            return challenge, 200
        return "Verification failed", 403

    try:
        payload = request.get_json(force=True, silent=True) or {}
        logger.info("Instagram webhook received: %s", str(payload)[:500])
        return "OK", 200
    except Exception as exc:
        logger.error("Instagram webhook error: %s", exc)
        return "Error processing webhook", 500


# ═══════════════════════════════════════════════════════════════════════════
# SCAN (top-level shortcuts)
# ═══════════════════════════════════════════════════════════════════════════

@api_v1_bp.route("/scan", methods=["POST"])
@_auth_required
def trigger_scan_shortcut():
    try:
        from services.guardian_service import GuardianService
        container = _get_container()
        if not container:
            return _err("Guardian not available", 503)
        svc = container.try_resolve(GuardianService)
        if not svc or not svc.is_configured:
            return _err("Guardian requires GitHub credentials", 503)
        t = _threading_mod.Thread(target=svc.scan_all, daemon=True, name="api-scan-shortcut")
        t.start()
        return _ok({"started": True, "message": "Code Guardian scan started"}, 202)
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/scan", 500)


@api_v1_bp.route("/scan", methods=["GET"])
def get_scan_status():
    try:
        from services.guardian_service import GuardianService
        container = _get_container()
        if not container:
            return _ok({"configured": False, "issues": []})
        svc = container.try_resolve(GuardianService)
        if not svc:
            return _ok({"configured": False, "issues": []})
        status = svc.get_status()
        issues = svc.get_issues() if hasattr(svc, "get_issues") else []
        return _ok({**status, "issues": issues[:50]})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/scan", 500)


# ═══════════════════════════════════════════════════════════════════════════
# SYSTEM HEALTH & CONNECTORS
# ═══════════════════════════════════════════════════════════════════════════

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
            "as_of": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/system/health", 500)


@api_v1_bp.route("/system/connectors", methods=["GET"])
@_auth_required
def get_system_connectors():
    try:
        from services.connectors.connector_factory import supported_platforms
        platforms = supported_platforms()
        return _ok({"supported_connectors": platforms, "count": len(platforms)})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/system/connectors", 500)


@api_v1_bp.route("/health/deep", methods=["GET"])
def health_deep():
    try:
        from config.settings import settings as _s
        services: dict = {}
        gemini_ok = bool(getattr(_s, "gemini_key", ""))
        services["gemini"] = {"status": "ok" if gemini_ok else "error", "latency_ms": 0, "page_name": "Gemini AI", "configured": gemini_ok}
        fb_ok = bool(getattr(_s, "page_access_token", "")) and bool(getattr(_s, "page_id", ""))
        services["facebook"] = {"status": "ok" if fb_ok else "error", "latency_ms": 0, "page_name": "Facebook API", "configured": fb_ok}
        supa_ok = bool(getattr(_s, "supabase_enabled", False))
        services["supabase"] = {"status": "ok" if supa_ok else "degraded", "latency_ms": 0, "page_name": "Supabase", "configured": supa_ok}
        try:
            container = _get_container()
            if container:
                from core.redis_client import UpstashRedisClient
                redis = container.resolve(UpstashRedisClient)
                redis_health = redis.health_check()
                services["redis"] = {"status": "ok" if redis_health.get("redis") == "healthy" else "degraded", "latency_ms": redis_health.get("latency_ms", 0), "page_name": "Upstash Redis", "configured": redis_health.get("redis") != "disabled"}
        except Exception:
            services["redis"] = {"status": "disabled", "latency_ms": 0, "page_name": "Upstash Redis", "configured": False}
        overall = "ok" if all(s["status"] == "ok" for s in services.values()) else "degraded"
        return jsonify({"status": overall, "version": "10.0.0", "services": services})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/health/deep", 500)


# ═══════════════════════════════════════════════════════════════════════════
# STATUS, FEATURES, RATE LIMITS, TENANTS
# ═══════════════════════════════════════════════════════════════════════════

@api_v1_bp.route("/status", methods=["GET"])
def get_status():
    try:
        from config.settings import settings as _s
        return _ok({
            "version": "10.0.0",
            "environment": getattr(_s, "environment", "production"),
            "config": {
                "ai_provider": getattr(_s, "ai_provider", "gemini"),
                "facebook_configured": bool(getattr(_s, "page_access_token", "")),
                "gemini_key_configured": bool(getattr(_s, "gemini_key", "")),
                "gemini_model": getattr(_s, "gemini_model", "gemini-2.5-pro"),
                "guardian_enabled": bool(getattr(_s, "guardian_enabled", False)),
                "supabase_enabled": bool(getattr(_s, "supabase_enabled", False)),
            },
        })
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/status", 500)


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
        return _safe_err(exc, "GET /api/v1/features", 500)


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
            if supabase_service and supabase_service.client:
                supabase_service.client.table("settings").upsert({
                    "key": f"feature_{feature}", "value": str(enabled).lower(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }, on_conflict="key").execute()
        except Exception:
            pass
        return _ok({"feature": feature, "enabled": enabled})
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/features/toggle", 500)


@api_v1_bp.route("/rate-limits", methods=["GET"])
def get_rate_limits():
    try:
        from utils.rate_limiter import rate_limiter
        return _ok({"rate_limits": rate_limiter.get_config()})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/rate-limits", 500)


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
        return _safe_err(exc, "POST /api/v1/rate-limits/unblock", 500)


@api_v1_bp.route("/tenants", methods=["GET"])
@_auth_required
def get_tenants():
    try:
        from services.supabase import supabase_service
        if supabase_service and supabase_service.client:
            result = supabase_service.client.table("tenants").select("*").eq("status", "active").execute()
            return _ok({"tenants": result.data or []})
        return _ok({"tenants": [], "note": "Supabase not configured"})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/tenants", 500)


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
        return _safe_err(exc, "POST /api/v1/tenants/switch", 500)


# ═══════════════════════════════════════════════════════════════════════════
# NOTIFICATIONS & INTEGRATIONS
# ═══════════════════════════════════════════════════════════════════════════

@api_v1_bp.route("/notifications", methods=["GET"])
@_auth_required
def get_notifications():
    try:
        limit = min(50, max(1, int(request.args.get("limit", 20))))
        from services.supabase import supabase_service
        if supabase_service and supabase_service.client:
            result = supabase_service.client.table("notifications").select("*").order("created_at", desc=True).limit(limit).execute()
            return _ok({"notifications": result.data or [], "count": len(result.data or [])})
        return _ok({"notifications": [], "count": 0})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/notifications", 500)


@api_v1_bp.route("/notifications/<notif_id>/read", methods=["POST"])
@_auth_required
def mark_notification_read(notif_id: str):
    try:
        from services.supabase import supabase_service
        if supabase_service and supabase_service.client:
            supabase_service.client.table("notifications").update({"read": True}).eq("id", notif_id).execute()
        return _ok({"read": True, "id": notif_id})
    except Exception as exc:
        return _safe_err(exc, f"POST /api/v1/notifications/{notif_id}/read", 500)


@api_v1_bp.route("/integrations", methods=["GET"])
@_auth_required
def get_integrations():
    try:
        from config.settings import settings as _s
        integrations = [
            {"id": "facebook", "name": "Facebook", "category": "Social Media", "connected": bool(getattr(_s, "page_access_token", "")), "status": "active" if bool(getattr(_s, "page_access_token", "")) else "disconnected"},
            {"id": "gemini", "name": "Google Gemini AI", "category": "AI", "connected": bool(getattr(_s, "gemini_key", "")), "status": "active" if bool(getattr(_s, "gemini_key", "")) else "disconnected"},
            {"id": "supabase", "name": "Supabase", "category": "Databases", "connected": _s.supabase_enabled, "status": "active" if _s.supabase_enabled else "disconnected"},
            {"id": "huggingface", "name": "Hugging Face", "category": "AI", "connected": bool(getattr(_s, "hf_token", "")), "status": "active" if bool(getattr(_s, "hf_token", "")) else "disconnected"},
            {"id": "github", "name": "GitHub", "category": "DevOps", "connected": bool(getattr(_s, "github_token", "")), "status": "active" if bool(getattr(_s, "github_token", "")) else "disconnected"},
            {"id": "twitter", "name": "Twitter / X", "category": "Social Media", "connected": bool(getattr(_s, "twitter_access_token", "")), "status": "active" if bool(getattr(_s, "twitter_access_token", "")) else "disconnected"},
            {"id": "linkedin", "name": "LinkedIn", "category": "Social Media", "connected": bool(getattr(_s, "linkedin_access_token", "")), "status": "active" if bool(getattr(_s, "linkedin_access_token", "")) else "disconnected"},
            {"id": "whatsapp", "name": "WhatsApp", "category": "Messaging", "connected": bool(getattr(_s, "whatsapp_token", "")), "status": "active" if bool(getattr(_s, "whatsapp_token", "")) else "disconnected"},
            {"id": "redis", "name": "Upstash Redis", "category": "Infrastructure", "connected": bool(getattr(_s, "upstash_redis_rest_url", "")), "status": "active" if bool(getattr(_s, "upstash_redis_rest_url", "")) else "disconnected"},
        ]
        connected_count = sum(1 for i in integrations if i["connected"])
        return _ok({"integrations": integrations, "count": len(integrations), "connected": connected_count})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/integrations", 500)


# ═══════════════════════════════════════════════════════════════════════════
# AUDIT LOGS
# ═══════════════════════════════════════════════════════════════════════════

@api_v1_bp.route("/audit-logs", methods=["GET"])
@_auth_required
def get_audit_logs():
    try:
        limit = min(200, max(1, int(request.args.get("limit", 50))))
        action = request.args.get("action", "").strip().lower()
        user = request.args.get("user", "").strip().lower()
        from services.supabase import supabase_service
        if supabase_service and supabase_service.client:
            try:
                q = supabase_service.client.table("audit_logs").select("*").order("created_at", desc=True).limit(limit)
                result = q.execute()
                logs = result.data or []
                return _ok({"logs": logs, "count": len(logs), "source": "supabase"})
            except Exception:
                pass
        with _audit_log_lock:
            logs = list(_audit_log)
        if action:
            logs = [l for l in logs if action in l.get("action", "").lower()]
        if user:
            logs = [l for l in logs if user in l.get("user", "").lower()]
        logs = logs[:limit]
        if not logs:
            from config.settings import settings as _s
            logs = [{"id": "seed-0", "timestamp": datetime.now(timezone.utc).isoformat(), "user": "system", "action": "server_start", "resource": "application", "status": "success", "detail": f"Kanyoza Systems v10 started (env={_s.environment})"}]
        return _ok({"logs": logs, "count": len(logs), "source": "memory"})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/audit-logs", 500)


# ═══════════════════════════════════════════════════════════════════════════
# WORKSPACE ANALYTICS
# ═══════════════════════════════════════════════════════════════════════════

@api_v1_bp.route("/workspaces/<workspace_id>/analytics", methods=["GET"])
@_auth_required
def get_workspace_analytics(workspace_id):
    from analytics.tracker import analytics
    try:
        _require_workspace_ownership("workspace", workspace_id)
        stats = analytics.get_stats()
        return _ok({"workspace_id": workspace_id, "analytics": stats, "as_of": datetime.now(timezone.utc).isoformat()})
    except Exception as exc:
        return _safe_err(exc, f"GET /api/v1/workspaces/{workspace_id}/analytics", 500)


@api_v1_bp.route("/brands/<brand_id>/analytics", methods=["GET"])
@_auth_required
def get_brand_analytics(brand_id):
    from analytics.tracker import analytics
    try:
        _require_workspace_ownership("brand", brand_id)
        stats = analytics.get_stats()
        return _ok({"brand_id": brand_id, "analytics": stats, "as_of": datetime.now(timezone.utc).isoformat()})
    except Exception as exc:
        return _safe_err(exc, f"GET /api/v1/brands/{brand_id}/analytics", 500)
