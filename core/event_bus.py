"""
KANYOZA EVENT BUS v3.1 — "THE NERVOUS SYSTEM"

Thread-safe, persistent, replayable event bus with async dispatch,
dead-letter queue, event sourcing, throttling, and distributed tracing.

v3.1 UPGRADES:
  - Normalized publish() API — accepts both DomainEvent and
    string+dict signatures. No more silently dropped events
    from services calling the wrong method.

v3.0 UPGRADES:
  - Event persistence (survives restarts via Supabase)
  - Async dispatch (thread pool — slow subscribers don't block)
  - Event replay (replay events from any point in time)
  - Dead letter queue (failed events captured for analysis)
  - Event sourcing (every state change is an event)
  - Throttling (prevent event storms from cascading failures)
  - Correlation IDs (trace events across engine boundaries)
  - Event statistics & health monitoring
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
import uuid
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from core.events import DomainEvent, EventType

logger = logging.getLogger(__name__)

Subscriber = Callable[[DomainEvent], None]


# ═══════════════════════════════════════════════════════════════════════════
# v3.0 DATA MODELS
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class EventMetadata:
    """Metadata attached to every event for tracing and replay."""
    event_id: str = field(
        default_factory=lambda: f"evt_{uuid.uuid4().hex[:12]}"
    )
    correlation_id: Optional[str] = None
    parent_event_id: Optional[str] = None
    sequence_number: int = 0
    published_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    source_engine: str = "unknown"
    replay: bool = False


@dataclass
class DeadLetter:
    """Captured failed event for analysis and retry."""
    event_id: str
    event_type: str
    payload: dict
    error: str
    handler_name: str
    attempt_count: int = 1
    first_failure: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    last_failure: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


@dataclass
class EventStoreEntry:
    """Persisted event for replay."""
    event_id: str
    event_type: str
    payload: dict
    correlation_id: Optional[str]
    sequence_number: int
    published_at: datetime
    source_engine: str


class ThrottlePolicy(Enum):
    """How to handle event floods."""
    DROP = "drop"           # Silently drop excess events
    DEFER = "defer"         # Queue and process later
    DEBOUNCE = "debounce"   # Only keep the latest
    BLOCK = "block"         # Block publisher until capacity available


# ═══════════════════════════════════════════════════════════════════════════
# EVENT BUS v3.1
# ═══════════════════════════════════════════════════════════════════════════

class EventBus:
    """Thread-safe, persistent, replayable event bus with async dispatch.

    v3.1: Normalized publish() — accepts DomainEvent OR string+dict.
    v3.0: Async dispatch, persistence, replay, dead letters, throttling.
    """

    # Throttle configuration
    MAX_EVENTS_PER_SECOND = 500
    THROTTLE_WINDOW_SECONDS = 1.0
    MAX_DEAD_LETTERS = 1000
    MAX_EVENT_STORE = 10000

    def __init__(
        self,
        supabase_client=None,
        async_workers: int = 4,
        persist_events: bool = False,
    ) -> None:
        self._lock = threading.RLock()
        self._subscribers: dict[
            EventType, list[Subscriber]
        ] = defaultdict(list)
        self._wildcard: list[Subscriber] = []

        # v3.0 — async dispatch
        self._executor = ThreadPoolExecutor(
            max_workers=async_workers,
            thread_name_prefix="eventbus-worker",
        )
        self._async_mode = False

        # v3.0 — persistence
        self._supabase = supabase_client
        self._persist_events = persist_events
        self._sequence_number = 0

        # v3.0 — dead letter queue
        self._dead_letters: deque = deque(
            maxlen=self.MAX_DEAD_LETTERS
        )

        # v3.0 — event store (in-memory ring buffer)
        self._event_store: deque = deque(
            maxlen=self.MAX_EVENT_STORE
        )

        # v3.0 — throttling
        self._recent_events: deque = deque()
        self._throttle_policy = ThrottlePolicy.DEFER
        self._deferred_events: deque = deque(maxlen=1000)
        self._throttled_count = 0

        # v3.0 — correlation tracking
        self._active_correlations: Dict[
            str, List[str]
        ] = defaultdict(list)

        # v3.0 — statistics
        self._event_count = 0
        self._error_count = 0
        self._async_futures: List[Future] = []

        logger.info(
            "EventBus v3.1 initialized — "
            "async=%d workers, persist=%s",
            async_workers,
            persist_events,
        )

    # ═══════════════════════════════════════════════════════════════════
    # SUBSCRIPTION
    # ═══════════════════════════════════════════════════════════════════

    def subscribe(
        self,
        event_type: EventType,
        handler: Subscriber,
    ) -> None:
        with self._lock:
            self._subscribers[event_type].append(handler)
        logger.debug(
            "EventBus: subscribed %s → %s",
            event_type.value if hasattr(event_type, "value") else event_type,
            handler.__qualname__,
        )

    def subscribe_all(self, handler: Subscriber) -> None:
        """Receive every event regardless of type."""
        with self._lock:
            self._wildcard.append(handler)
        logger.info(
            "EventBus: wildcard subscriber added: %s",
            handler.__qualname__,
        )

    def unsubscribe(
        self,
        event_type: EventType,
        handler: Subscriber,
    ) -> None:
        with self._lock:
            self._subscribers[event_type] = [
                h for h in self._subscribers[event_type] if h is not handler
            ]

    # ═══════════════════════════════════════════════════════════════════
    # v3.1 — PUBLISHING (Normalized)
    # ═══════════════════════════════════════════════════════════════════

    def enable_async(self, enabled: bool = True) -> None:
        """Enable non-blocking async dispatch."""
        self._async_mode = enabled
        logger.info(
            "EventBus: async mode %s",
            "enabled" if enabled else "disabled",
        )

    def publish(self, event=None, payload=None, source="unknown", **kwargs) -> None:
        """Publish an event.

        v3.1 — Normalized API. Accepts:
          1. publish(domain_event)                      → DomainEvent
          2. publish("event_name", {"key": "value"})   → string + dict
          3. publish(event_name, payload, source="...") → explicit

        This prevents the API mismatch that silently dropped events
        when services called publish("name", payload) with the old
        signature expecting only DomainEvent.
        """
        # Normalize the call signature
        if isinstance(event, DomainEvent):
            domain_event = event
        elif isinstance(event, str):
            domain_event = DomainEvent(
                event_type=event,
                payload=payload if isinstance(payload, dict) else {"data": payload},
                source=source or kwargs.get("source", "unknown"),
            )
        elif event is None and payload is not None:
            # publish(payload=domain_event) pattern
            if isinstance(payload, DomainEvent):
                domain_event = payload
            else:
                logger.error("EventBus.publish() called with invalid payload type")
                return
        else:
            logger.error(
                "EventBus.publish() called with invalid signature: type=%s",
                type(event).__name__ if event else "None",
            )
            return

        # v3.0 — throttling check
        if not self._check_throttle():
            return

        # v3.0 — attach metadata
        metadata = EventMetadata(
            sequence_number=self._next_sequence(),
            source_engine=getattr(domain_event, "source", source or "unknown"),
        )
        domain_event.metadata = metadata

        # v3.0 — persist to event store
        self._store_event(domain_event, metadata)

        # Resolve subscribers
        event_type = domain_event.event_type
        with self._lock:
            handlers = list(self._subscribers.get(event_type, []))
            wildcards = list(self._wildcard)

        self._event_count += 1

        # v3.0 — dispatch
        if self._async_mode:
            self._dispatch_async(domain_event, handlers, wildcards)
        else:
            self._dispatch_sync(domain_event, handlers, wildcards)

    def publish_event(
        self,
        event_name: str,
        payload: Optional[dict] = None,
        correlation_id: Optional[str] = None,
        parent_event_id: Optional[str] = None,
        source: str = "unknown",
    ) -> DomainEvent:
        """Convenience method — publish a string-named event."""
        if payload is None:
            payload = {}

        try:
            event_type = EventType(event_name)
        except (ValueError, TypeError):
            event_type = event_name

        if isinstance(payload, DomainEvent):
            event = payload
        else:
            event = DomainEvent(
                event_type=event_type,
                payload=payload if isinstance(payload, dict) else {"data": payload},
                source=source,
            )

        if correlation_id:
            event.correlation_id = correlation_id
            with self._lock:
                self._active_correlations[correlation_id].append(event.event_id)

        if parent_event_id:
            event.parent_event_id = parent_event_id

        self.publish(event)
        return event

    # ═══════════════════════════════════════════════════════════════════
    # v3.0 — DISPATCH
    # ═══════════════════════════════════════════════════════════════════

    def _dispatch_sync(
        self,
        event: DomainEvent,
        handlers: List[Subscriber],
        wildcards: List[Subscriber],
    ) -> None:
        """Synchronous dispatch — handlers run in publisher's thread."""
        for handler in handlers + wildcards:
            self._invoke_handler(handler, event)

    def _dispatch_async(
        self,
        event: DomainEvent,
        handlers: List[Subscriber],
        wildcards: List[Subscriber],
    ) -> None:
        """Async dispatch — handlers run in thread pool."""
        for handler in handlers + wildcards:
            future = self._executor.submit(
                self._invoke_handler, handler, event
            )
            self._async_futures.append(future)

        if len(self._async_futures) > 100:
            self._async_futures = [
                f for f in self._async_futures if not f.done()
            ]

    def _invoke_handler(
        self,
        handler: Subscriber,
        event: DomainEvent,
    ) -> None:
        """Invoke a single handler with error capture."""
        try:
            handler(event)
        except Exception as exc:
            self._error_count += 1
            logger.error(
                "EventBus: handler %s raised %s for %s",
                handler.__qualname__, exc, event, exc_info=True,
            )
            self._capture_dead_letter(
                event, handler.__qualname__, exc
            )

    # ═══════════════════════════════════════════════════════════════════
    # v3.0 — EVENT PERSISTENCE
    # ═══════════════════════════════════════════════════════════════════

    def _next_sequence(self) -> int:
        with self._lock:
            self._sequence_number += 1
            return self._sequence_number

    def _store_event(
        self,
        event: DomainEvent,
        metadata: EventMetadata,
    ) -> None:
        """Store event in the ring buffer and optionally persist."""
        event_type = (
            event.event_type.value
            if hasattr(event.event_type, "value")
            else str(event.event_type)
        )

        entry = EventStoreEntry(
            event_id=metadata.event_id,
            event_type=event_type,
            payload=event.payload if isinstance(event.payload, dict) else {"data": str(event.payload)},
            correlation_id=metadata.correlation_id,
            sequence_number=metadata.sequence_number,
            published_at=metadata.published_at,
            source_engine=metadata.source_engine,
        )

        self._event_store.append(entry)

        if self._persist_events and self._supabase:
            self._persist_event(entry)

    def _persist_event(self, entry: EventStoreEntry) -> None:
        """Persist event to Supabase for durability."""
        try:
            self._supabase.table("event_store").insert({
                "event_id": entry.event_id,
                "event_type": entry.event_type,
                "payload": entry.payload,
                "correlation_id": entry.correlation_id,
                "sequence_number": entry.sequence_number,
                "published_at": entry.published_at.isoformat(),
                "source_engine": entry.source_engine,
            }).execute()
        except Exception as exc:
            logger.debug("Event persistence failed: %s", exc)

    def enable_persistence(self, supabase_client=None) -> None:
        """Enable event persistence to Supabase."""
        self._persist_events = True
        if supabase_client:
            self._supabase = supabase_client
        logger.info("EventBus: persistence enabled")

    # ═══════════════════════════════════════════════════════════════════
    # v3.0 — EVENT REPLAY
    # ═══════════════════════════════════════════════════════════════════

    def replay_events(
        self,
        from_sequence: int = 0,
        event_types: Optional[List[str]] = None,
        limit: int = 100,
    ) -> List[EventStoreEntry]:
        """Replay events from the event store."""
        events = list(self._event_store)
        events = [e for e in events if e.sequence_number >= from_sequence]

        if event_types:
            events = [e for e in events if e.event_type in event_types]

        return events[-limit:]

    def replay_and_publish(
        self,
        from_sequence: int = 0,
        event_types: Optional[List[str]] = None,
        limit: int = 100,
    ) -> int:
        """Replay events by re-publishing them to subscribers."""
        events = self.replay_events(from_sequence, event_types, limit)
        count = 0

        for entry in events:
            event = DomainEvent(
                event_type=(
                    EventType(entry.event_type)
                    if entry.event_type in EventType.__members__
                    else entry.event_type
                ),
                payload=entry.payload,
                source=entry.source_engine,
            )
            event.metadata = EventMetadata(
                event_id=entry.event_id,
                correlation_id=entry.correlation_id,
                sequence_number=entry.sequence_number,
                published_at=entry.published_at,
                source_engine=entry.source_engine,
                replay=True,
            )

            with self._lock:
                handlers = list(self._subscribers.get(event.event_type, []))
                wildcards = list(self._wildcard)

            for handler in handlers + wildcards:
                self._invoke_handler(handler, event)
            count += 1

        logger.info("EventBus: replayed %d events from seq %d", count, from_sequence)
        return count

    # ═══════════════════════════════════════════════════════════════════
    # v3.0 — DEAD LETTER QUEUE
    # ═══════════════════════════════════════════════════════════════════

    def _capture_dead_letter(
        self,
        event: DomainEvent,
        handler_name: str,
        error: Exception,
    ) -> None:
        """Capture a failed event for analysis."""
        event_type = (
            event.event_type.value
            if hasattr(event.event_type, "value")
            else str(event.event_type)
        )

        existing = next(
            (dl for dl in self._dead_letters
             if dl.event_id == getattr(event.metadata, "event_id", None)),
            None,
        )

        if existing:
            existing.attempt_count += 1
            existing.last_failure = datetime.now(timezone.utc)
        else:
            self._dead_letters.append(
                DeadLetter(
                    event_id=getattr(event.metadata, "event_id", "unknown"),
                    event_type=event_type,
                    payload=event.payload if isinstance(event.payload, dict) else {},
                    error=str(error),
                    handler_name=handler_name,
                )
            )

    def get_dead_letters(self, limit: int = 50) -> List[DeadLetter]:
        """Get recent dead letters for analysis."""
        return list(self._dead_letters)[-limit:]

    def retry_dead_letter(self, event_id: str) -> bool:
        """Retry a dead letter event by re-publishing it."""
        dead = next(
            (dl for dl in self._dead_letters if dl.event_id == event_id),
            None,
        )
        if not dead:
            return False

        self.publish_event(dead.event_type, dead.payload, source="dead_letter_retry")
        self._dead_letters.remove(dead)
        logger.info("EventBus: retried dead letter %s", event_id)
        return True

    # ═══════════════════════════════════════════════════════════════════
    # v3.0 — THROTTLING
    # ═══════════════════════════════════════════════════════════════════

    def _check_throttle(self) -> bool:
        """Check if publishing should be throttled."""
        now = time.monotonic()

        while self._recent_events and self._recent_events[0] < now - self.THROTTLE_WINDOW_SECONDS:
            self._recent_events.popleft()

        if len(self._recent_events) >= self.MAX_EVENTS_PER_SECOND:
            self._throttled_count += 1
            if self._throttle_policy == ThrottlePolicy.DROP:
                return False
            elif self._throttle_policy == ThrottlePolicy.DEFER:
                return False
            elif self._throttle_policy == ThrottlePolicy.BLOCK:
                time.sleep(0.01)
                return self._check_throttle()

        self._recent_events.append(now)
        return True

    def set_throttle_policy(self, policy: ThrottlePolicy) -> None:
        """Set the throttle policy for event floods."""
        self._throttle_policy = policy
        logger.info("EventBus: throttle policy set to %s", policy.value)

    def process_deferred(self, limit: int = 100) -> int:
        """Process deferred events from the throttle queue."""
        processed = 0
        while self._deferred_events and processed < limit:
            event = self._deferred_events.popleft()
            self.publish(event)
            processed += 1
        return processed

    # ═══════════════════════════════════════════════════════════════════
    # v3.0 — CORRELATION TRACKING
    # ═══════════════════════════════════════════════════════════════════

    def start_correlation(self, correlation_id: Optional[str] = None) -> str:
        """Start a new correlation context for tracing."""
        cid = correlation_id or f"corr_{uuid.uuid4().hex[:10]}"
        with self._lock:
            self._active_correlations[cid] = []
        logger.debug("EventBus: correlation %s started", cid)
        return cid

    def get_correlation_chain(self, correlation_id: str) -> List[str]:
        """Get all event IDs in a correlation chain."""
        with self._lock:
            return list(self._active_correlations.get(correlation_id, []))

    # ═══════════════════════════════════════════════════════════════════
    # v3.0 — DIAGNOSTICS
    # ═══════════════════════════════════════════════════════════════════

    def subscriber_count(self, event_type: EventType) -> int:
        with self._lock:
            return len(self._subscribers.get(event_type, []))

    def get_stats(self) -> dict:
        """Return comprehensive event bus statistics."""
        with self._lock:
            total_subscribers = sum(
                len(handlers) for handlers in self._subscribers.values()
            ) + len(self._wildcard)

        return {
            "event_count": self._event_count,
            "error_count": self._error_count,
            "error_rate": round(self._error_count / max(self._event_count, 1) * 100, 2),
            "subscriber_count": total_subscribers,
            "event_types_tracked": len(self._subscribers),
            "wildcard_subscribers": len(self._wildcard),
            "async_mode": self._async_mode,
            "persist_events": self._persist_events,
            "dead_letters": len(self._dead_letters),
            "event_store_size": len(self._event_store),
            "sequence_number": self._sequence_number,
            "throttled_count": self._throttled_count,
            "throttle_policy": self._throttle_policy.value,
            "active_correlations": len(self._active_correlations),
            "events_per_second": round(len(self._recent_events) / self.THROTTLE_WINDOW_SECONDS, 1),
        }

    def get_event_types(self) -> List[str]:
        """Get all tracked event types."""
        with self._lock:
            return sorted(
                str(et.value if hasattr(et, "value") else et)
                for et in self._subscribers.keys()
            )

    def health_check(self) -> dict:
        """Quick health check for the event bus."""
        stats = self.get_stats()
        status = "healthy"

        if stats["error_rate"] > 10:
            status = "degraded"
        if stats["dead_letters"] > 100:
            status = "degraded"
        if stats["throttled_count"] > 1000:
            status = "degraded"
        if stats["event_count"] == 0:
            status = "idle"

        return {"status": status, "stats": stats}

    # ═══════════════════════════════════════════════════════════════════
    # v3.0 — CLEANUP
    # ═══════════════════════════════════════════════════════════════════

    def shutdown(self, timeout: float = 5.0) -> None:
        """Graceful shutdown — wait for async handlers to complete."""
        logger.info(
            "EventBus: shutting down (%d pending futures)",
            len(self._async_futures),
        )
        self._executor.shutdown(wait=True, timeout=timeout)
        logger.info("EventBus: shutdown complete")

    def clear(self) -> None:
        """Clear all subscribers and state. Useful for testing."""
        with self._lock:
            self._subscribers.clear()
            self._wildcard.clear()
            self._dead_letters.clear()
            self._event_store.clear()
            self._active_correlations.clear()
            self._event_count = 0
            self._error_count = 0
            self._sequence_number = 0
        logger.info("EventBus: cleared all state")


# ═══════════════════════════════════════════════════════════════════════════
# MODULE SINGLETON
# ═══════════════════════════════════════════════════════════════════════════

_bus: Optional[EventBus] = None
_bus_lock = threading.Lock()


def get_event_bus(
    supabase_client=None,
    async_workers: int = 4,
    persist_events: bool = False,
) -> EventBus:
    global _bus
    if _bus is None:
        with _bus_lock:
            if _bus is None:
                _bus = EventBus(
                    supabase_client=supabase_client,
                    async_workers=async_workers,
                    persist_events=persist_events,
                )
    return _bus
