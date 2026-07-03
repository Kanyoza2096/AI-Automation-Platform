"""
Kanyoza Systems AI Platform v10 — REST API v1 Blueprint (M8).

Versioned endpoints at /api/v1/* that mirror and extend the v9 routes in
routes/api.py.  All state-changing endpoints require the X-API-Key header
(value = OWNER_PSID or APP_SECRET).

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
    Check that the incoming request has a valid X-API-Key header.

    Accepted values (priority order):
    1. APP_SECRET (HMAC secret from settings)
    2. OWNER_PSID (the bot owner's Messenger sender ID)

    Returns True when the key is valid, False otherwise.
    """
    from config.settings import settings as _s
    key = request.headers.get("X-API-Key", "").strip()
    if not key:
        return False
    api_secret = getattr(_s, "app_secret", "") or ""
    owner_psid = getattr(_s, "owner_psid", "") or ""
    return bool((api_secret and key == api_secret) or (owner_psid and key == owner_psid))


def _auth_required(fn):
    """Decorator: reject requests without a valid X-API-Key with 401."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not _require_auth():
            return jsonify({"error": "unauthorized", "message": "Valid X-API-Key header required"}), 401
        return fn(*args, **kwargs)
    return wrapper


def _ok(data: dict, status: int = 200):
    return jsonify({"ok": True, **data}), status


def _err(msg: str, status: int = 400):
    return jsonify({"ok": False, "error": msg}), status


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
        stats = analytics.get_stats()
        all_posts = stats.get("recent_posts", [])
        start = (page - 1) * per_page
        posts = all_posts[start: start + per_page]
        return _ok({
            "posts": posts,
            "page": page,
            "per_page": per_page,
            "total": len(all_posts),
        })
    except Exception as exc:
        logger.error("GET /api/v1/posts error: %s", exc)
        return _err(str(exc), 500)


@api_v1_bp.route("/posts", methods=["POST"])
@_auth_required
def create_post():
    from scheduler.post_scheduler import scheduler
    try:
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
        allowed_keys = {"post_interval_hours", "news_interval_hours", "enabled"}
        unknown = set(data.keys()) - allowed_keys
        if unknown:
            return _err(f"Unknown keys: {sorted(unknown)}", 400)
        result = scheduler.update_config(**{k: v for k, v in data.items() if k in allowed_keys})
        return _ok({"updated": True, "config": result})
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
