"""
Kanyoza Systems AI Platform v13.1 — WebSocket / SocketIO Server (FastAPI).

UNIFIED AUTHENTICATION:
  - auth.token (Socket.IO handshake)
  - Authorization: Bearer <token> (HTTP header)
  - X-API-Key: <key> (HTTP header)
  - Query-string tokens are NOT accepted

All existing EventBus → SocketIO mappings preserved.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional

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
# UNIFIED AUTHENTICATION — v13.1
# ---------------------------------------------------------------------------

def _extract_token_from_socket(
    environ: Optional[Dict[str, Any]] = None,
    auth: Optional[Dict[str, Any]] = None,
) -> str:
    """Extract token from Socket.IO connection using ALL auth methods.

    Order:
    1. auth.token (Socket.IO handshake)
    2. Authorization: Bearer <token> (HTTP header)
    3. X-API-Key: <key> (HTTP header)
    """
    token = ""

    # 1. Socket.IO auth dict
    if isinstance(auth, dict):
        token = str(auth.get("token", "") or "").strip()

    # 2. Authorization: Bearer header
    if not token and environ:
        headers = environ.get("headers", {}) or {}
        for key, value in headers.items():
            key_str = key.decode("utf-8", errors="ignore") if isinstance(key, bytes) else str(key)
            if key_str.lower() == "authorization":
                value_str = value.decode("utf-8", errors="ignore") if isinstance(value, bytes) else str(value)
                if value_str.startswith("Bearer "):
                    token = value_str[7:].strip()
                break

    # 3. X-API-Key header
    if not token and environ:
        headers = environ.get("headers", {}) or {}
        for key, value in headers.items():
            key_str = key.decode("utf-8", errors="ignore") if isinstance(key, bytes) else str(key)
            if key_str.lower() == "x-api-key":
                value_str = value.decode("utf-8", errors="ignore") if isinstance(value, bytes) else str(value)
                token = value_str.strip()
                break

    # Query-string credentials are intentionally rejected. Tokens in URLs can
    # leak through proxy/access logs, browser history and observability tooling.
    return token


def _validate_token(token: str) -> str | None:
    """Validate master or KCPG service credential and return auth method."""
    from config.settings import settings
    if not token:
        return None
    for attr, method in (("master_api_token", "master"), ("python_service_token", "python_service")):
        expected = (getattr(settings, attr, "") or "").strip()
        if expected and hmac.compare_digest(token, expected):
            return method
    return None


# ---------------------------------------------------------------------------
# Multi-tenant platform health checks
# ---------------------------------------------------------------------------

def _check_facebook_health() -> dict:
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


def _get_scheduler_stats() -> dict:
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


def build_live_stats() -> dict:
    stats: dict = {
        "server_time": datetime.now(timezone.utc).isoformat(),
        "uptime_seconds": get_uptime_seconds(),
        "counters": get_counters(),
    }

    try:
        from analytics.tracker import analytics
        stats["analytics"] = analytics.get_stats()
    except Exception:
        stats["analytics"] = {}

    stats["scheduler"] = _get_scheduler_stats()

    try:
        from utils.metrics import metrics
        stats["metrics"] = metrics.get_snapshot()
    except Exception:
        stats["metrics"] = {}

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

    counters = stats.get("counters", {})
    overview = stats.get("analytics", {}).get("overview", {})
    stats["messages_today"] = overview.get("messages_received", counters.get("messages_today", 0))
    stats["posts_published"] = overview.get("posts_published", counters.get("posts_today", 0))
    stats["active_users"] = overview.get("active_users_24h", counters.get("active_connections", 0))
    stats["api_calls_today"] = counters.get("events_emitted", 0)

    return stats


# ---------------------------------------------------------------------------
# Namespace handlers — /dashboard (UNIFIED AUTH)
# ---------------------------------------------------------------------------

@sio.event(namespace="/dashboard")
async def connect(sid, environ, auth):
    """Connect with unified authentication."""
    token = _extract_token_from_socket(environ, auth)

    if not _validate_token(token):
        logger.warning("Dashboard: rejected unauthenticated SocketIO connection (sid=%s)", sid)
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
