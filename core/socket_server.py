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

def _auth_dashboard_client() -> bool:
    """
    Validate the connecting dashboard client.

    Accepted via query-string:  ?token=<MASTER_API_TOKEN>

    Behaviour when MASTER_API_TOKEN is not set:
    - development / testing: open (returns True) so the console works without config
    - production: closed (returns False) — no unauthenticated clients allowed
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
        # Development / testing: allow without a token so the console works
        return True

    from flask import request as _req
    token = ""
    try:
        token = _req.args.get("token", "")
    except Exception:
        pass

    if not token:
        return False

    return hmac.compare_digest(token, master_token)


@socketio.on("connect", namespace="/dashboard")
def on_connect():
    if not _auth_dashboard_client():
        logger.warning("Dashboard: rejected unauthenticated SocketIO connection")
        return False   # flask-socketio: returning False rejects the connection

    increment("active_connections")
    logger.info("Dashboard client connected  (total=%d)", _counters["active_connections"])
    # Push current stats snapshot immediately on connect
    from api.dashboard import build_live_stats
    emit("stats", build_live_stats(), namespace="/dashboard")


@socketio.on("disconnect", namespace="/dashboard")
def on_disconnect():
    with _lock:
        _counters["active_connections"] = max(0, _counters["active_connections"] - 1)
    logger.info("Dashboard client disconnected  (total=%d)", _counters["active_connections"])


@socketio.on("request_stats", namespace="/dashboard")
def on_request_stats(data=None):
    """Client can request a fresh stats snapshot at any time."""
    from api.dashboard import build_live_stats
    emit("stats", build_live_stats(), namespace="/dashboard")


@socketio.on("ping", namespace="/dashboard")
def on_ping(data=None):
    emit("pong", {"ts": datetime.now(timezone.utc).isoformat()}, namespace="/dashboard")


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------

def init_app(app: Flask) -> None:
    """Attach SocketIO to the Flask app.  Call once during bootstrap."""
    socketio.init_app(app)
    logger.info("SocketIO initialised (async_mode=eventlet, namespace=/dashboard)")
