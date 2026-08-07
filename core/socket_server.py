"""
Kanyoza Systems AI Platform v13.0 — WebSocket / SocketIO Server (FastAPI).

Replaces Flask-SocketIO singleton with python-socketio ASGI mode.
All existing EventBus → SocketIO mappings preserved.
Frontend socket.io-client works with ZERO changes.

v13.0 — Dashboard now reads from BrandSchedulerManager (active scheduler)
        instead of the legacy post_scheduler.

Design
------
* One AsyncServer singleton created here. Mounted as ASGI app in main.py.
* All browser clients connect to the /dashboard namespace.
* On connect they receive an immediate stats snapshot.
* EventBus events are forwarded as named SocketIO events.
* In-memory session counters track real-time connection counts.

EventBus → SocketIO mapping (unchanged)
---------------------------------------
  MessageReceivedEvent  →  new_message
  PostPublishedEvent    →  post_published
  ScanCompletedEvent    →  scan_complete
  ProviderFailedEvent   →  provider_failed
  PostGeneratedEvent    →  post_generated
  ImageRenderedEvent    →  image_rendered
  WorkerError           →  worker_error
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import threading
from datetime import datetime, timezone
from typing import Any

import socketio

logger = logging.getLogger("kanyoza.socket")

# ---------------------------------------------------------------------------
# Singleton — python-socketio AsyncServer (ASGI mode)
# ---------------------------------------------------------------------------

def _get_sio_cors_origins():
    try:
        from config.settings import settings
        if settings.is_production:
            origins = settings.cors_allowed_origins
            return origins if origins else []
    except Exception:
        pass
    return '*'

sio = socketio.AsyncServer(
    async_mode='asgi',
    cors_allowed_origins=_get_sio_cors_origins(),
    logger=False,
    engineio_logger=False,
    ping_timeout=60,
    ping_interval=25,
)


# ---------------------------------------------------------------------------
# In-memory live counters
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_counters: dict[str, int] = {
    "messages_today": 0,
    "posts_today": 0,
    "active_connections": 0,
    "events_emitted": 0,
}
_boot_time: datetime = datetime.now(timezone.utc)


def increment(key: str, by: int = 1) -> None:
    with _lock:
        _counters[key] = _counters.get(key, 0) + by


def get_counters() -> dict:
    with _lock:
        return dict(_counters)


def get_uptime_seconds() -> int:
    delta = datetime.now(timezone.utc) - _boot_time
    return int(delta.total_seconds())


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

async def emit_to_dashboard(event: str, data: dict[str, Any]) -> None:
    """Async emit to all clients in the /dashboard namespace."""
    try:
        await sio.emit(event, data, namespace="/dashboard")
        increment("events_emitted")
    except Exception as exc:
        logger.debug("socket emit failed (%s): %s", event, exc)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def _validate_token(token: str) -> bool:
    """Constant-time token validation against MASTER_API_TOKEN."""
    from config.settings import settings
    from config.environments import Environment

    master_token = getattr(settings, "master_api_token", "") or ""

    if not master_token:
        env = Environment.from_string(getattr(settings, "environment", "production"))
        if env.is_production:
            logger.warning("Dashboard: MASTER_API_TOKEN not set in production — rejecting connection.")
            return False
        return True

    if not token:
        return False

    return hmac.compare_digest(token, master_token)


# ---------------------------------------------------------------------------
# Multi-tenant platform health checks
# ---------------------------------------------------------------------------

def _check_facebook_health() -> dict:
    """Check Facebook service health — multi-tenant aware."""
    try:
        from services.facebook import facebook as fb_svc
        if fb_svc.is_configured:
            return {"ok": True, "platform": "facebook", "mode": "global"}
        try:
            from repositories.social_account_repository import SocialAccountRepository
            repo = SocialAccountRepository()
            accounts = repo.find_by_platform("facebook")
            if accounts:
                enabled_with_creds = [
                    a for a in accounts
                    if getattr(a, 'enabled', False) and getattr(a, 'access_token', None)
                ]
                if enabled_with_creds:
                    return {"ok": True, "platform": "facebook", "mode": "multi_tenant", "accounts_configured": len(enabled_with_creds)}
        except Exception as e:
            logger.debug("Multi-tenant Facebook health check failed: %s", e)
        return {"ok": False, "platform": "facebook", "mode": "unconfigured"}
    except Exception as e:
        logger.debug("Facebook health check error: %s", e)
        return {"ok": False, "platform": "facebook", "error": str(e)[:100]}


def _check_twitter_health() -> dict:
    try:
        from config.settings import settings
        return {"ok": bool(settings.twitter_access_token), "platform": "twitter"}
    except Exception:
        return {"ok": False, "platform": "twitter"}


def _check_linkedin_health() -> dict:
    try:
        from config.settings import settings
        return {"ok": bool(settings.linkedin_access_token), "platform": "linkedin"}
    except Exception:
        return {"ok": False, "platform": "linkedin"}


def _check_whatsapp_health() -> dict:
    try:
        from config.settings import settings
        return {"ok": bool(settings.whatsapp_token), "platform": "whatsapp"}
    except Exception:
        return {"ok": False, "platform": "whatsapp"}


def _check_telegram_health() -> dict:
    try:
        from config.settings import settings
        return {"ok": bool(settings.telegram_bot_token), "platform": "telegram"}
    except Exception:
        return {"ok": False, "platform": "telegram"}


def _check_instagram_health() -> dict:
    try:
        from repositories.social_account_repository import SocialAccountRepository
        repo = SocialAccountRepository()
        accounts = repo.find_by_platform("instagram")
        if accounts:
            enabled = [a for a in accounts if getattr(a, 'enabled', False)]
            if enabled:
                return {"ok": True, "platform": "instagram", "accounts_configured": len(enabled)}
        return {"ok": False, "platform": "instagram"}
    except Exception:
        return {"ok": False, "platform": "instagram"}


def _build_integration_status() -> list[dict]:
    platforms = [
        _check_facebook_health(), _check_twitter_health(), _check_linkedin_health(),
        _check_instagram_health(), _check_whatsapp_health(), _check_telegram_health(),
    ]
    return [
        {"platform": p["platform"], "healthy": p.get("ok", False),
         "mode": p.get("mode"), "accounts_configured": p.get("accounts_configured")}
        for p in platforms
    ]


# ---------------------------------------------------------------------------
# Scheduler stats — reads from BrandSchedulerManager (v13.0)
# ---------------------------------------------------------------------------

def _get_scheduler_stats() -> dict:
    """Get scheduler stats from BrandSchedulerManager.

    Replaces the legacy post_scheduler reference.
    Falls back gracefully if the scheduler isn't running yet.
    """
    try:
        from scheduler.brand_scheduler import BrandSchedulerManager
        from service_container import get_container

        container = get_container()
        if container:
            manager = container.try_resolve(BrandSchedulerManager)
            if manager:
                all_status = manager.get_all_status()
                return {
                    "running": True,
                    "brands": len(all_status),
                    "brands_active": sum(1 for s in all_status if s.get("running")),
                    "posts_today": sum(s.get("posts_today", 0) for s in all_status),
                    "details": all_status,
                }
    except Exception as e:
        logger.debug("BrandSchedulerManager not available for dashboard stats: %s", e)

    return {"running": False, "brands": 0}


# ---------------------------------------------------------------------------
# Stats builder
# ---------------------------------------------------------------------------

def build_live_stats() -> dict:
    """Assemble a full live-stats dict for Socket.IO stats event."""
    stats: dict = {
        "server_time": datetime.now(timezone.utc).isoformat(),
        "uptime_seconds": get_uptime_seconds(),
        "counters": get_counters(),
    }

    # Analytics
    try:
        from analytics.tracker import analytics
        stats["analytics"] = analytics.get_stats()
    except Exception:
        stats["analytics"] = {}

    # Scheduler — now reads from BrandSchedulerManager
    stats["scheduler"] = _get_scheduler_stats()

    # Metrics
    try:
        from utils.metrics import metrics
        stats["metrics"] = metrics.get_snapshot()
    except Exception:
        stats["metrics"] = {}

    # Service health
    services: dict = {}
    try:
        from config.settings import settings
        services["gemini"] = {"ok": bool(settings.gemini_key)}
        services["supabase"] = {"ok": settings.supabase_enabled}
    except Exception:
        services["gemini"] = {"ok": False}
        services["supabase"] = {"ok": False}

    services["facebook"] = _check_facebook_health()
    services["twitter"] = _check_twitter_health()
    services["linkedin"] = _check_linkedin_health()
    services["whatsapp"] = _check_whatsapp_health()
    services["telegram"] = _check_telegram_health()
    services["instagram"] = _check_instagram_health()
    stats["services"] = services
    stats["integration_status"] = _build_integration_status()

    # Top-level aliases for frontend compatibility
    counters = stats.get("counters", {})
    overview = stats.get("analytics", {}).get("overview", {})
    stats["messages_today"] = overview.get("messages_received", counters.get("messages_today", 0))
    stats["posts_published"] = overview.get("posts_published", counters.get("posts_today", 0))
    stats["active_users"] = overview.get("active_users_24h", counters.get("active_connections", 0))
    stats["api_calls_today"] = counters.get("events_emitted", 0)

    return stats


# ---------------------------------------------------------------------------
# Namespace handlers — /dashboard
# ---------------------------------------------------------------------------

@sio.event(namespace="/dashboard")
async def connect(sid, environ, auth):
    token = ""
    if isinstance(auth, dict):
        token = str(auth.get("token", ""))
    if not token:
        query_string = environ.get('QUERY_STRING', '')
        for param in query_string.split('&'):
            if param.startswith('token='):
                token = param.split('=', 1)[1]
                break

    if not _validate_token(token):
        logger.warning("Dashboard: rejected unauthenticated SocketIO connection")
        return False

    increment("active_connections")
    logger.info("Dashboard client connected (total=%d)", _counters["active_connections"])
    await sio.emit("stats", build_live_stats(), namespace="/dashboard", to=sid)


@sio.event(namespace="/dashboard")
async def disconnect(sid):
    with _lock:
        _counters["active_connections"] = max(0, _counters["active_connections"] - 1)
    logger.info("Dashboard client disconnected (total=%d)", _counters["active_connections"])


@sio.event(namespace="/dashboard")
async def request_stats(sid, data=None):
    await sio.emit("stats", build_live_stats(), namespace="/dashboard", to=sid)


@sio.event(namespace="/dashboard")
async def ping(sid, data=None):
    await sio.emit("pong", {"ts": datetime.now(timezone.utc).isoformat()}, namespace="/dashboard", to=sid)


# ---------------------------------------------------------------------------
# ASGI app factory
# ---------------------------------------------------------------------------

def create_socket_app(fastapi_app):
    """Wraps a FastAPI app with the SocketIO ASGI server."""
    return socketio.ASGIApp(sio, fastapi_app)
