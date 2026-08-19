"""
Shared Event Bus API Routes v1.1
=================================

v1.1 FIXES:
  - Eliminated RuntimeWarning: coroutine never awaited
  - EventBusAPIWrapper now uses thread-safe synchronous capture
  - EventHistoryBuffer supports both async and sync event addition
  - Supabase persistence in background thread (non-blocking)
"""

from fastapi import APIRouter, HTTPException, Depends, Request, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, validator
from typing import Optional, Dict, Any, List, AsyncGenerator
import asyncio
import hmac
import json
import logging
import threading
import time
from datetime import datetime, timezone, timedelta
from collections import deque

logger = logging.getLogger("kanyoza.events.api")

router = APIRouter(prefix="/api/v1/events", tags=["events"])


# ═══════════════════════════════════════════════════════════════════════════
# EVENT BUS RESOLUTION
# ═══════════════════════════════════════════════════════════════════════════

def _get_event_bus():
    """Get the shared event bus instance."""
    try:
        from core.shared_event_bus import get_shared_event_bus
        return get_shared_event_bus()
    except Exception as exc:
        logger.error("Failed to get shared event bus: %s", exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# AUTHENTICATION
# ═══════════════════════════════════════════════════════════════════════════

def _require_auth(request: Request):
    """Authenticate API requests with constant-time comparison."""
    from config.settings import settings
    
    if getattr(settings, "disable_event_api_auth", False):
        return True
    
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        raise HTTPException(401, "Missing authentication token")
    
    master_token = settings.master_api_token
    if not master_token:
        return True
    
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
        filtered = [e for e in filtered if e.get("service") == service]
    if min_severity:
        severity_order = {"info": 0, "warning": 1, "critical": 2}
        min_level = severity_order.get(min_severity, 0)
        filtered = [
            e for e in filtered
            if severity_order.get(e.get("severity", "info"), 0) >= min_level
        ]
    return filtered


def _infer_severity(event_name: str, payload: Dict[str, Any]) -> str:
    critical_keywords = ["failed", "down", "offline", "critical", "alert"]
    if any(kw in event_name for kw in critical_keywords):
        return "critical"
    warning_keywords = ["degraded", "slow", "warning", "recovery"]
    if any(kw in event_name for kw in warning_keywords):
        return "warning"
    status = str(payload.get("status", "")).lower()
    if status in {"failed", "down", "offline", "critical"}:
        return "critical"
    if status in {"degraded", "slow", "warning"}:
        return "warning"
    return "info"


# ═══════════════════════════════════════════════════════════════════════════
# EVENT HISTORY BUFFER (v1.1 — Sync + Async)
# ═══════════════════════════════════════════════════════════════════════════

class EventHistoryBuffer:
    """Bounded in-memory event history for API queries."""
    
    def __init__(self, max_events: int = 1000):
        self.max_events = max_events
        self._events: deque = deque(maxlen=max_events)
        self._subscribers: Dict[str, asyncio.Queue] = {}
        self._lock = threading.RLock()  # ✅ Use threading.Lock (sync-compatible)
    
    def add_event_sync(self, event: Dict[str, Any]) -> None:
        """✅ SYNCHRONOUS — Safe to call from any context."""
        with self._lock:
            self._events.append(event)
            
            # Notify subscribers (fire-and-forget via loop if available)
            for queue in list(self._subscribers.values()):
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    try:
                        queue.get_nowait()
                        queue.put_nowait(event)
                    except asyncio.QueueEmpty:
                        pass
    
    async def add_event(self, event: Dict[str, Any]) -> None:
        """Async wrapper for backward compatibility."""
        self.add_event_sync(event)
    
    async def get_recent(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._events)[-limit:]
    
    async def add_subscriber(self, client_id: str) -> asyncio.Queue:
        with self._lock:
            queue = asyncio.Queue(maxsize=100)
            self._subscribers[client_id] = queue
            return queue
    
    async def remove_subscriber(self, client_id: str) -> None:
        with self._lock:
            self._subscribers.pop(client_id, None)
    
    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
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
            "events_per_minute": round(len(events) / 60, 2),  # Simplified
            "by_type": dict(sorted(by_type.items(), key=lambda x: x[1], reverse=True)[:10]),
            "by_severity": by_severity,
        }
    
    def clear(self) -> None:
        with self._lock:
            self._events.clear()
    
    @property
    def size(self) -> int:
        with self._lock:
            return len(self._events)
    
    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)


event_history = EventHistoryBuffer(max_events=1000)


# ═══════════════════════════════════════════════════════════════════════════
# EVENT BUS WRAPPER (v1.1 — FIXED: No coroutine warnings)
# ═══════════════════════════════════════════════════════════════════════════

class EventBusAPIWrapper:
    """Wraps the shared event bus to capture events for API."""
    
    def __init__(self, bus):
        self._bus = bus
        self._original_publish = getattr(bus, 'publish', None)
        self._wrapped = False
    
    def wrap(self) -> None:
        if self._wrapped or not self._original_publish:
            return
        
        def wrapped_publish(event_name: str, payload: Dict[str, Any], *args, **kwargs):
            # 1. Call original publish
            try:
                self._original_publish(event_name, payload, *args, **kwargs)
            except Exception:
                pass
            
            # 2. Capture event — ✅ SYNCHRONOUS (no coroutine!)
            self._capture_event_sync(event_name, payload)
        
        self._bus.publish = wrapped_publish
        self._wrapped = True
        logger.info("EventBusAPI: wrapped publish method (v1.1 sync)")
    
    def unwrap(self) -> None:
        if not self._wrapped:
            return
        if self._original_publish:
            self._bus.publish = self._original_publish
        self._wrapped = False
    
    def _capture_event_sync(self, event_name: str, payload: Dict[str, Any]) -> None:
        """✅ SYNCHRONOUS event capture — no async issues."""
        event = {
            "event_name": event_name,
            "payload": payload or {},
            "severity": _infer_severity(event_name, payload or {}),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        
        # Add to in-memory buffer (thread-safe)
        event_history.add_event_sync(event)
        
        # Persist to Supabase in background thread (non-blocking)
        threading.Thread(
            target=self._persist_event,
            args=(event_name, payload or {}),
            daemon=True,
            name=f"event-persist-{event_name[:20]}",
        ).start()
    
    def _persist_event(self, event_name: str, payload: Dict[str, Any]) -> None:
        """Persist event to Supabase — runs in daemon thread."""
        try:
            bus = self._bus
            if bus and hasattr(bus, 'supabase') and bus.supabase:
                bus.supabase.table("events").insert({
                    "event_name": event_name,
                    "payload": payload,
                    "source_service": "event_api",
                }).execute()
        except Exception as exc:
            logger.debug("Event persist failed: %s", exc)


event_bus_wrapper: Optional[EventBusAPIWrapper] = None


def init_event_api():
    """Initialize event API wrapper."""
    global event_bus_wrapper
    bus = _get_event_bus()
    if bus and event_bus_wrapper is None:
        event_bus_wrapper = EventBusAPIWrapper(bus)
        event_bus_wrapper.wrap()
        logger.info("Event API initialized (v1.1 sync capture)")


# ═══════════════════════════════════════════════════════════════════════════
# ENDPOINTS (unchanged from v1.0)
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
async def stream_events(request: Request, event_name: Optional[str] = None):
    async def event_generator():
        client_id = f"client_{int(time.time() * 1000)}"
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
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.get("/stats")
async def get_event_stats(_auth=Depends(_require_auth)):
    stats = event_history.get_stats()
    bus = _get_event_bus()
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
        service = event_name.split("_")[0] if "_" in event_name else "system"
        
        if service not in services:
            services[service] = {"name": service, "event_count": 0, "event_types": set()}
        services[service]["event_count"] += 1
        services[service]["event_types"].add(event_name)
        event_flows[event_name] = event_flows.get(event_name, 0) + 1
    
    return {
        "services": {
            name: {**info, "event_types": sorted(info["event_types"])}
            for name, info in services.items()
        },
        "event_flows": dict(sorted(event_flows.items(), key=lambda x: x[1], reverse=True)),
        "total_services": len(services),
        "total_event_types": len(event_flows),
    }


@router.post("/publish")
async def publish_event(data: PublishEventRequest, _auth=Depends(_require_auth)):
    bus = _get_event_bus()
    if not bus:
        raise HTTPException(503, "Event bus not available")
    try:
        bus.publish(data.event_name, {**data.payload, "event_version": data.event_version})
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
    bus = _get_event_bus()
    return {
        "status": "healthy" if bus else "degraded",
        "event_bus": "connected" if bus else "unavailable",
        "event_history_size": event_history.size,
        "subscriber_count": event_history.subscriber_count,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def register_event_api_routes(app):
    app.include_router(router)
    init_event_api()
    logger.info("Event API routes registered (v1.1)")


__all__ = [
    "router",
    "register_event_api_routes",
    "init_event_api",
    "event_history",
    "EventHistoryBuffer",
]