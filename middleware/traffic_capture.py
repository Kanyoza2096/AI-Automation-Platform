"""
Captures HTTP traffic and emits events to Socket.IO for the Data Flow Visualizer.
"""

import time
import logging
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("kanyoza.middleware.traffic")


class TrafficCaptureMiddleware(BaseHTTPMiddleware):
    """Emits traffic_packet events to Socket.IO on every API request."""

    async def dispatch(self, request: Request, call_next):
        start = time.time()
        response = await call_next(request)
        duration_ms = (time.time() - start) * 1000

        if request.url.path.startswith("/api/"):
            try:
                from core.socket_server import emit_to_dashboard

                path = request.url.path
                if "/ai/" in path:
                    to_service = "gemini"
                elif "/posts" in path or "/workflow" in path:
                    to_service = "pipeline"
                elif "/social" in path:
                    to_service = "connectors"
                elif "/supabase" in path:
                    to_service = "supabase"
                elif "/redis" in path:
                    to_service = "redis"
                else:
                    to_service = "connectors"

                await emit_to_dashboard("traffic_packet", {
                    "id": f"traffic_{int(time.time() * 1000)}",
                    "from_service": "frontend",
                    "to_service": to_service,
                    "duration_ms": round(duration_ms),
                    "status": response.status_code,
                    "method": request.method,
                    "path": path,
                })
            except Exception as e:
                logger.debug("Traffic capture emit failed: %s", e)

        return response
