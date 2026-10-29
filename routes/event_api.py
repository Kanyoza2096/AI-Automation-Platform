"""
Shared Event Bus API Routes v1.2
=================================

v1.1 FIXES:
  - Eliminated RuntimeWarning: coroutine never awaited (partial — see v1.2)
  - EventBusAPIWrapper now uses thread-safe synchronous capture
  - EventHistoryBuffer supports both async and sync event addition
  - Supabase persistence in background thread (non-blocking)

v1.2 FIXES:
  - /stats, /health, /publish: await _get_event_bus() (was returning a coroutine)
  - /publish: handle async bus.publish result instead of leaving it un-awaited
  - /recent: accept `severity` (exact) alongside `min_severity` (lower bound)
  - /recent: reject unknown query parameters (400) instead of silently ignoring
  - /stats + /subscribers: report sse_clients and registered_consumers separately
  - /stats + /subscribers: surface bus errors instead of silently swallowing them
  - _infer_severity: add "error" (critical) and "retry" (warning) keywords
  - _filter_events: also match on `source` (parity with core implementation)
  - PublishEventRequest.validate_payload_size: add @classmethod (Pydantic v2)
"""

from fastapi import APIRouter, HTTPException, Depends, Request, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from typing import Optional, Dict, Any, List, AsyncGenerator
import asyncio
import hmac
import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta
from core.security.deps import require_species_privileges
from collections import deque

logger = logging.getLogger("kanyoza.events.api")

router = APIRouter(
    prefix="/api/v1/events",
    tags=["events"],
    dependencies=[Depends(require_species_privileges)],
)


# ═══════════════════════════════════════════════════════════════════════════
# EVENT BUS RESOLUTION
# ═══════════════════════════════════════════════════════════════════════════

async def _get_event_bus():
    """Resolve the canonical in-process UnifiedEventBus.

    The FastAPI event API must observe the same bus used by domain services;
    it must not create or monkey-patch a second transport.
    """
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
    """Authenticate through the canonical SpeciesAuth door.

    This endpoint family must never fail open when a master token is absent or
    a development-only auth flag is accidentally enabled in production.
    """
    from core.species_auth import species_auth
    from config.settings import settings

    token = request.headers.get("Authorization", "")
    if token.lower().startswith("bearer "):
        token = token.split(" ", 1)[1].strip()
    else:
        token = request.headers.get("X-API-Key", "").strip()
    if not token:
        raise HTTPException(401, "Authentication required")

    container = getattr(request.app.state, "container", None)
    supabase_client = getattr(getattr(container, "supabase_service", None), "client", None)
    key_store = getattr(request.app.state, "key_store", None)
    identity = species_auth.validate(
        bearer_token=token,
        supabase_client=supabase_client,
        key_store=key_store,
    )
    if not identity.valid:
        raise HTTPException(401, "Unauthorized")
    return identity


# ═══════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ═══════════════════════════════════════════════════════════════════════════

class PublishEventRequest(BaseModel):
    event_name: str = Field(min_length=1, max_length=100, pattern=r'^[a-z0-9_\.]+$')
    payload: Dict[str, Any] = Field(default_factory=dict)
    event_version: str = Field(default="1.0", max_length=20)

    @field_validator('payload')
    @classmethod
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
    if any(kw in event_name for kw in critical_keywords):
        return "critical"
    warning_keywords = ["degraded", "slow", "warning", "recovery", "retry"]
    if any(kw in event_name for kw in warning_keywords):
        return "warning"
    status = str(payload.get("status", "")).lower()
    if status in {"failed", "down", "offline", "critical", "error"}:
        return "critical"
    if status in {"degraded", "slow", "warning", "retry"}:
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
        self._lock = threading.RLock()

    def add_event_sync(self, event: Dict[str, Any]) -> None:
        """✅ SYNCHRONOUS — Safe to call from any context."""
        with self._lock:
            self._events.append(event)
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
            "events_per_minute": round(len(events) / 60, 2),
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
# EVENT BUS OBSERVER (canonical UnifiedEventBus; no monkey-patching)
# ═══════════════════════════════════════════════════════════════════════════

class EventBusAPIWrapper:
    """Observe the canonical UnifiedEventBus for Mission Control history."""

    def __init__(self, bus):
        self._bus = bus
        self._unsubscribe = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._unsubscribe = await self._bus.subscribe_all(self._handle_bus_event)
        self._running = True
        logger.info("EventBusAPI: subscribed to UnifiedEventBus")

    async def stop(self) -> None:
        if self._unsubscribe:
            try:
                self._unsubscribe()
            except Exception:
                logger.debug("EventBusAPI unsubscribe failed", exc_info=True)
        self._unsubscribe = None
        self._running = False

    async def _handle_bus_event(self, event_data: Any) -> None:
        try:
            if not isinstance(event_data, dict):
                return
            event_name = event_data.get("event_name", "unknown")
            payload = event_data.get("payload") or {}
            source = event_data.get("source", "unknown")
            event_id = event_data.get("event_id") or f"evt_{uuid.uuid4().hex[:12]}"
            event_history.add_event_sync({
                "id": event_id,
                "event_name": event_name,
                "payload": payload if isinstance(payload, dict) else {},
                "summary": _build_event_summary(event_name, payload if isinstance(payload, dict) else {}),
                "severity": _infer_severity(event_name, payload if isinstance(payload, dict) else {}),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": source,
            })
        except Exception as exc:
            logger.error("Error capturing bus event: %s", exc, exc_info=True)


event_bus_wrapper: Optional[EventBusAPIWrapper] = None


async def init_event_api():
    """Attach Mission Control event history to the canonical event bus."""
    global event_bus_wrapper
    if event_bus_wrapper and event_bus_wrapper._running:
        return
    bus = await _get_event_bus()
    if not bus:
        logger.warning("Event API: UnifiedEventBus unavailable; event history remains empty")
        return
    event_bus_wrapper = EventBusAPIWrapper(bus)
    await event_bus_wrapper.start()


async def shutdown_event_api():
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
    severity: Optional[str] = Query(default=None),
    _auth=Depends(_require_auth),
):
    # `min_severity` filters as a lower bound (warning → warning + critical).
    # `severity` filters as an exact match (critical → critical only).
    # Unknown query parameters are rejected explicitly rather than dropped.
    allowed_params = {"limit", "event_name", "service", "min_severity", "severity"}
    unknown = set(request.query_params.keys()) - allowed_params
    if unknown:
        raise HTTPException(400, f"Unknown query parameters: {', '.join(sorted(unknown))}")

    valid_severities = {"info", "warning", "critical"}
    if min_severity and min_severity not in valid_severities:
        raise HTTPException(400, "Invalid min_severity")
    if severity and severity not in valid_severities:
        raise HTTPException(400, "Invalid severity")

    events = await event_history.get_recent(limit=500)
    filtered = _filter_events(events, event_name, service, min_severity)
    if severity:
        filtered = [e for e in filtered if e.get("severity") == severity]

    return {
        "events": filtered[-limit:],
        "total": len(filtered),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/stream")
async def stream_events(request: Request, event_name: Optional[str] = None, _auth=Depends(_require_auth)):
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
    """Event bus stats.

    `subscribers` reports two distinct things:
      - sse_clients:          browsers currently streaming /events/stream
      - registered_consumers: engine/domain handlers registered on the bus

    These are not the same number and previously collapsed to the former.
    """
    stats = event_history.get_stats()

    bus = await _get_event_bus()
    bus_stats: Dict[str, Any] = {}
    bus_error: Optional[str] = None
    if bus is None:
        bus_error = "event_bus_not_resolved"
    elif not hasattr(bus, "get_stats"):
        bus_error = "event_bus_missing_get_stats"
    else:
        try:
            bus_stats = bus.get_stats() or {}
        except Exception as exc:
            bus_error = f"{type(exc).__name__}: {exc}"[:300]

    return {
        "event_history": stats,
        "event_bus": bus_stats,
        "event_bus_error": bus_error,
        "subscribers": {
            "sse_clients": event_history.subscriber_count,
            "registered_consumers": bus_stats.get("subscriber_count") if bus_stats else None,
            "bus_event_types_tracked": bus_stats.get("event_types_tracked") if bus_stats else None,
            "bus_wildcard_subscribers": bus_stats.get("wildcard_subscribers") if bus_stats else None,
        },
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
        service = source if source != "unknown" else (event_name.split("_")[0] if "_" in event_name else "system")

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
async def publish_event(data: PublishEventRequest, _auth=Depends(_require_auth), _species=Depends(require_species_privileges)):
    bus = await _get_event_bus()
    if not bus:
        raise HTTPException(503, "Event bus not available")
    try:
        result = bus.publish(data.event_name, {**data.payload, "event_version": data.event_version})
        if asyncio.iscoroutine(result):
            await result
        return {"ok": True, "event_name": data.event_name}
    except Exception as exc:
        raise HTTPException(500, str(exc))


@router.get("/subscribers")
async def list_subscribers(_auth=Depends(_require_auth)):
    """Report subscriber counts from both planes.

    sse_clients: browsers streaming /events/stream.
    registered_consumers: event handlers registered on the bus.
    """
    bus = await _get_event_bus()
    bus_stats: Dict[str, Any] = {}
    bus_error: Optional[str] = None
    if bus is None:
        bus_error = "event_bus_not_resolved"
    elif not hasattr(bus, "get_stats"):
        bus_error = "event_bus_missing_get_stats"
    else:
        try:
            bus_stats = bus.get_stats() or {}
        except Exception as exc:
            bus_error = f"{type(exc).__name__}: {exc}"[:300]

    return {
        "sse_clients": event_history.subscriber_count,
        "registered_consumers": bus_stats.get("subscriber_count") if bus_stats else None,
        "event_types_tracked": bus_stats.get("event_types_tracked") if bus_stats else None,
        "wildcard_subscribers": bus_stats.get("wildcard_subscribers") if bus_stats else None,
        "event_bus_error": bus_error,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.delete("/clear")
async def clear_events(_auth=Depends(_require_auth), _species=Depends(require_species_privileges)):
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
    """Register routes only; async initialization belongs to application lifespan."""
    app.include_router(router)
    logger.info("Event API routes registered (v1.2); initialization deferred to lifespan")


__all__ = [
    "router",
    "register_event_api_routes",
    "init_event_api",
    "event_history",
    "EventHistoryBuffer",
]
