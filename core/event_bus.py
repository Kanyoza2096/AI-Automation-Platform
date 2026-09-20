"""
KANYOZA EVENT BUS v4.1 — LEGACY (Phase B)

Prefer ``core.unified_bus.UnifiedEventBus`` for all new code.
This module remains as a DomainEvent-compatible bridge; lifecycle
registers an EventBusBridge that forwards to UnifiedEventBus.

Canonical nerve events and workspace_id live on UnifiedEventBus only.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional

# ✅ FIXED: OpenTelemetry is now OPTIONAL
try:
    from opentelemetry import trace
    tracer = trace.get_tracer("kanyoza.eventbus", "4.1.0")
    HAS_TRACING = True
except ImportError:
    tracer = None
    HAS_TRACING = False

from core.events import DomainEvent, EventType, EventMetadata

logger = logging.getLogger("kanyoza.eventbus")

Subscriber = Callable[[DomainEvent], None]
AsyncSubscriber = Callable[[DomainEvent], Awaitable[None]]


@dataclass
class DeadLetter:
    event_id: str
    event_type: str
    payload: dict
    error: str
    handler_name: str
    attempt_count: int = 1
    first_failure: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_failure: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class EventStoreEntry:
    event_id: str
    event_type: str
    payload: dict
    correlation_id: Optional[str]
    sequence_number: int
    published_at: datetime
    source_engine: str
    trace_id: Optional[str] = None


class ThrottlePolicy(Enum):
    DROP = "drop"
    DEFER = "defer"
    DEBOUNCE = "debounce"
    BLOCK = "block"


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class PersistenceCircuitBreaker:
    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 30.0):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.state = CircuitState.CLOSED
        self._failures = 0
        self._last_failure_time: Optional[float] = None
        self._lock = asyncio.Lock()

    @property
    def is_open(self) -> bool:
        return self.state == CircuitState.OPEN

    async def record_success(self) -> None:
        async with self._lock:
            self._failures = 0
            if self.state == CircuitState.HALF_OPEN:
                self.state = CircuitState.CLOSED
                logger.info("Persistence circuit breaker closed")

    async def record_failure(self) -> None:
        async with self._lock:
            self._failures += 1
            self._last_failure_time = time.monotonic()
            if self._failures >= self.failure_threshold:
                self.state = CircuitState.OPEN
                logger.warning("Persistence circuit breaker opened after %d failures", self._failures)

    async def should_attempt(self) -> bool:
        async with self._lock:
            if self.state == CircuitState.CLOSED:
                return True
            if self.state == CircuitState.OPEN:
                if self._last_failure_time and (time.monotonic() - self._last_failure_time >= self.recovery_timeout):
                    self.state = CircuitState.HALF_OPEN
                    return True
                return False
            return True


class EventBus:
    """Async-native event bus with sync compatibility."""

    MAX_QUEUE_SIZE = 10000
    MAX_EVENTS_PER_SECOND = 500
    THROTTLE_WINDOW_SECONDS = 1.0
    MAX_DEAD_LETTERS = 1000
    MAX_EVENT_STORE = 10000
    MAX_PAYLOAD_SIZE = 1_048_576
    CORRELATION_TTL_SECONDS = 3600
    PERSISTENCE_BATCH_SIZE = 10
    PERSISTENCE_FLUSH_INTERVAL = 1.0

    def __init__(self, supabase_client=None, persist_events: bool = False, enable_tracing: bool = True):
        self._subscribers: dict[EventType, list] = defaultdict(list)
        self._wildcard: list = []
        self._lock = asyncio.Lock()

        self._dispatch_queue: asyncio.Queue = asyncio.Queue(maxsize=self.MAX_QUEUE_SIZE)
        self._dispatch_task: Optional[asyncio.Task] = None
        self._running = False

        self._supabase = supabase_client
        self._persist_events = persist_events
        self._persistence_breaker = PersistenceCircuitBreaker()
        self._persistence_queue: asyncio.Queue = asyncio.Queue(maxsize=self.MAX_QUEUE_SIZE)
        self._persistence_task: Optional[asyncio.Task] = None
        self._persistence_batch: List[EventStoreEntry] = []

        self._event_store: deque = deque(maxlen=self.MAX_EVENT_STORE)
        self._sequence_number = 0
        self._dead_letters: deque = deque(maxlen=self.MAX_DEAD_LETTERS)

        self._recent_events: deque = deque()
        self._throttle_policy = ThrottlePolicy.DEFER
        self._throttled_count = 0

        self._active_correlations: Dict[str, Dict[str, Any]] = {}
        self._correlation_cleanup_task: Optional[asyncio.Task] = None

        self._event_count = 0
        self._error_count = 0
        self._persistence_failures = 0
        self._enable_tracing = enable_tracing and HAS_TRACING

        logger.info("EventBus v4.1 initialized — persist=%s tracing=%s", persist_events, self._enable_tracing)

    # ═══════════════════════════════════════════════════════════════
    # LIFECYCLE
    # ═══════════════════════════════════════════════════════════════

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._dispatch_task = asyncio.create_task(self._dispatch_loop(), name="eventbus-dispatch")
        if self._persist_events:
            self._persistence_task = asyncio.create_task(self._persistence_loop(), name="eventbus-persistence")
        self._correlation_cleanup_task = asyncio.create_task(self._correlation_cleanup_loop(), name="eventbus-correlation-cleanup")
        logger.info("EventBus v4.1 started")

    async def stop(self, timeout: float = 5.0) -> None:
        if not self._running:
            return
        logger.info("EventBus: shutting down...")
        self._running = False

        if self._persistence_queue.qsize() > 0:
            await self._flush_persistence()

        for task in [self._dispatch_task, self._persistence_task, self._correlation_cleanup_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        logger.info("EventBus: shutdown complete")

    # ═══════════════════════════════════════════════════════════════
    # SUBSCRIPTION
    # ═══════════════════════════════════════════════════════════════

    def subscribe(self, event_type, handler) -> Callable[[], None]:
        """Subscribe to event type. Returns unsubscribe function."""
        if isinstance(event_type, str):
            try:
                event_type = EventType(event_type)
            except ValueError:
                pass

        self._subscribers[event_type].append(handler)
        logger.debug("EventBus: subscribed %s", self._event_type_name(event_type))

        def unsubscribe() -> None:
            self._subscribers[event_type] = [
                h for h in self._subscribers[event_type] if h is not handler
            ]

        return unsubscribe

    def subscribe_all(self, handler) -> None:
        self._wildcard.append(handler)
        logger.info("EventBus: wildcard subscriber added")

    def unsubscribe(self, event_type, handler) -> None:
        self._subscribers[event_type] = [
            h for h in self._subscribers[event_type] if h is not handler
        ]

    # ═══════════════════════════════════════════════════════════════
    # PUBLISHING
    # ═══════════════════════════════════════════════════════════════

    def enable_async(self, enabled: bool = True) -> None:
        logger.debug("EventBus: async mode always enabled in v4.1")

    def enable_persistence(self, supabase_client=None) -> None:
        self._persist_events = True
        if supabase_client:
            self._supabase = supabase_client
        logger.info("EventBus: persistence enabled")

    async def publish(self, event=None, payload=None, source: str = "unknown", correlation_id: Optional[str] = None, parent_event_id: Optional[str] = None, **kwargs) -> Optional[DomainEvent]:
        """Publish an event (async)."""
        domain_event = self._normalize_event(event, payload, source, **kwargs)
        if domain_event is None:
            logger.error("EventBus.publish() called with invalid signature")
            return None

        if not self._validate_event(domain_event):
            return None

        if not await self._check_throttle():
            return None

        metadata = EventMetadata(
            sequence_number=self._next_sequence(),
            source_engine=getattr(domain_event, "source", source or "unknown"),
            correlation_id=correlation_id or getattr(domain_event, "correlation_id", None),
            parent_event_id=parent_event_id or getattr(domain_event, "parent_event_id", None),
        )

        if self._enable_tracing and HAS_TRACING:
            current_span = trace.get_current_span()
            if current_span and current_span.is_recording():
                metadata.trace_id = current_span.get_span_context().trace_id
                metadata.span_id = current_span.get_span_context().span_id

        domain_event.metadata = metadata

        if metadata.correlation_id:
            self._track_correlation(metadata.correlation_id, metadata.event_id)

        self._store_event(domain_event, metadata)
        await self._enqueue_dispatch(domain_event)
        self._event_count += 1

        return domain_event

    async def publish_event(self, event_name: str, payload: Optional[dict] = None, correlation_id: Optional[str] = None, parent_event_id: Optional[str] = None, source: str = "unknown") -> Optional[DomainEvent]:
        """Convenience method — publish string-named event."""
        return await self.publish(
            event=event_name,
            payload=payload or {},
            source=source,
            correlation_id=correlation_id,
            parent_event_id=parent_event_id,
        )

    def publish_sync(self, event=None, payload=None, source: str = "unknown", **kwargs) -> Optional[DomainEvent]:
        """Synchronous wrapper for publish."""
        try:
            loop = asyncio.get_running_loop()
            asyncio.create_task(self.publish(event, payload, source, **kwargs))
            return None
        except RuntimeError:
            return asyncio.run(self.publish(event, payload, source, **kwargs))

    def _normalize_event(self, event: Any, payload: Any, source: str, **kwargs) -> Optional[DomainEvent]:
        if isinstance(event, DomainEvent):
            return event
        if isinstance(event, str):
            return DomainEvent(
                event_type=event,
                payload=payload if isinstance(payload, dict) else {"data": payload},
                source=source or kwargs.get("source", "unknown"),
            )
        if event is None and isinstance(payload, DomainEvent):
            return payload
        return None

    def _validate_event(self, event: DomainEvent) -> bool:
        import json
        try:
            payload_size = len(json.dumps(event.payload))
            if payload_size > self.MAX_PAYLOAD_SIZE:
                logger.error("Event payload too large: %d bytes", payload_size)
                return False
        except (TypeError, ValueError):
            logger.error("Event payload not JSON serializable")
            return False
        return True

    # ═══════════════════════════════════════════════════════════════
    # DISPATCH
    # ═══════════════════════════════════════════════════════════════

    async def _enqueue_dispatch(self, event: DomainEvent) -> None:
        try:
            self._dispatch_queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.error("Dispatch queue full — event dropped")
            self._throttled_count += 1

    async def _dispatch_loop(self) -> None:
        while self._running:
            try:
                event = await self._dispatch_queue.get()
                await self._dispatch_event(event)
                self._dispatch_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Dispatch loop error: %s", exc, exc_info=True)

    async def _dispatch_event(self, event: DomainEvent) -> None:
        if HAS_TRACING and tracer:
            async with tracer.start_as_current_span("eventbus.dispatch"):
                await self._dispatch_event_inner(event)
        else:
            await self._dispatch_event_inner(event)

    async def _dispatch_event_inner(self, event: DomainEvent) -> None:
        event_type = event.event_type
        handlers = list(self._subscribers.get(event_type, []))
        wildcards = list(self._wildcard)

        for handler in handlers + wildcards:
            await self._invoke_handler(handler, event)

    async def _invoke_handler(self, handler, event: DomainEvent) -> None:
        try:
            if asyncio.iscoroutinefunction(handler):
                await handler(event)
            else:
                handler(event)
        except Exception as exc:
            self._error_count += 1
            logger.error("Handler failed: %s", exc, exc_info=True)
            self._capture_dead_letter(event, getattr(handler, "__qualname__", str(handler)), exc)

    # ═══════════════════════════════════════════════════════════════
    # PERSISTENCE
    # ═══════════════════════════════════════════════════════════════

    def _store_event(self, event: DomainEvent, metadata: EventMetadata) -> None:
        entry = EventStoreEntry(
            event_id=metadata.event_id,
            event_type=self._event_type_name(event.event_type),
            payload=event.payload if isinstance(event.payload, dict) else {"data": str(event.payload)},
            correlation_id=metadata.correlation_id,
            sequence_number=metadata.sequence_number,
            published_at=metadata.published_at,
            source_engine=metadata.source_engine,
            trace_id=metadata.trace_id,
        )
        self._event_store.append(entry)

        if self._persist_events:
            try:
                self._persistence_queue.put_nowait(entry)
            except asyncio.QueueFull:
                logger.warning("Persistence queue full")
                self._persistence_failures += 1

    async def _persistence_loop(self) -> None:
        while self._running:
            try:
                batch: List[EventStoreEntry] = []
                try:
                    entry = await asyncio.wait_for(self._persistence_queue.get(), timeout=self.PERSISTENCE_FLUSH_INTERVAL)
                    batch.append(entry)
                    for _ in range(self.PERSISTENCE_BATCH_SIZE - 1):
                        try:
                            entry = self._persistence_queue.get_nowait()
                            batch.append(entry)
                        except asyncio.QueueEmpty:
                            break
                    await self._persist_batch(batch)
                    for _ in batch:
                        self._persistence_queue.task_done()
                except asyncio.TimeoutError:
                    if self._persistence_batch:
                        await self._flush_persistence()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Persistence loop error: %s", exc)
                await self._persistence_breaker.record_failure()

    async def _persist_batch(self, batch: List[EventStoreEntry]) -> None:
        if not self._supabase:
            return
        if not await self._persistence_breaker.should_attempt():
            return
        try:
            records = [
                {
                    "event_id": e.event_id,
                    "event_type": e.event_type,
                    "payload": e.payload,
                    "correlation_id": e.correlation_id,
                    "sequence_number": e.sequence_number,
                    "published_at": e.published_at.isoformat(),
                    "source_engine": e.source_engine,
                }
                for e in batch
            ]
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._supabase.table("event_store").insert(records).execute(),
            )
            await self._persistence_breaker.record_success()
        except Exception as exc:
            logger.warning("Event persistence failed: %s", exc)
            self._persistence_failures += len(batch)
            await self._persistence_breaker.record_failure()

    async def _flush_persistence(self) -> None:
        if not self._persistence_batch:
            return
        batch = self._persistence_batch
        self._persistence_batch = []
        await self._persist_batch(batch)

    # ═══════════════════════════════════════════════════════════════
    # DEAD LETTERS
    # ═══════════════════════════════════════════════════════════════

    def _capture_dead_letter(self, event: DomainEvent, handler_name: str, error: Exception) -> None:
        event_type = self._event_type_name(event.event_type)
        event_id = getattr(event.metadata, "event_id", "unknown")

        for dl in self._dead_letters:
            if dl.event_id == event_id and dl.handler_name == handler_name:
                dl.attempt_count += 1
                dl.last_failure = datetime.now(timezone.utc)
                dl.error = str(error)
                return

        self._dead_letters.append(DeadLetter(
            event_id=event_id,
            event_type=event_type,
            payload=event.payload if isinstance(event.payload, dict) else {},
            error=str(error),
            handler_name=handler_name,
        ))

    def get_dead_letters(self, limit: int = 50) -> List[DeadLetter]:
        return list(self._dead_letters)[-limit:]

    async def retry_dead_letter(self, event_id: str) -> bool:
        for dl in self._dead_letters:
            if dl.event_id == event_id:
                await self.publish_event(dl.event_type, dl.payload, source="dead_letter_retry")
                self._dead_letters.remove(dl)
                return True
        return False

    # ═══════════════════════════════════════════════════════════════
    # THROTTLING
    # ═══════════════════════════════════════════════════════════════

    async def _check_throttle(self) -> bool:
        now = time.monotonic()
        while self._recent_events and self._recent_events[0] < now - self.THROTTLE_WINDOW_SECONDS:
            self._recent_events.popleft()

        if len(self._recent_events) >= self.MAX_EVENTS_PER_SECOND:
            self._throttled_count += 1
            if self._throttle_policy == ThrottlePolicy.DROP:
                return False
            elif self._throttle_policy == ThrottlePolicy.BLOCK:
                await asyncio.sleep(0.01)
                return await self._check_throttle()
            elif self._throttle_policy == ThrottlePolicy.DEFER:
                return False

        self._recent_events.append(now)
        return True

    def set_throttle_policy(self, policy: ThrottlePolicy) -> None:
        self._throttle_policy = policy

    # ═══════════════════════════════════════════════════════════════
    # CORRELATION
    # ═══════════════════════════════════════════════════════════════

    def _track_correlation(self, correlation_id: str, event_id: str) -> None:
        if correlation_id not in self._active_correlations:
            self._active_correlations[correlation_id] = {"events": [], "created_at": time.monotonic()}
        self._active_correlations[correlation_id]["events"].append(event_id)

    def get_correlation_chain(self, correlation_id: str) -> List[str]:
        if correlation_id in self._active_correlations:
            return list(self._active_correlations[correlation_id]["events"])
        return []

    async def _correlation_cleanup_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self.CORRELATION_TTL_SECONDS // 2)
                now = time.monotonic()
                expired = [
                    cid for cid, data in self._active_correlations.items()
                    if now - data["created_at"] > self.CORRELATION_TTL_SECONDS
                ]
                for cid in expired:
                    del self._active_correlations[cid]
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Correlation cleanup error: %s", exc)

    # ═══════════════════════════════════════════════════════════════
    # DIAGNOSTICS
    # ═══════════════════════════════════════════════════════════════

    def _next_sequence(self) -> int:
        self._sequence_number += 1
        return self._sequence_number

    @staticmethod
    def _event_type_name(event_type: Any) -> str:
        return event_type.value if hasattr(event_type, "value") else str(event_type)

    def replay_events(self, from_sequence: int = 0, event_types: Optional[List[str]] = None, limit: int = 100) -> List[EventStoreEntry]:
        events = [e for e in self._event_store if e.sequence_number >= from_sequence]
        if event_types:
            events = [e for e in events if e.event_type in event_types]
        return events[-limit:]

    def get_stats(self) -> dict:
        total_subscribers = sum(len(h) for h in self._subscribers.values()) + len(self._wildcard)
        return {
            "event_count": self._event_count,
            "error_count": self._error_count,
            "error_rate": round(self._error_count / max(self._event_count, 1) * 100, 2),
            "subscriber_count": total_subscribers,
            "event_types_tracked": len(self._subscribers),
            "wildcard_subscribers": len(self._wildcard),
            "async_mode": True,
            "persist_events": self._persist_events,
            "dead_letters": len(self._dead_letters),
            "event_store_size": len(self._event_store),
            "sequence_number": self._sequence_number,
            "throttled_count": self._throttled_count,
            "throttle_policy": self._throttle_policy.value,
            "active_correlations": len(self._active_correlations),
            "dispatch_queue_size": self._dispatch_queue.qsize(),
            "persistence_failures": self._persistence_failures,
            "persistence_circuit_breaker": self._persistence_breaker.state.value,
        }

    def health_check(self) -> dict:
        stats = self.get_stats()
        status = "healthy"
        if stats["error_rate"] > 10:
            status = "degraded"
        if stats["dead_letters"] > 100:
            status = "degraded"
        if stats["persistence_circuit_breaker"] == "open" and stats["persist_events"]:
            status = "degraded"
        return {"status": status, "stats": stats}


# ═══════════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════════

_bus: Optional[EventBus] = None
_bus_lock = asyncio.Lock()


async def get_event_bus(supabase_client=None, persist_events: bool = False) -> EventBus:
    """Get legacy EventBus singleton."""
    global _bus
    if _bus is None:
        async with _bus_lock:
            if _bus is None:
                _bus = EventBus(supabase_client=supabase_client, persist_events=persist_events)
                await _bus.start()
    try:
        get_event_bus_bridge(unified=_try_get_unified_sync())
    except Exception:
        pass
    return _bus


def get_event_bus_sync(supabase_client=None, persist_events: bool = False) -> EventBus:
    """Get or create the singleton event bus (sync)."""
    global _bus
    if _bus is None:
        try:
            loop = asyncio.get_running_loop()
            _bus = EventBus(supabase_client=supabase_client, persist_events=persist_events)
        except RuntimeError:
            _bus = EventBus(supabase_client=supabase_client, persist_events=persist_events)
            asyncio.run(_bus.start())
    return _bus


async def shutdown_event_bus() -> None:
    global _bus
    if _bus is not None:
        await _bus.stop()
        _bus = None


__all__ = [
    "EventBus",
    "DeadLetter",
    "EventStoreEntry",
    "ThrottlePolicy",
    "CircuitState",
    "PersistenceCircuitBreaker",
    "get_event_bus",
    "get_event_bus_sync",
    "shutdown_event_bus",
]


# ═══════════════════════════════════════════════════════════════════
# BRIDGE TO UNIFIED EVENT BUS
# ═══════════════════════════════════════════════════════════════════

def _try_get_unified_sync():
    try:
        from core import unified_bus as ub
        return getattr(ub, "_bus", None)
    except Exception:
        return None


def _domain_event_name(event) -> str:
    from core.events import DomainEvent, EventType
    if isinstance(event, DomainEvent):
        et = event.event_type
        return et.value if hasattr(et, "value") else str(et)
    if isinstance(event, EventType):
        return event.value
    if isinstance(event, str):
        return event
    if hasattr(event, "event_type"):
        et = event.event_type
        return et.value if hasattr(et, "value") else str(et)
    return type(event).__name__


def _domain_event_payload(event) -> dict:
    from core.events import DomainEvent
    if isinstance(event, DomainEvent):
        return dict(event.payload or {})
    if isinstance(event, dict):
        return event
    if hasattr(event, "payload"):
        return dict(getattr(event, "payload") or {})
    try:
        from dataclasses import asdict, is_dataclass
        if is_dataclass(event):
            return asdict(event)
    except Exception:
        pass
    return {"data": str(event)}


class EventBusBridge:
    """Compatibility façade for legacy DomainEvent callers.

    Architecture rule (Phase 4): UnifiedEventBus is the only authoritative
    in-process event transport.  The legacy EventBus is used only when no
    UnifiedEventBus is available (for isolated legacy/unit-test contexts).
    In particular, this bridge MUST NOT dual-publish to both buses because
    that creates duplicate domain events and breaks exactly-once assumptions
    in downstream consumers.
    """

    def __init__(self, unified=None, legacy=None):
        self._unified = unified
        self._legacy = legacy or EventBus(persist_events=False)
        self._unified_unsubscribers: list[Callable[[], None]] = []

    def set_unified(self, unified) -> None:
        self._unified = unified

    def _use_unified(self):
        return self._unified or _try_get_unified_sync()

    def subscribe(self, event_type, handler):
        name = _domain_event_name(event_type)
        unified = self._use_unified()
        if unified is None:
            return self._legacy.subscribe(event_type, handler)

        def _wrap(payload, source="unknown", event_id=None, **kwargs):
            from core.events import DomainEvent, EventType
            try:
                et = event_type if isinstance(event_type, EventType) else EventType(name) if name in EventType._value2member_map_ else name
            except Exception:
                et = name
            de = DomainEvent(
                event_type=et,
                payload=payload or {},
                source=source or "unified",
            )
            try:
                handler(de)
            except TypeError:
                handler(payload)

        import asyncio
        registration = {"unsubscribe": None}
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(unified.subscribe(name, _wrap))
        except RuntimeError:
            # A synchronous caller without an active loop cannot register an
            # async UnifiedEventBus subscription. Fall back explicitly rather
            # than silently maintaining two active buses.
            logger.warning("UnifiedEventBus subscription requested outside event loop; using legacy compatibility bus")
            return self._legacy.subscribe(event_type, handler)

        def unsubscribe() -> None:
            if registration["unsubscribe"] is not None:
                registration["unsubscribe"]()
            elif not task.done():
                task.cancel()

        def capture_result(done_task):
            if done_task.cancelled():
                return
            try:
                registration["unsubscribe"] = done_task.result()
            except Exception as exc:
                logger.error("UnifiedEventBus subscription failed for %s: %s", name, exc)

        task.add_done_callback(capture_result)
        return unsubscribe

    def publish_sync(self, event=None, payload=None, source="unknown", **kwargs):
        unified = self._use_unified()
        if unified is None:
            return self._legacy.publish_sync(event=event, payload=payload, source=source, **kwargs)

        name = _domain_event_name(event if event is not None else payload)
        pl = _domain_event_payload(event) if event is not None and not isinstance(event, str) else (payload or {})
        if isinstance(event, str):
            name = event
            pl = payload or {}
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(unified.publish(name, pl, source=source, **{
                k: kwargs[k] for k in ("correlation_id", "parent_event_id", "workspace_id", "brand_id", "decision_id", "strict_nerve") if k in kwargs
            }))
            return None
        except RuntimeError:
            return asyncio.run(unified.publish(name, pl, source=source, **{
                k: kwargs[k] for k in ("correlation_id", "parent_event_id", "workspace_id", "brand_id", "decision_id", "strict_nerve") if k in kwargs
            }))

    async def publish(self, event=None, payload=None, source="unknown", **kwargs):
        unified = self._use_unified()
        if unified is None:
            return await self._legacy.publish(event=event, payload=payload, source=source, **kwargs)

        name = _domain_event_name(event if event is not None else payload)
        pl = _domain_event_payload(event) if event is not None and not isinstance(event, str) else (payload or {})
        if isinstance(event, str):
            name = event
            pl = payload or {}
        allowed = {"correlation_id", "parent_event_id", "workspace_id", "brand_id", "decision_id", "strict_nerve"}
        return await unified.publish(
            name,
            pl,
            source=source,
            **{k: kwargs[k] for k in allowed if k in kwargs},
        )

    def health_check(self):
        unified = self._use_unified()
        if unified is not None and hasattr(unified, "health_check"):
            health = unified.health_check()
            if isinstance(health, dict):
                return {**health, "transport": "UnifiedEventBus", "compatibility_bridge": True}
            return health
        if hasattr(self._legacy, "health_check"):
            return {**self._legacy.health_check(), "transport": "LegacyEventBus", "compatibility_bridge": True}
        return {"status": "ok", "bridge": True}


_bridge: Optional[EventBusBridge] = None


def get_event_bus_bridge(unified=None) -> EventBusBridge:
    global _bridge
    if _bridge is None:
        _bridge = EventBusBridge(unified=unified)
    elif unified is not None:
        _bridge.set_unified(unified)
    return _bridge
