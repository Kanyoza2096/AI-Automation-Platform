"""
Kanyoza Event Bus API Routes v2.0
=================================

FIXES:
  - Fixed import: core.unified_bus (not core.unified_event_bus)
  - Made _get_event_bus async
  - Added auth to /stream endpoint
  - Made init_event_api async
  - EventBusAPIWrapper subscribes to bus (no monkey-patching)
"""

from fastapi import APIRouter, HTTPException, Depends, Request, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, validator
from typing import Optional, Dict, Any, List, AsyncGenerator
import asyncio
import hmac
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from collections import deque

logger = logging.getLogger("kanyoza.events.api")

router = APIRouter(prefix="/api/v1/events", tags=["events"])


# ═══════════════════════════════════════════════════════════════════════════
# EVENT BUS RESOLUTION (FIXED — async, correct import)
# ═══════════════════════════════════════════════════════════════════════════

async def _get_event_bus():
    """Get the unified event bus instance."""
    try:
        from core.unified_bus import get_unified_bus
        return await get_unified_bus()
    except Exception as exc:
        logger.error("Failed to get unified event bus: %s", exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# AUTHENTICATION
# ═══════════════════════════════════════════════════════════════════════════

def _require_auth(request: Request):
    """Authenticate API requests."""
    from config.settings import settings
    
    if getattr(settings, "disable_event_api_auth", False):
        return True
    
    token = (
        request.headers.get("Authorization", "").replace("Bearer ", "").strip()
        or request.headers.get("X-API-Key", "").strip()
    )
    
    if not token:
        raise HTTPException(401, "Missing authentication token")
    
    master_token = settings.master_api_token
    if not master_token:
        # Phase 0: fail closed — missing master token is not "open access"
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="MASTER_API_TOKEN not configured")
    
    if not hmac.compare_digest(token.encode('utf-8'), master_token.encode('utf-8')):
        raise HTTPException(401, "Unauthorized")
    
    return True


# ═══════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ═══════════════════════════════════════════════════════════════════════════

class PublishEventRequest(BaseModel):
    event_name: str = Field(min_length=1, max_length=100, pattern=r'^[a-z0-9_\.]+$')
    payload: Dict[str, Any] = Field(default_factory=dict)
    event_version: str = Field(default="1.0", max_length=20)
    source: Optional[str] = Field(default=None, max_length=50)
    
    @validator('payload')
    def validate_payload_size(cls, v):
        payload_str = json.dumps(v, default=str)
        if len(payload_str) > 10_000:
            raise ValueError('Payload too large (max 10KB)')
        return v


# ═══════════════════════════════════════════════════════════════════════════
# EVENT FILTERING
# ═══════════════════════════════════════════════════════════════════════════

def _filter_events(events, event_name=None, service=None, min_severity=None):
    filtered = events
    if event_name:
        filtered = [e for e in filtered if e.get("event_name") == event_name]
    if service:
        filtered = [
            e for e in filtered
            if e.get("service") == service or e.get("source") == service
        ]
    if min_severity:
        severity_order = {"info": 0, "warning": 1, "critical": 2}
        min_level = severity_order.get(min_severity, 0)
        filtered = [
            e for e in filtered
            if severity_order.get(e.get("severity", "info"), 0) >= min_level
        ]
    return filtered


def _build_event_summary(event_name: str, payload: Dict[str, Any]) -> str:
    """Build a deterministic, non-invented summary for operator surfaces.

    Prefer producer-supplied human text. Otherwise describe only the observed
    event name/status; never infer business meaning from missing payload data.
    """
    for key in ("summary", "message", "description"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:500]
    status = payload.get("status")
    if isinstance(status, str) and status.strip():
        return f"{event_name} reported status={status.strip()}."[:500]
    return f"{event_name} event observed; no producer summary supplied."[:500]

def _infer_severity(event_name: str, payload: Dict[str, Any]) -> str:
    critical_keywords = ["failed", "down", "offline", "critical", "alert", "error"]
    if any(kw in event_name.lower() for kw in critical_keywords):
        return "critical"
    warning_keywords = ["degraded", "slow", "warning", "recovery", "retry"]
    if any(kw in event_name.lower() for kw in warning_keywords):
        return "warning"
    status = str(payload.get("status", "")).lower()
    if status in {"failed", "down", "offline", "critical", "error"}:
        return "critical"
    if status in {"degraded", "slow", "warning", "retry"}:
        return "warning"
    return "info"


# ═══════════════════════════════════════════════════════════════════════════
# EVENT HISTORY BUFFER
# ═══════════════════════════════════════════════════════════════════════════

class EventHistoryBuffer:
    """Bounded in-memory event history."""
    
    def __init__(self, max_events: int = 1000):
        self.max_events = max_events
        self._events: deque = deque(maxlen=max_events)
        self._subscribers: Dict[str, asyncio.Queue] = {}
        self._lock = asyncio.Lock()
    
    @property
    def size(self) -> int:
        return len(self._events)
    
    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
    
    async def add_event(self, event: Dict[str, Any]) -> None:
        async with self._lock:
            self._events.append(event)
            dead_subscribers = []
            for client_id, queue in self._subscribers.items():
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    try:
                        queue.get_nowait()
                        queue.put_nowait(event)
                    except asyncio.QueueEmpty:
                        dead_subscribers.append(client_id)
                except Exception:
                    dead_subscribers.append(client_id)
            for client_id in dead_subscribers:
                self._subscribers.pop(client_id, None)
    
    async def get_recent(self, limit: int = 100) -> List[Dict[str, Any]]:
        async with self._lock:
            return list(self._events)[-limit:]
    
    async def add_subscriber(self, client_id: str) -> asyncio.Queue:
        async with self._lock:
            queue = asyncio.Queue(maxsize=100)
            self._subscribers[client_id] = queue
            return queue
    
    async def remove_subscriber(self, client_id: str) -> None:
        async with self._lock:
            self._subscribers.pop(client_id, None)
    
    def get_stats(self) -> Dict[str, Any]:
        events = list(self._events)
        if not events:
            return {"total_events": 0, "events_per_minute": 0, "by_type": {}, "by_severity": {}}
        by_type: Dict[str, int] = {}
        by_severity: Dict[str, int] = {}
        for event in events:
            event_name = event.get("event_name", "unknown")
            severity = event.get("severity", "info")
            by_type[event_name] = by_type.get(event_name, 0) + 1
            by_severity[severity] = by_severity.get(severity, 0) + 1
        return {
            "total_events": len(events),
            "events_per_minute": round(len(events) / 60, 2),
            "by_type": dict(sorted(by_type.items(), key=lambda x: x[1], reverse=True)[:10]),
            "by_severity": by_severity,
        }
    
    def clear(self) -> None:
        self._events.clear()


event_history = EventHistoryBuffer(max_events=1000)


# ═══════════════════════════════════════════════════════════════════════════
# EVENT BUS WRAPPER (v2.0 — subscribe-based, no monkey-patching)
# ═══════════════════════════════════════════════════════════════════════════

class EventBusAPIWrapper:
    """Subscribes to UnifiedEventBus for API capture."""
    
    def __init__(self, bus):
        self._bus = bus
        self._unsubscribe = None
        self._running = False
    
    async def start(self) -> None:
        if self._running:
            return
        try:
            self._unsubscribe = await self._bus.subscribe_all(self._handle_bus_event)
            self._running = True
            logger.info("EventBusAPI: subscribed to UnifiedEventBus")
        except Exception as exc:
            logger.error("Failed to subscribe: %s", exc)
    
    async def stop(self) -> None:
        if self._unsubscribe and self._running:
            self._unsubscribe()
            self._unsubscribe = None
            self._running = False
    
    async def _handle_bus_event(self, event_data: Any) -> None:
        try:
            if isinstance(event_data, dict):
                event_name = event_data.get("event_name", "unknown")
                payload = event_data.get("payload", {})
                source = event_data.get("source", "unknown")
                event_id = event_data.get("event_id", f"evt_{uuid.uuid4().hex[:12]}")
            else:
                event_name = "unknown"
                payload = {}
                source = "unknown"
                event_id = f"evt_{uuid.uuid4().hex[:12]}"
            
            event = {
                "id": event_id,
                "event_name": event_name,
                "payload": payload if isinstance(payload, dict) else {},
                "summary": _build_event_summary(event_name, payload if isinstance(payload, dict) else {}),
                "severity": _infer_severity(event_name, payload if isinstance(payload, dict) else {}),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": source,
            }
            await event_history.add_event(event)
        except Exception as exc:
            logger.error("Error handling bus event: %s", exc)


event_bus_wrapper: Optional[EventBusAPIWrapper] = None


async def init_event_api():
    """Initialize event API wrapper."""
    global event_bus_wrapper
    try:
        bus = await _get_event_bus()
        if bus and event_bus_wrapper is None:
            event_bus_wrapper = EventBusAPIWrapper(bus)
            await event_bus_wrapper.start()
            logger.info("Event API initialized (v2.0)")
    except Exception as exc:
        logger.error("Failed to initialize event API: %s", exc)


async def shutdown_event_api():
    """Shutdown event API wrapper."""
    global event_bus_wrapper
    if event_bus_wrapper:
        await event_bus_wrapper.stop()
        event_bus_wrapper = None


# ═══════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/recent")
async def get_recent_events(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    event_name: Optional[str] = Query(default=None),
    service: Optional[str] = Query(default=None),
    min_severity: Optional[str] = Query(default=None),
    _auth=Depends(_require_auth),
):
    if min_severity and min_severity not in {"info", "warning", "critical"}:
        raise HTTPException(400, "Invalid severity")
    events = await event_history.get_recent(limit=500)
    filtered = _filter_events(events, event_name, service, min_severity)
    return {
        "events": filtered[-limit:],
        "total": len(filtered),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/stream")
async def stream_events(
    request: Request,
    event_name: Optional[str] = Query(default=None),
    _auth=Depends(_require_auth),
):
    """SSE stream with auth."""
    async def event_generator():
        client_id = f"client_{uuid.uuid4().hex[:8]}"
        queue = await event_history.add_subscriber(client_id)
        try:
            yield f"data: {json.dumps({'type': 'connected'})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    if event_name and event.get("event_name") != event_name:
                        continue
                    yield f"data: {json.dumps(event, default=str)}\n\n"
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
        finally:
            await event_history.remove_subscriber(client_id)
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.get("/stats")
async def get_event_stats(_auth=Depends(_require_auth)):
    stats = event_history.get_stats()
    bus = await _get_event_bus()
    bus_stats = {}
    if bus and hasattr(bus, 'get_stats'):
        try:
            bus_stats = bus.get_stats()
        except Exception:
            pass
    return {
        "event_history": stats,
        "event_bus": bus_stats,
        "subscribers": event_history.subscriber_count,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/topology")
async def get_event_topology(_auth=Depends(_require_auth)):
    events = await event_history.get_recent(limit=500)
    services = {}
    event_flows = {}
    for event in events:
        event_name = event.get("event_name", "unknown")
        source = event.get("source", "unknown")
        service = source if source != "unknown" else event_name.split("_")[0] if "_" in event_name else "system"
        if service not in services:
            services[service] = {"name": service, "event_count": 0, "event_types": set()}
        services[service]["event_count"] += 1
        services[service]["event_types"].add(event_name)
        event_flows[event_name] = event_flows.get(event_name, 0) + 1
    return {
        "services": {name: {**info, "event_types": sorted(info["event_types"])} for name, info in services.items()},
        "event_flows": dict(sorted(event_flows.items(), key=lambda x: x[1], reverse=True)),
        "total_services": len(services),
        "total_event_types": len(event_flows),
    }


@router.post("/publish")
async def publish_event(data: PublishEventRequest, _auth=Depends(_require_auth)):
    bus = await _get_event_bus()
    if not bus:
        raise HTTPException(503, "Event bus not available")
    try:
        result = bus.publish(data.event_name, {**data.payload, "event_version": data.event_version}, source=data.source or "api")
        if asyncio.iscoroutine(result):
            await result
        return {"ok": True, "event_name": data.event_name}
    except Exception as exc:
        raise HTTPException(500, str(exc))


@router.get("/subscribers")
async def list_subscribers(_auth=Depends(_require_auth)):
    return {
        "subscriber_count": event_history.subscriber_count,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.delete("/clear")
async def clear_events(_auth=Depends(_require_auth)):
    event_history.clear()
    return {"ok": True, "message": "Event history cleared"}


@router.get("/health")
async def health_check():
    bus = await _get_event_bus()
    return {
        "status": "healthy" if bus else "degraded",
        "event_bus": "connected" if bus else "unavailable",
        "event_history_size": event_history.size,
        "subscriber_count": event_history.subscriber_count,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def register_event_api_routes(app):
    app.include_router(router)
    
    @app.on_event("startup")
    async def _startup():
        await init_event_api()
    
    @app.on_event("shutdown")
    async def _shutdown():
        await shutdown_event_api()
    
    logger.info("Event API routes registered (v2.0)")


__all__ = [
    "router",
    "register_event_api_routes",
    "init_event_api",
    "shutdown_event_api",
    "event_history",
    "EventHistoryBuffer",
]
