"""
Kanyoza Systems AI Platform v10 — WebSocket / SocketIO Server.

Provides the singleton SocketIO instance that the rest of the platform uses to
push real-time events to connected frontend controllers.

Design
------
* One SocketIO singleton is created here (`socketio`) and initialised against
  the Flask app in `init_app()`.  The factory and lifecycle modules call this;
  nothing else should create SocketIO instances.

* All browser clients connect to the ``/dashboard`` namespace.
  On connect they receive an immediate ``stats`` snapshot.
  Every domain event published to the EventBus is forwarded as a named
  SocketIO event so the frontend can react without polling.

* In-memory session counters track real-time connection counts and
  event throughput without touching Supabase.

* Uses async_mode='eventlet' — required by gunicorn ``--worker-class eventlet``
  for HTTP→WebSocket upgrade support.  eventlet must be in requirements.txt.

EventBus → SocketIO mapping
---------------------------
  MessageReceivedEvent  →  ``new_message``
  PostPublishedEvent    →  ``post_published``
  ScanCompletedEvent    →  ``scan_complete``
  ProviderFailedEvent   →  ``provider_failed``
  PostGeneratedEvent    →  ``post_generated``
  ImageRenderedEvent    →  ``image_rendered``
  WorkerError           →  ``worker_error``
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

from flask import Flask
from flask_socketio import SocketIO, emit

logger = logging.getLogger("kanyoza.socket")

# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

socketio = SocketIO(
    cors_allowed_origins="*",
    async_mode="eventlet",
    logger=False,
    engineio_logger=False,
    ping_timeout=60,
    ping_interval=25,
)

# ---------------------------------------------------------------------------
# In-memory live counters (survive across connections, reset on restart)
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

def emit_to_dashboard(event: str, data: dict[str, Any]) -> None:
    """Thread-safe emit to all clients in the /dashboard namespace."""
    try:
        socketio.emit(event, data, namespace="/dashboard")
        increment("events_emitted")
    except Exception as exc:
        logger.debug("socket emit failed (%s): %s", event, exc)


# ---------------------------------------------------------------------------
# Namespace handlers
# ---------------------------------------------------------------------------

def _auth_dashboard_client(auth: dict | None = None) -> bool:
    """
    Validate the connecting dashboard client via MASTER_API_TOKEN.

    Token lookup order (most-to-least preferred):
    1. Socket.IO ``auth`` dict — { "token": "<value>" }  (sent by socket.io-client v4 via
       ``io(url, { auth: { token } })``)
    2. HTTP query string — ``?token=<value>``  (legacy / curl-friendly fallback)
    """
    import hmac
    from config.settings import settings as _s
    from config.environments import Environment

    master_token = getattr(_s, "master_api_token", "") or ""

    if not master_token:
        env = Environment.from_string(getattr(_s, "environment", "production"))
        if env.is_production:
            logger.warning(
                "Dashboard: MASTER_API_TOKEN not set in production — "
                "rejecting SocketIO connection for security."
            )
            return False
        return True

    # 1. Socket.IO auth dict (preferred — never leaks into URLs or server logs)
    token = ""
    if isinstance(auth, dict):
        token = str(auth.get("token", "") or "")

    # 2. Query-string fallback (backward compat)
    if not token:
        from flask import request as _req
        try:
            token = _req.args.get("token", "")
        except Exception:
            pass

    if not token:
        return False

    return hmac.compare_digest(token, master_token)


@socketio.on("connect", namespace="/dashboard")
def on_connect(auth=None):
    # ``auth`` is the dict passed by socket.io-client v4 via ``io(url, { auth: {...} })``.
    # Flask-SocketIO delivers it as the first positional argument of the connect handler.
    if not _auth_dashboard_client(auth):
        logger.warning("Dashboard: rejected unauthenticated SocketIO connection")
        return False

    increment("active_connections")
    logger.info("Dashboard client connected (total=%d)", _counters["active_connections"])

    from api.dashboard import build_live_stats
    emit("stats", build_live_stats(), namespace="/dashboard")


@socketio.on("disconnect", namespace="/dashboard")
def on_disconnect():
    with _lock:
        _counters["active_connections"] = max(0, _counters["active_connections"] - 1)
    logger.info("Dashboard client disconnected (total=%d)", _counters["active_connections"])


@socketio.on("request_stats", namespace="/dashboard")
def on_request_stats(data=None):
    from api.dashboard import build_live_stats
    emit("stats", build_live_stats(), namespace="/dashboard")


@socketio.on("ping", namespace="/dashboard")
def on_ping(data=None):
    emit("pong", {"ts": datetime.now(timezone.utc).isoformat()}, namespace="/dashboard")


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------

def init_app(app: Flask) -> None:
    """Attach SocketIO to the Flask app. Call once during bootstrap."""
    socketio.init_app(app)
    logger.info("SocketIO initialised (async_mode=eventlet, namespace=/dashboard)")