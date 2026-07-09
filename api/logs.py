"""
api/logs.py — Live log streaming Blueprint.

Endpoints
---------
GET  /api/v1/logs/recent   — paginated recent logs (level/module/search filters)
GET  /api/v1/logs/stream   — SSE real-time log stream
GET  /api/v1/logs/stats    — buffer statistics
DELETE /api/v1/logs/clear  — flush the buffer (auth required)
"""

from __future__ import annotations

import json
import logging
import queue
import time
from functools import wraps

from flask import Blueprint, Response, jsonify, request, stream_with_context

from core.log_buffer import LogBuffer

logger = logging.getLogger("kanyoza.api.logs")

logs_bp = Blueprint("logs", __name__, url_prefix="/api/v1/logs")

# ---------------------------------------------------------------------------
# Auth (mirrors api.v1 auth pattern — keeps this blueprint self-contained)
# ---------------------------------------------------------------------------


def _require_auth() -> bool:
    """
    Validate request credentials.

    Accepted (priority order):
    1. Authorization: Bearer <token>   — standard header auth
    2. X-API-Key: <secret>             — legacy header auth
    3. ?token=<token> query param      — SSE fallback (EventSource cannot set headers)

    The query-param path is intentionally limited: the token is validated with
    constant-time compare, treated as a short-lived credential, and only used
    when the standard header methods are absent.
    """
    import hmac
    from config.settings import settings as _s

    master_token = getattr(_s, "master_api_token", "") or ""
    api_secret   = getattr(_s, "app_secret", "") or ""
    owner_psid   = getattr(_s, "owner_psid", "") or ""

    # 1. Authorization: Bearer header
    auth_header = request.headers.get("Authorization", "").strip()
    if auth_header.startswith("Bearer "):
        bearer = auth_header[len("Bearer "):]
        if master_token and hmac.compare_digest(bearer, master_token):
            return True

    # 2. X-API-Key header
    key = request.headers.get("X-API-Key", "").strip()
    if key:
        if api_secret and hmac.compare_digest(key, api_secret):
            return True
        if owner_psid and hmac.compare_digest(key, owner_psid):
            return True

    # 3. Query-param token — SSE fallback (EventSource cannot set headers)
    q_token = request.args.get("token", "").strip()
    if q_token:
        if master_token and hmac.compare_digest(q_token, master_token):
            return True
        if api_secret and hmac.compare_digest(q_token, api_secret):
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


# ---------------------------------------------------------------------------
# GET /recent
# ---------------------------------------------------------------------------


@logs_bp.route("/recent", methods=["GET"])
@_auth_required
def recent_logs():
    """Return recent log entries with optional filters."""
    try:
        limit  = min(int(request.args.get("limit", 100)), 500)
        level  = request.args.get("level") or None
        module = request.args.get("module") or None
        search = request.args.get("search") or None
        page   = max(int(request.args.get("page", 1)), 1)

        buf     = LogBuffer.get_instance()
        entries = buf.get_recent(limit=limit * page, level=level, module=module, search=search)

        # Simple pagination slice
        start = (page - 1) * limit
        page_entries = entries[start: start + limit]

        return jsonify({
            "ok": True,
            "logs": page_entries,
            "count": len(page_entries),
            "total": len(entries),
            "page": page,
            "limit": limit,
        })
    except Exception as exc:
        logger.exception("GET /api/v1/logs/recent failed")
        return jsonify({"ok": False, "error": str(exc)}), 500


# ---------------------------------------------------------------------------
# GET /stream  (SSE)
# ---------------------------------------------------------------------------


@logs_bp.route("/stream", methods=["GET"])
@_auth_required
def stream_logs():
    """Server-Sent Events endpoint — streams new log entries in real time."""
    buf = LogBuffer.get_instance()
    q: queue.Queue = queue.Queue(maxsize=200)

    def on_entry(entry) -> None:
        try:
            q.put_nowait(entry)
        except queue.Full:
            pass  # drop rather than block

    buf.subscribe(on_entry)

    def generate():
        try:
            # Send a heartbeat immediately so the client knows the stream is live
            yield "event: connected\ndata: {\"ok\": true}\n\n"
            last_heartbeat = time.time()

            while True:
                try:
                    entry = q.get(timeout=5.0)
                    data  = json.dumps(entry.to_dict())
                    yield f"data: {data}\n\n"
                except queue.Empty:
                    pass

                # 30-second heartbeat
                now = time.time()
                if now - last_heartbeat >= 30:
                    yield ": heartbeat\n\n"
                    last_heartbeat = now
        except GeneratorExit:
            pass
        finally:
            buf.unsubscribe(on_entry)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "Connection":       "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# GET /stats
# ---------------------------------------------------------------------------


@logs_bp.route("/stats", methods=["GET"])
@_auth_required
def log_stats():
    """Return buffer statistics."""
    try:
        stats = LogBuffer.get_instance().get_stats()
        return jsonify({"ok": True, **stats})
    except Exception as exc:
        logger.exception("GET /api/v1/logs/stats failed")
        return jsonify({"ok": False, "error": str(exc)}), 500


# ---------------------------------------------------------------------------
# DELETE /clear
# ---------------------------------------------------------------------------


@logs_bp.route("/clear", methods=["DELETE"])
@_auth_required
def clear_logs():
    """Flush the in-memory log buffer."""
    try:
        LogBuffer.get_instance().clear()
        logger.info("Log buffer cleared via API")
        return jsonify({"ok": True, "message": "Log buffer cleared."})
    except Exception as exc:
        logger.exception("DELETE /api/v1/logs/clear failed")
        return jsonify({"ok": False, "error": str(exc)}), 500
