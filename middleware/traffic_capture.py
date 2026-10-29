"""
Traffic Capture Middleware v2.0
================================
Captures HTTP traffic and emits events to Socket.IO for the Data Flow Visualizer.

v2.0 CHANGES:
  - FIXED: Removed unused variable
  - ADDED: Correlation ID tracking
  - ADDED: Status category classification
  - ADDED: Content length tracking
  - ADDED: Rate limiting (prevents Socket.IO flooding)
  - ADDED: Skip paths (health checks excluded)
  - ADDED: Configurable service detection map
"""

import asyncio
import logging
import time
from collections import deque
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("kanyoza.middleware.traffic")


# ═══════════════════════════════════════════════════════════════════════════
# SERVICE DETECTION MAP (v2.0 — configurable)
# ═══════════════════════════════════════════════════════════════════════════

SERVICE_DETECTION_MAP = [
    ("/ai/", "gemini"),
    ("/director/", "director"),
    ("/posts", "pipeline"),
    ("/workflow", "pipeline"),
    ("/content", "pipeline"),
    ("/render", "renderer"),
    ("/social", "connectors"),
    ("/facebook", "connectors"),
    ("/twitter", "connectors"),
    ("/linkedin", "connectors"),
    ("/supabase", "supabase"),
    ("/redis", "redis"),
    ("/events", "event_bus"),
    ("/metrics", "metrics"),
    ("/integrations", "connectors"),
    ("/plugins", "plugins"),
]

# v2.0: Paths that should NOT emit traffic packets
SKIP_PATHS = {
    "/health",
    "/health/live",
    "/health/ready",
    "/health/detailed",
}

# v2.0: Rate limiting — max emits per second
MAX_EMITS_PER_SECOND = 50


def _detect_service(path: str) -> str:
    """Detect target service from path."""
    for pattern, service in SERVICE_DETECTION_MAP:
        if pattern in path:
            return service
    return "connectors"


def _status_category(status_code: int) -> str:
    """Classify HTTP status code."""
    if status_code < 300:
        return "success"
    if status_code < 400:
        return "redirect"
    if status_code < 500:
        return "client_error"
    return "server_error"


class TrafficCaptureMiddleware(BaseHTTPMiddleware):
    """Emits traffic_packet events to Socket.IO on API requests (v2.0)."""

    def __init__(self, app, max_emits_per_second: int = MAX_EMITS_PER_SECOND):
        super().__init__(app)
        self._max_emits = max_emits_per_second
        self._recent_emits: deque = deque(maxlen=max_emits_per_second * 2)
        self._emit_lock = asyncio.Lock()
        self._dropped_count = 0

    def _can_emit(self) -> bool:
        """Rate limit check — max N emits per second."""
        now = time.monotonic()
        while self._recent_emits and now - self._recent_emits[0] >= 1.0:
            self._recent_emits.popleft()
        return len(self._recent_emits) < self._max_emits

    def _record_emit(self) -> None:
        self._recent_emits.append(time.monotonic())

    async def dispatch(self, request: Request, call_next):
        start = time.monotonic()

        # Check if we should skip this path
        if request.url.path in SKIP_PATHS:
            return await call_next(request)

        response = await call_next(request)
        duration_ms = (time.monotonic() - start) * 1000

        # Only capture API requests
        if not request.url.path.startswith("/api/"):
            return response

        # Rate limit
        async with self._emit_lock:
            if not self._can_emit():
                self._dropped_count += 1
                if self._dropped_count == 1 or self._dropped_count % 100 == 0:
                    logger.warning(
                        "TrafficCapture: rate limit hit — dropped %d packets total",
                        self._dropped_count,
                    )
                return response
            self._record_emit()

        try:
            from core.socket_server import emit_to_dashboard

            path = request.url.path
            to_service = _detect_service(path)

            # v2.0: Extract correlation ID
            correlation_id = request.headers.get(
                "X-Correlation-ID",
                request.headers.get("X-Request-ID", ""),
            )

            # v2.0: Content length
            content_length = request.headers.get("content-length", "0")
            try:
                content_length_int = int(content_length)
            except (ValueError, TypeError):
                content_length_int = 0

            status_category = _status_category(response.status_code)

            payload = {
                "id": f"traffic_{int(time.time() * 1000)}",
                "from_service": "frontend",
                "to_service": to_service,
                "duration_ms": round(duration_ms, 1),
                "status": response.status_code,
                "status_category": status_category,  # v2.0: NEW
                "method": request.method,
                "path": path,
                "correlation_id": correlation_id,  # v2.0: NEW
                "content_length": content_length_int,  # v2.0: NEW
                "timestamp": time.time(),
            }

            await emit_to_dashboard("traffic_packet", payload)

        except Exception as e:
            logger.debug("Traffic capture emit failed: %s", e)

        return response


__all__ = ["TrafficCaptureMiddleware", "SERVICE_DETECTION_MAP", "SKIP_PATHS"]
