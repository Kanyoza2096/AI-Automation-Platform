"""
Shared Event Bus API Routes v1.0
=================================

Provides REST API for viewing and managing the event-driven system.

ENDPOINTS:
  GET  /api/v1/events/stream      — SSE stream of live events
  GET  /api/v1/events/recent      — Recent events with filtering
  GET  /api/v1/events/stats       — Event statistics and health
  GET  /api/v1/events/topology    — Service topology and event flow
  POST /api/v1/events/publish     — Manually publish an event
  GET  /api/v1/events/subscribers — List active subscribers
  DELETE /api/v1/events/clear     — Clear event history (admin)
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
    
    # Allow disabling auth for development
    if getattr(settings, "disable_event_api_auth", False):
        return True
    
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        raise HTTPException(401, "Missing authentication token")
    
    master_token = settings.master_api_token
    if not master_token:
        logger.warning("Master API token not configured")
        return True
    
    if not hmac.compare_digest(token.encode('utf-8'), master_token.encode('utf-8')):
        raise HTTPException(401, "Unauthorized")
    
    return True


# ═══════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ═══════════════════════════════════════════════════════════════════════════

class PublishEventRequest(BaseModel):
    """Validated model for publishing events."""
    event_name: str = Field(min_length=1, max_length=100, pattern=r'^[a-z0-9_\.]+$')
    payload: Dict[str, Any] = Field(default_factory=dict)
    event_version: str = Field(default="1.0", max_length=20)
    
    @validator('payload')
    def validate_payload_size(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        """Prevent oversized payloads."""
        payload_str = json.dumps(v, default=str)
        if len(payload_str) > 10_000:  # 10KB limit
            raise ValueError('Payload too large (max 10KB)')
        return v


# ═══════════════════════════════════════════════════════════════════════════
# EVENT FILTERING HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _filter_events(
    events: List[Dict[str, Any]],
    event_name: Optional[str] = None,
    service: Optional[str] = None,
    min_severity: Optional[str] = None,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Filter events by criteria."""
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
    
    if start_time:
        filtered = [
            e for e in filtered
            if datetime.fromisoformat(e.get("timestamp", "")) >= start_time
        ]
    
    if end_time:
        filtered = [
            e for e in filtered
            if datetime.fromisoformat(e.get("timestamp", "")) <= end_time
        ]
    
    return filtered


def _infer_severity(event_name: str, payload: Dict[str, Any]) -> str:
    """Infer event severity from event name and payload."""
    # Critical events
    critical_keywords = ["failed", "down", "offline", "critical", "alert"]
    if any(kw in event_name for kw in critical_keywords):
        return "critical"
    
    # Warning events
    warning_keywords = ["degraded", "slow", "warning", "recovery"]
    if any(kw in event_name for kw in warning_keywords):
        return "warning"
    
    # Check payload for status
    status = str(payload.get("status", "")).lower()
    if status in {"failed", "down", "offline", "critical"}:
        return "critical"
    if status in {"degraded", "slow", "warning"}:
        return "warning"
    
    return "info"


# ═══════════════════════════════════════════════════════════════════════════
# IN-MEMORY EVENT HISTORY (for API queries)
# ═══════════════════════════════════════════════════════════════════════════

class EventHistoryBuffer:
    """Bounded in-memory event history for API queries."""
    
    def __init__(self, max_events: int = 1000):
        self.max_events = max_events
        self._events: deque = deque(maxlen=max_events)
        self._subscribers: Dict[str, asyncio.Queue] = {}
        self._lock = asyncio.Lock()
        
    async def add_event(self, event: Dict[str, Any]) -> None:
        """Add event to history and notify subscribers."""
        async with self._lock:
            self._events.append(event)
            
            # Notify SSE subscribers
            for queue in self._subscribers.values():
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    # Drop oldest if queue full
                    try:
                        queue.get_nowait()
                        queue.put_nowait(event)
                    except asyncio.QueueEmpty:
                        pass
    
    async def get_recent(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent events."""
        async with self._lock:
            events = list(self._events)[-limit:]
            return events
    
    async def add_subscriber(self, client_id: str) -> asyncio.Queue:
        """Add SSE subscriber."""
        async with self._lock:
            queue = asyncio.Queue(maxsize=100)
            self._subscribers[client_id] = queue
            return queue
    
    async def remove_subscriber(self, client_id: str) -> None:
        """Remove SSE subscriber."""
        async with self._lock:
            self._subscribers.pop(client_id, None)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get event history statistics."""
        events = list(self._events)
        
        if not events:
            return {
                "total_events": 0,
                "events_per_minute": 0,
                "by_type": {},
                "by_severity": {},
            }
        
        # Count by type
        by_type: Dict[str, int] = {}
        by_severity: Dict[str, int] = {}
        
        for event in events:
            event_name = event.get("event_name", "unknown")
            severity = event.get("severity", "info")
            by_type[event_name] = by_type.get(event_name, 0) + 1
            by_severity[severity] = by_severity.get(severity, 0) + 1
        
        # Calculate events per minute
        if len(events) >= 2:
            first_time = datetime.fromisoformat(events[0].get("timestamp", ""))
            last_time = datetime.fromisoformat(events[-1].get("timestamp", ""))
            time_span_minutes = max(1, (last_time - first_time).total_seconds() / 60)
            events_per_minute = len(events) / time_span_minutes
        else:
            events_per_minute = 0
        
        return {
            "total_events": len(events),
            "events_per_minute": round(events_per_minute, 2),
            "by_type": dict(sorted(by_type.items(), key=lambda x: x[1], reverse=True)[:10]),
            "by_severity": by_severity,
        }
    
    def clear(self) -> None:
        """Clear event history."""
        self._events.clear()


# Global event history buffer
event_history = EventHistoryBuffer(max_events=1000)


# ═══════════════════════════════════════════════════════════════════════════
# EVENT BUS WRAPPER — Intercepts events for API
# ═══════════════════════════════════════════════════════════════════════════

class EventBusAPIWrapper:
    """Wraps the shared event bus to capture events for API."""
    
    def __init__(self, bus):
        self._bus = bus
        self._original_publish = getattr(bus, 'publish', None)
        self._original_publish_event = getattr(bus, 'publish_event', None)
        self._wrapped = False
    
    def wrap(self) -> None:
        """Wrap the bus publish method to capture events."""
        if self._wrapped:
            return
        
        if self._original_publish:
            def wrapped_publish(event_name: str, payload: Dict[str, Any], *args, **kwargs):
                # Call original
                self._original_publish(event_name, payload, *args, **kwargs)
                # Capture for API
                asyncio.create_task(self._capture_event(event_name, payload))
            
            self._bus.publish = wrapped_publish
            self._wrapped = True
            logger.info("EventBusAPI: wrapped publish method")
    
    def unwrap(self) -> None:
        """Restore original publish method."""
        if not self._wrapped:
            return
        
        if self._original_publish:
            self._bus.publish = self._original_publish
        self._wrapped = False
    
    async def _capture_event(self, event_name: str, payload: Dict[str, Any]) -> None:
        """Capture event for API."""
        event = {
            "event_name": event_name,
            "payload": payload,
            "severity": _infer_severity(event_name, payload),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_id": f"evt_{int(time.time() * 1000)}_{event_name}",
        }
        await event_history.add_event(event)


# Global wrapper instance
event_bus_wrapper: Optional[EventBusAPIWrapper] = None


def init_event_api():
    """Initialize event API wrapper."""
    global event_bus_wrapper
    
    bus = _get_event_bus()
    if bus and event_bus_wrapper is None:
        event_bus_wrapper = EventBusAPIWrapper(bus)
        event_bus_wrapper.wrap()
        logger.info("Event API initialized")


# ═══════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/recent")
async def get_recent_events(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500, description="Number of events to return"),
    event_name: Optional[str] = Query(default=None, description="Filter by event name"),
    service: Optional[str] = Query(default=None, description="Filter by service"),
    min_severity: Optional[str] = Query(default=None, description="Filter by minimum severity (info, warning, critical)"),
    _auth=Depends(_require_auth),
):
    """Get recent events with filtering."""
    if min_severity and min_severity not in {"info", "warning", "critical"}:
        raise HTTPException(400, "Invalid severity. Must be info, warning, or critical")
    
    events = await event_history.get_recent(limit=500)
    filtered = _filter_events(
        events,
        event_name=event_name,
        service=service,
        min_severity=min_severity,
    )
    
    return {
        "events": filtered[-limit:],
        "total": len(filtered),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/stream")
async def stream_events(
    request: Request,
    event_name: Optional[str] = Query(default=None, description="Filter by event name"),
):
    """Server-Sent Events stream for real-time event monitoring."""
    
    async def event_generator() -> AsyncGenerator[str, None]:
        client_id = f"client_{int(time.time() * 1000)}"
        queue = await event_history.add_subscriber(client_id)
        
        try:
            # Send initial connection message
            yield f"data: {json.dumps({'type': 'connected', 'timestamp': datetime.now(timezone.utc).isoformat()})}\n\n"
            
            while True:
                # Check if client disconnected
                if await request.is_disconnected():
                    break
                
                try:
                    # Wait for event with timeout
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    
                    # Filter if needed
                    if event_name and event.get("event_name") != event_name:
                        continue
                    
                    yield f"data: {json.dumps(event, default=str)}\n\n"
                    
                except asyncio.TimeoutError:
                    # Send heartbeat
                    yield f"data: {json.dumps({'type': 'heartbeat', 'timestamp': datetime.now(timezone.utc).isoformat()})}\n\n"
                    
        finally:
            await event_history.remove_subscriber(client_id)
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/stats")
async def get_event_stats(
    _auth=Depends(_require_auth),
):
    """Get event statistics and health."""
    stats = event_history.get_stats()
    
    bus = _get_event_bus()
    bus_stats = {}
    if bus:
        try:
            bus_stats = bus.get_stats() if hasattr(bus, 'get_stats') else {}
        except Exception:
            pass
    
    return {
        "event_history": stats,
        "event_bus": bus_stats,
        "subscribers": len(event_history._subscribers),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/topology")
async def get_event_topology(
    _auth=Depends(_require_auth),
):
    """Get service topology and event flow map."""
    events = await event_history.get_recent(limit=500)
    
    # Build topology from events
    services: Dict[str, Dict[str, Any]] = {}
    event_flows: Dict[str, int] = {}
    
    for event in events:
        event_name = event.get("event_name", "unknown")
        service = event_name.split("_")[0] if "_" in event_name else "system"
        
        # Count service events
        if service not in services:
            services[service] = {
                "name": service,
                "event_count": 0,
                "event_types": set(),
                "last_seen": None,
            }
        services[service]["event_count"] += 1
        services[service]["event_types"].add(event_name)
        services[service]["last_seen"] = event.get("timestamp")
        
        # Track event flows
        event_flows[event_name] = event_flows.get(event_name, 0) + 1
    
    # Convert sets to lists
    topology = {
        "services": {
            name: {
                **info,
                "event_types": sorted(info["event_types"]),
            }
            for name, info in services.items()
        },
        "event_flows": dict(sorted(event_flows.items(), key=lambda x: x[1], reverse=True)),
        "total_services": len(services),
        "total_event_types": len(event_flows),
    }
    
    return topology


@router.post("/publish")
async def publish_event(
    data: PublishEventRequest,
    _auth=Depends(_require_auth),
):
    """Manually publish an event to the bus."""
    bus = _get_event_bus()
    if not bus:
        raise HTTPException(503, "Event bus not available")
    
    try:
        # Add version to payload
        payload = {
            **data.payload,
            "event_version": data.event_version,
            "published_by": "api",
        }
        
        bus.publish(data.event_name, payload)
        
        return {
            "ok": True,
            "event_name": data.event_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        logger.error("Failed to publish event: %s", exc)
        raise HTTPException(500, f"Failed to publish event: {str(exc)}")


@router.get("/subscribers")
async def list_subscribers(
    _auth=Depends(_require_auth),
):
    """List active SSE subscribers."""
    return {
        "subscriber_count": len(event_history._subscribers),
        "subscribers": list(event_history._subscribers.keys()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.delete("/clear")
async def clear_events(
    _auth=Depends(_require_auth),
):
    """Clear event history (admin only)."""
    event_history.clear()
    return {
        "ok": True,
        "message": "Event history cleared",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/health")
async def health_check():
    """Health check for event API."""
    bus = _get_event_bus()
    return {
        "status": "healthy" if bus else "degraded",
        "event_bus": "connected" if bus else "unavailable",
        "event_history_size": len(event_history._events),
        "subscriber_count": len(event_history._subscribers),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ═══════════════════════════════════════════════════════════════════════════
# INITIALIZATION
# ═══════════════════════════════════════════════════════════════════════════

def register_event_api_routes(app):
    """Register event API routes and initialize wrapper."""
    app.include_router(router)
    init_event_api()
    logger.info("Event API routes registered")


__all__ = [
    "router",
    "register_event_api_routes",
    "init_event_api",
    "event_history",
    "EventHistoryBuffer",
]
