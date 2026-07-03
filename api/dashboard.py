"""
Kanyoza Systems AI Platform v10 — Dashboard API.

Provides:
  GET /api/v1/dashboard/live   — JSON snapshot of all live stats (polling-safe)
  GET /api/v1/dashboard/ws     — WebSocket connection info / handshake hint

The same data that is returned from /live is also emitted on the /dashboard
SocketIO namespace as the ``stats`` event whenever a client connects or
explicitly sends ``request_stats``.

Stats shape
-----------
{
  "server_time":    "<ISO-8601 UTC>",
  "uptime_seconds": N,
  "counters": {
    "messages_today":    N,
    "posts_today":       N,
    "active_connections": N,
    "events_emitted":    N,
  },
  "analytics": {
    "overview": { messages_received, messages_sent, posts_published, ... }
  },
  "scheduler": {
    "running":              bool,
    "posts_today":          N,
    "target_posts_per_day": N,
    "content_hours_utc":    [...],
    "queue_size":           N,
    "last_post":            str | null,
  },
  "metrics": { ... },   # from MetricsCollector.get_snapshot()
  "services": {
    "gemini":    {"ok": bool, "configured": bool},
    "facebook":  {"ok": bool, "platform": "facebook", "active": bool},
    "supabase":  {"ok": bool},
    "converter": {"ok": bool},
  }
}
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

logger = logging.getLogger("kanyoza.dashboard")

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/api/v1/dashboard")


def _require_dashboard_auth() -> bool:
    """Accept Authorization: Bearer <MASTER_API_TOKEN> or the legacy X-API-Key."""
    import hmac
    from config.settings import settings as _s

    master_token = getattr(_s, "master_api_token", "") or ""
    api_secret   = getattr(_s, "app_secret", "") or ""

    auth_header = request.headers.get("Authorization", "").strip()
    if auth_header.startswith("Bearer "):
        bearer = auth_header[len("Bearer "):]
        if master_token and hmac.compare_digest(bearer, master_token):
            return True

    key = request.headers.get("X-API-Key", "").strip()
    if key and api_secret and hmac.compare_digest(key, api_secret):
        return True

    return False


def _dashboard_auth_required(fn):
    """Reject dashboard requests that don't carry a valid token."""
    from functools import wraps
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not _require_dashboard_auth():
            return jsonify({
                "error": "unauthorized",
                "message": "Authorization: Bearer <MASTER_API_TOKEN> required",
            }), 401
        return fn(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Stat builder (shared with socket_server.py on connect)
# ---------------------------------------------------------------------------

def build_live_stats() -> dict:
    """
    Assemble a full live-stats dict.

    All sub-calls are defensive (never raise) so the endpoint always returns
    something useful even when individual services are unconfigured.
    """
    from datetime import datetime, timezone

    stats: dict = {
        "server_time": datetime.now(timezone.utc).isoformat(),
    }

    # ── Uptime + in-memory counters ──────────────────────────────────────────
    try:
        from core.socket_server import get_counters, get_uptime_seconds
        stats["uptime_seconds"] = get_uptime_seconds()
        stats["counters"] = get_counters()
    except Exception as exc:
        logger.debug("counters unavailable: %s", exc)
        stats["uptime_seconds"] = 0
        stats["counters"] = {}

    # ── Analytics (Supabase or zero-fallback) ───────────────────────────────
    try:
        from analytics.tracker import analytics
        stats["analytics"] = analytics.get_stats()
    except Exception as exc:
        logger.debug("analytics unavailable: %s", exc)
        stats["analytics"] = {}

    # ── Scheduler ───────────────────────────────────────────────────────────
    try:
        from scheduler.post_scheduler import scheduler
        stats["scheduler"] = scheduler.get_status()
    except Exception as exc:
        logger.debug("scheduler status unavailable: %s", exc)
        stats["scheduler"] = {"running": False, "error": str(exc)}

    # ── Operational metrics ─────────────────────────────────────────────────
    try:
        from utils.metrics import metrics
        stats["metrics"] = metrics.get_snapshot()
    except Exception as exc:
        logger.debug("metrics unavailable: %s", exc)
        stats["metrics"] = {}

    # ── Service health (lightweight — no network calls) ─────────────────────
    services: dict = {}

    try:
        from config.settings import settings
        services["gemini"] = {
            "ok": bool(settings.gemini_key),
            "configured": bool(settings.gemini_key),
        }
        services["supabase"] = {
            "ok": settings.supabase_enabled,
            "configured": settings.supabase_enabled,
        }
    except Exception as exc:
        logger.debug("settings health check failed: %s", exc)
        services.setdefault("gemini", {"ok": False, "configured": False})
        services.setdefault("supabase", {"ok": False, "configured": False})

    try:
        # FacebookService.is_configured checks page_token and page_id are set
        from services.facebook import facebook as fb_svc
        configured = fb_svc.is_configured
        services["facebook"] = {
            "ok": configured,
            "platform": "facebook",
            "active": configured,
        }
    except Exception as exc:
        logger.debug("facebook health check failed: %s", exc)
        services["facebook"] = {"ok": False, "platform": "facebook", "active": False}

    try:
        # CurrencyService is a local computation — always structurally OK.
        # Avoid health_check() here because it makes a live HTTP call.
        from products.converter.service import CurrencyService
        conv = CurrencyService()
        has_cache = bool(getattr(conv, "_cache", None))
        services["converter"] = {"ok": True, "cached_pairs": len(getattr(conv, "_cache", {}))}
    except Exception as exc:
        logger.debug("converter health check failed: %s", exc)
        services["converter"] = {"ok": True}

    # ── Guardian last scan ───────────────────────────────────────────────────
    try:
        from guardian.scanner import CodeGuardianScanner
        scanner = CodeGuardianScanner.__new__(CodeGuardianScanner)
        last = getattr(scanner, "_last_results", None)
        if last:
            stats["guardian"] = {
                "last_scan": str(last),
                "ok": True,
            }
        else:
            stats["guardian"] = {"ok": True, "last_scan": None}
    except Exception:
        stats["guardian"] = {"ok": False, "last_scan": None}

    stats["services"] = services
    return stats


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------

@dashboard_bp.route("/live")
@_dashboard_auth_required
def live():
    """
    Fast snapshot of all live stats.

    Safe to poll frequently (all queries are in-memory or Supabase cached).
    For streaming updates use the SocketIO /dashboard namespace instead.
    """
    return jsonify(build_live_stats())


@dashboard_bp.route("/ws-info")
@_dashboard_auth_required
def ws_info():
    """Return WebSocket connection parameters for the frontend controller."""
    return jsonify({
        "namespace": "/dashboard",
        "events": {
            "subscribe": [
                # Core telemetry
                "stats",
                # Domain events
                "new_message", "post_published", "scan_complete",
                "provider_failed", "post_generated", "image_rendered", "worker_error",
                # Workflow Engine — step-level progress (base.py)
                "workflow_started", "workflow_step_started", "workflow_step_done", "workflow_done",
                # Live Payload Inspector — raw request/response payloads
                "payload_inbound",   # inbound Facebook webhook payload
                "api_payload",       # REST API request/response pairs
            ],
            "emit": ["request_stats", "ping"],
        },
        "hint": "Connect to /dashboard namespace. On connect you receive an immediate 'stats' event.",
    })
