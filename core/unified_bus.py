"""
KANYOZA UNIFIED EVENT BUS v17.2 — "THE NERVOUS SYSTEM"

v17.2 FIXES:
  - publish() no longer silently drops events when the bus was constructed
    but never started. The factory in dependency_registry._build_unified_bus
    builds an instance without calling start(); services built during
    build_container() (pipeline, guardian, recorder, plugins, scheduler)
    hold that instance. lifecycle._boot_event_bus creates a second, started
    instance and overwrites the container binding, but the earlier-created
    services keep the unstarted reference. The result was that every publish
    from those services logged "Cannot publish — bus not running" and
    returned None — a silent loss of every event from the pre-boot half of
    the system.
  - The guard now performs a lazy start on first publish. UnifiedEventBus.
    start() is already idempotent (returns immediately if _running is True),
    so races with lifecycle's explicit start are safe. The bus becomes
    self-starting on first use instead of requiring an external caller to
    have started it.
  - If the lazy start raises, the event is still dropped but with an error
    that names the failure, not a misleading "not running" warning.
  - U1 composition-root wiring now adopts the module singleton during DI
    construction and lifecycle boot resolves and starts that same instance.
    DI-created services and legacy get_unified_bus() callers therefore share
    one in-process subscriber registry. SharedEventBus remains the separate
    cross-process transport; this singleton is not a distributed bus.

v17.1 FIXES:
  - Added _validate_payload method (was missing)
  - Added _check_throttle method (was missing)
  - Added _next_sequence method (was missing)

v17.0 CHANGES:
  - Handlers now receive FULL event context
  - Backward compatible with handlers expecting only payload
  - Improved handler invocation with proper error capture
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional
from utils.ids import correlation_id_ctx, get_process_instance_id
from core.event_provenance import ProducerCapability, ProducerRegistry, VerifiedEventProvenance, current_actor_principal

logger = logging.getLogger("kanyoza.unified_bus")


# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class EventBusConfig:
    max_queue_size: int = 10000
    max_payload_size: int = 1_048_576  # 1MB
    max_dead_letters: int = 1000
    max_event_store: int = 10000
    max_events_per_second: int = 500
    throttle_window_seconds: float = 1.0
    persistence_batch_size: int = 10
    persistence_flush_interval: float = 1.0
    circuit_failure_threshold: int = 5
    circuit_recovery_timeout: float = 30.0
    correlation_ttl_seconds: int = 3600


# ═══════════════════════════════════════════════════════════════════════════
# DATA MODELS
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class EventMetadata:
    event_id: str = field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:12]}")
    correlation_id: Optional[str] = None
    parent_event_id: Optional[str] = None
    sequence_number: int = 0
    published_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source_engine: str = "unknown"
    replay: bool = False


@dataclass
class DeadLetter:
    event_id: str
    event_name: str
    payload: dict
    error: str
    handler_name: str
    attempt_count: int = 1
    first_failure: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_failure: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class EventStoreEntry:
    event_id: str
    event_name: str
    payload: dict
    correlation_id: Optional[str]
    sequence_number: int
    published_at: datetime
    source_engine: str
    workspace_id: Optional[str] = None
    brand_id: Optional[str] = None
    decision_id: Optional[str] = None
    parent_event_id: Optional[str] = None
    schema_version: str = "1.0"


# ═══════════════════════════════════════════════════════════════════════════
# CIRCUIT STATE
# ═══════════════════════════════════════════════════════════════════════════

class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpenError(Exception):
    def __init__(self, name: str):
        self.name = name
        super().__init__(f"Circuit breaker '{name}' is OPEN")


# ═══════════════════════════════════════════════════════════════════════════
# THROTTLE POLICY
# ═══════════════════════════════════════════════════════════════════════════

class ThrottlePolicy(Enum):
    DROP = "drop"
    DEFER = "defer"
    BLOCK = "block"


# ═══════════════════════════════════════════════════════════════════════════
# UNIFIED EVENT BUS
# ═══════════════════════════════════════════════════════════════════════════

class UnifiedEventBus:
    """Single async-native event bus for the entire Kanyoza platform."""

    def __init__(
        self,
        supabase_client: Any = None,
        config: EventBusConfig | None = None,
        producer_registry: ProducerRegistry | None = None,
    ):
        self.config = config or EventBusConfig()
        # Only a trusted composition root may inject this registry. Legacy
        # source labels remain descriptive and never produce provenance.
        self._producer_registry = producer_registry
        self.supabase = supabase_client

        # Subscribers
        self._subscribers: Dict[str, List[Callable]] = {}
        self._wildcard: List[Callable] = []
        self._lock = asyncio.Lock()

        # Dispatch queue
        self._dispatch_queue: asyncio.Queue = asyncio.Queue(
            maxsize=self.config.max_queue_size
        )
        self._dispatch_task: Optional[asyncio.Task] = None

        # Persistence queue
        self._persistence_queue: asyncio.Queue = asyncio.Queue(
            maxsize=self.config.max_queue_size
        )
        self._persistence_task: Optional[asyncio.Task] = None

        # Circuit breaker
        self._circuit_state = CircuitState.CLOSED
        self._circuit_failures = 0
        self._circuit_last_failure_time: Optional[float] = None

        # Event store
        self._event_store: deque = deque(maxlen=self.config.max_event_store)
        self._sequence_number = 0

        # Dead letter queue
        self._dead_letters: deque = deque(maxlen=self.config.max_dead_letters)

        # Throttling
        self._recent_events: deque = deque()
        self._throttle_policy = ThrottlePolicy.DEFER
        self._throttled_count = 0

        # Correlation tracking
        self._active_correlations: Dict[str, Dict[str, Any]] = {}
        self._correlation_cleanup_task: Optional[asyncio.Task] = None

        # Statistics
        self._events_published = 0
        self._events_dropped = 0
        self._handler_errors = 0
        self._persistence_failures = 0

        # State
        self._running = False
        self._started_at: Optional[datetime] = None

        logger.info(
            "UnifiedEventBus v17.2 initialized — queue=%d, persist=%s",
            self.config.max_queue_size,
            self.supabase is not None,
        )

    # ═══════════════════════════════════════════════════════════════════
    # LIFECYCLE
    # ═══════════════════════════════════════════════════════════════════

    async def start(self) -> None:
        if self._running:
            logger.warning("UnifiedEventBus already running")
            return

        self._running = True
        self._started_at = datetime.now(timezone.utc)

        self._dispatch_task = asyncio.create_task(
            self._dispatch_loop(),
            name="unified-bus-dispatch",
        )

        if self.supabase:
            self._persistence_task = asyncio.create_task(
                self._persistence_loop(),
                name="unified-bus-persistence",
            )

        self._correlation_cleanup_task = asyncio.create_task(
            self._correlation_cleanup_loop(),
            name="unified-bus-correlation-cleanup",
        )

        logger.info("✅ UnifiedEventBus started")

    async def stop(self, timeout: float = 5.0) -> None:
        if not self._running:
            return

        logger.info("UnifiedEventBus: shutting down...")
        self._running = False

        # Stop the persistence worker first so an in-flight batch is returned
        # to the queue before the final shutdown flush runs.
        if self._persistence_task and not self._persistence_task.done():
            self._persistence_task.cancel()
            try:
                await self._persistence_task
            except asyncio.CancelledError:
                pass

        if self.supabase and self._persistence_queue.qsize() > 0:
            await self._flush_persistence()

        for task in [self._dispatch_task, self._correlation_cleanup_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        self._dispatch_task = None
        self._persistence_task = None
        self._correlation_cleanup_task = None

        logger.info("✅ UnifiedEventBus stopped")

    # ═══════════════════════════════════════════════════════════════════
    # SUBSCRIBE
    # ═══════════════════════════════════════════════════════════════════

    async def subscribe(
        self,
        event_name: str,
        handler: Callable[[Dict[str, Any]], Any],
    ) -> Callable[[], None]:
        async with self._lock:
            if event_name not in self._subscribers:
                self._subscribers[event_name] = []
            self._subscribers[event_name].append(handler)

        def unsubscribe() -> None:
            if event_name in self._subscribers:
                self._subscribers[event_name] = [
                    h for h in self._subscribers[event_name] if h is not handler
                ]

        return unsubscribe

    async def subscribe_all(
        self,
        handler: Callable[[Dict[str, Any]], Any],
    ) -> Callable[[], None]:
        async with self._lock:
            self._wildcard.append(handler)

        def unsubscribe() -> None:
            self._wildcard = [h for h in self._wildcard if h is not handler]

        return unsubscribe

    # ═══════════════════════════════════════════════════════════════════
    # ✅ v17.1: MISSING METHODS ADDED
    # ═══════════════════════════════════════════════════════════════════

    def _validate_payload(self, payload: dict) -> bool:
        """Validate payload size before publishing."""
        try:
            size = len(json.dumps(payload, default=str))
            if size > self.config.max_payload_size:
                logger.error(
                    "Payload too large: %d bytes (max %d)",
                    size,
                    self.config.max_payload_size,
                )
                return False
            return True
        except (TypeError, ValueError):
            logger.error("Payload not JSON serializable")
            return False

    def _check_throttle(self) -> bool:
        """Check if publishing should be throttled."""
        now = time.monotonic()

        # Clean old events
        while (
            self._recent_events
            and self._recent_events[0] < now - self.config.throttle_window_seconds
        ):
            self._recent_events.popleft()

        # Check if over limit
        if len(self._recent_events) >= self.config.max_events_per_second:
            self._throttled_count += 1

            if self._throttle_policy == ThrottlePolicy.DROP:
                logger.warning("Event dropped due to throttling")
                return False
            elif self._throttle_policy == ThrottlePolicy.DEFER:
                # publish() waits asynchronously for rolling-window capacity.
                return True

        self._recent_events.append(now)
        return True

    async def _wait_for_throttle_capacity(self) -> None:
        """Wait until the configured rolling rate window has capacity."""
        while True:
            now = time.monotonic()
            while (
                self._recent_events
                and self._recent_events[0] < now - self.config.throttle_window_seconds
            ):
                self._recent_events.popleft()
            if len(self._recent_events) < self.config.max_events_per_second:
                self._recent_events.append(now)
                return
            self._throttled_count += 1
            delay = max(
                0.001,
                self._recent_events[0] + self.config.throttle_window_seconds - now,
            )
            await asyncio.sleep(delay)

    def _next_sequence(self) -> int:
        """Get next sequence number."""
        self._sequence_number += 1
        return self._sequence_number

    # ═══════════════════════════════════════════════════════════════════
    # PUBLISH
    # ═══════════════════════════════════════════════════════════════════

    async def publish(
        self,
        event_name: str,
        payload: Optional[Dict[str, Any]] = None,
        source: str = "unknown",
        correlation_id: Optional[str] = None,
        parent_event_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        brand_id: Optional[str] = None,
        decision_id: Optional[str] = None,
        *,
        strict_nerve: bool = False,
        schema_version: str = "1.0",
        producer_capability: ProducerCapability | None = None,
    ) -> Optional[str]:
        """
        Publish an event onto the nervous system.

        Phase A/B: every event is normalized through the nerve contract.
        Prefer passing workspace_id explicitly. Legacy callers without it
        are accepted with a warning until Phase B strict mode is enabled.

        v17.2: If this bus instance was constructed but never started
        (the dependency_registry factory path), start it lazily on first
        publish. start() is idempotent, so this is safe even if a later
        lifecycle step also calls start() on the same instance. Without
        this, every pre-boot service silently dropped its events with a
        misleading "not running" warning.
        """
        if not self._running:
            try:
                await self.start()
            except Exception as exc:
                logger.error(
                    "Cannot publish '%s' — bus failed to start lazily: %s",
                    event_name,
                    exc,
                )
                return None

        event_id = f"evt_{uuid.uuid4().hex[:12]}"
        try:
            from core.nerve import validate_and_normalize, NerveContractError
            if not workspace_id:
                try:
                    from core.workspace_context import get_current_workspace
                    workspace_id = get_current_workspace()
                except PermissionError:
                    workspace_id = None
            # Missing identity is strict by default: tenant events must not be
            # persisted under an "unbound" pseudo-workspace. Platform event
            # types are still normalized to the platform workspace by Nerve.
            effective_strict = strict_nerve or not bool(workspace_id)
            envelope = validate_and_normalize(
                event_name,
                payload,
                source=source,
                event_id=event_id,
                correlation_id=correlation_id,
                workspace_id=workspace_id,
                brand_id=brand_id,
                decision_id=decision_id,
                parent_event_id=parent_event_id,
                schema_version=schema_version,
                strict=effective_strict,
            )
            payload = envelope.to_payload()
            workspace_id = envelope.workspace_id
            brand_id = envelope.brand_id
            decision_id = envelope.decision_id
            parent_event_id = envelope.parent_event_id
            correlation_id = envelope.correlation_id
            source = envelope.source
        except NerveContractError as exc:
            logger.error("Nerve contract rejected event %s: %s", event_name, exc)
            return None
        except Exception as exc:
            logger.warning("Nerve normalize fallback for %s: %s", event_name, exc)
            payload = payload or {}

        verified_provenance = None
        if producer_capability is not None:
            if self._producer_registry is None:
                logger.error("Publisher capability supplied but no trusted registry is configured")
                return None
            try:
                verified_provenance = self._producer_registry.verify(
                    producer_capability,
                    workspace_id=workspace_id,
                    actor=current_actor_principal(),
                    brand_id=brand_id,
                )
            except Exception as exc:
                logger.warning("Event publisher provenance rejected for %s: %s", event_name, exc)
                return None

        if not self._validate_payload(payload):
            return None

        if self._throttle_policy in (ThrottlePolicy.DEFER, ThrottlePolicy.BLOCK):
            await self._wait_for_throttle_capacity()
        elif not self._check_throttle():
            return None

        metadata = EventMetadata(
            event_id=event_id,
            correlation_id=correlation_id,
            parent_event_id=parent_event_id,
            sequence_number=self._next_sequence(),
            source_engine=source,
        )

        if correlation_id:
            self._track_correlation(correlation_id, event_id)

        entry = EventStoreEntry(
            event_id=event_id,
            event_name=event_name,
            payload=payload,
            correlation_id=correlation_id,
            sequence_number=metadata.sequence_number,
            published_at=metadata.published_at,
            source_engine=source,
            workspace_id=workspace_id,
            brand_id=brand_id,
            decision_id=decision_id,
            parent_event_id=parent_event_id,
            schema_version=schema_version,
        )
        self._event_store.append(entry)

        # A successful publish acknowledgement means the durable event write
        # completed. The persistence worker retries transient failures and
        # applies bounded queue backpressure while this future remains pending.
        if self.supabase:
            persisted = asyncio.get_running_loop().create_future()
            pending_item = (entry, persisted)
            try:
                if self._throttle_policy == ThrottlePolicy.DROP:
                    self._persistence_queue.put_nowait(pending_item)
                else:
                    await self._persistence_queue.put(pending_item)
            except asyncio.QueueFull:
                logger.warning("Persistence queue FULL — dropping event: %s", event_name)
                self._events_dropped += 1
                return None

            try:
                await persisted
            except Exception as exc:
                logger.error(
                    "Event %s was not durably persisted; publication rejected: %s",
                    event_name,
                    exc,
                )
                return None

        # Provenance is carried out-of-band to in-process handlers only.
        # It is deliberately NOT serialized into the event payload or current
        # durable row; database replay therefore remains untrusted.
        dispatch_item = (
            event_name, payload, source, event_id, workspace_id, brand_id,
            decision_id, correlation_id, parent_event_id, verified_provenance,
        )
        try:
            if self._throttle_policy == ThrottlePolicy.DROP:
                self._dispatch_queue.put_nowait(dispatch_item)
            else:
                await self._dispatch_queue.put(dispatch_item)
        except asyncio.QueueFull:
            logger.warning("Dispatch queue FULL — dropping event: %s", event_name)
            self._events_dropped += 1
            return None

        self._events_published += 1
        return event_id

    async def publish_event(
        self,
        event_name: str,
        payload: Optional[dict] = None,
        source: str = "unknown",
    ) -> Optional[str]:
        return await self.publish(event_name, payload, source)

    # ═══════════════════════════════════════════════════════════════════
    # DISPATCH
    # ═══════════════════════════════════════════════════════════════════

    async def _dispatch_loop(self) -> None:
        while self._running:
            try:
                event_name, payload, source, event_id, workspace_id, brand_id, decision_id, correlation_id, parent_event_id, provenance = await self._dispatch_queue.get()
                await self._dispatch_event(event_name, payload, source, event_id, workspace_id, brand_id, decision_id, correlation_id, parent_event_id, provenance)
                self._dispatch_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Dispatch loop error: %s", exc, exc_info=True)

    async def _dispatch_event(
        self,
        event_name: str,
        payload: dict,
        source: str,
        event_id: str,
        workspace_id: Optional[str] = None,
        brand_id: Optional[str] = None,
        decision_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        parent_event_id: Optional[str] = None,
        provenance: VerifiedEventProvenance | None = None,
    ) -> None:
        async with self._lock:
            handlers = list(self._subscribers.get(event_name, []))
            wildcards = list(self._wildcard)

        # Await the full handler set before acknowledging this dispatch item.
        # Detached tasks could outlive shutdown and escape queue accounting.
        await asyncio.gather(*[
            self._invoke_handler(
                handler,
                event_name,
                payload,
                source,
                event_id,
                workspace_id,
                brand_id,
                decision_id,
                correlation_id,
                parent_event_id,
                provenance,
            )
            for handler in handlers + wildcards
        ])

    async def _invoke_handler(
        self,
        handler: Callable,
        event_name: str,
        payload: dict,
        source: str,
        event_id: str,
        workspace_id: Optional[str] = None,
        brand_id: Optional[str] = None,
        decision_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        parent_event_id: Optional[str] = None,
        provenance: VerifiedEventProvenance | None = None,
    ) -> None:
        """Invoke handler with full event context and scoped correlation ID."""
        correlation_token = correlation_id_ctx.set(correlation_id or event_id)
        try:
            event_data = {
                "event_name": event_name,
                "payload": payload,
                "source": source,
                "event_id": event_id,
                "workspace_id": workspace_id,
                "brand_id": brand_id,
                "decision_id": decision_id,
                "correlation_id": correlation_id,
                "parent_event_id": parent_event_id,
                # Live process-local proof only; absent on legacy and replayed events.
                "provenance": provenance,
            }

            try:
                result = handler(event_data)
            except TypeError:
                result = handler(payload)

            if asyncio.iscoroutine(result):
                await result
            elif asyncio.isfuture(result):
                await result

        except Exception as exc:
            self._handler_errors += 1
            logger.error(
                "Handler %s failed for '%s': %s",
                getattr(handler, "__qualname__", str(handler)),
                event_name,
                exc,
                exc_info=True,
            )
            self._capture_dead_letter(
                event_id, event_name, payload, exc,
                getattr(handler, "__qualname__", str(handler)),
            )
        finally:
            correlation_id_ctx.reset(correlation_token)

    async def replay_dead_letter(
        self,
        event_id: str,
        handler_name: Optional[str] = None,
    ) -> int:
        """Retry matching in-memory dead letters and remove successful retries."""
        candidates = [
            letter for letter in list(self._dead_letters)
            if letter.event_id == event_id
            and (handler_name is None or letter.handler_name == handler_name)
        ]
        replayed = 0
        for letter in candidates:
            async with self._lock:
                registered_handlers = (
                    list(self._subscribers.get(letter.event_name, []))
                    + list(self._wildcard)
                )
            handler = next(
                (
                    candidate for candidate in registered_handlers
                    if getattr(candidate, "__qualname__", str(candidate))
                    == letter.handler_name
                ),
                None,
            )
            if handler is None:
                continue
            failures_before = self._handler_errors
            await self._invoke_handler(
                handler,
                letter.event_name,
                letter.payload,
                "dead_letter_replay",
                letter.event_id,
            )
            if self._handler_errors == failures_before:
                try:
                    self._dead_letters.remove(letter)
                except ValueError:
                    pass
                replayed += 1
        return replayed

    # ═══════════════════════════════════════════════════════════════════
    # PERSISTENCE
    # ═══════════════════════════════════════════════════════════════════

    async def _persistence_loop(self) -> None:
        while self._running:
            batch: List[tuple[EventStoreEntry, asyncio.Future]] = []
            try:
                try:
                    item = await asyncio.wait_for(
                        self._persistence_queue.get(),
                        timeout=self.config.persistence_flush_interval,
                    )
                    batch.append(item)
                    for _ in range(self.config.persistence_batch_size - 1):
                        try:
                            batch.append(self._persistence_queue.get_nowait())
                        except asyncio.QueueEmpty:
                            break
                except asyncio.TimeoutError:
                    continue

                attempt = 0
                entries = [entry for entry, _future in batch]
                while self._running:
                    if await self._persist_batch(entries):
                        for _entry, future in batch:
                            if not future.done():
                                future.set_result(True)
                            self._persistence_queue.task_done()
                        break
                    attempt += 1
                    await asyncio.sleep(min(30.0, max(1.0, 2 ** min(attempt, 5))))
                else:
                    for item in batch:
                        await self._persistence_queue.put(item)
                        self._persistence_queue.task_done()

            except asyncio.CancelledError:
                # Return in-flight work for the orderly shutdown flush.
                for item in batch:
                    await self._persistence_queue.put(item)
                    self._persistence_queue.task_done()
                break
            except Exception as exc:
                logger.error("Persistence loop error: %s", exc)

    async def _persist_batch(self, batch: List[EventStoreEntry]) -> bool:
        if not self.supabase:
            return True

        if self._circuit_state == CircuitState.OPEN:
            if not self._should_retry_circuit():
                return False
            self._circuit_state = CircuitState.HALF_OPEN

        try:
            records = [
                {
                    "id": entry.event_id,
                    "event_type": entry.event_name,
                    "payload": entry.payload,
                    "workspace_id": entry.workspace_id,
                    "correlation_id": entry.correlation_id,
                    "decision_id": entry.decision_id,
                    "brand_id": entry.brand_id,
                    "parent_event_id": entry.parent_event_id,
                    "source_service": entry.source_engine,
                    "source_instance": get_process_instance_id(),
                    "processed": False,
                    "schema_version": entry.schema_version,
                }
                for entry in batch
            ]

            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.supabase.table("events").upsert(records, on_conflict="id").execute(),
            )

            self._circuit_failures = 0
            self._circuit_state = CircuitState.CLOSED
            return True

        except Exception as exc:
            self._persistence_failures += len(batch)
            self._circuit_failures += 1
            self._circuit_last_failure_time = time.monotonic()

            if self._circuit_failures >= self.config.circuit_failure_threshold:
                self._circuit_state = CircuitState.OPEN
                logger.warning("Persistence circuit breaker opened")

            logger.warning("Persistence failed; batch retained for retry: %s", str(exc)[:200])
            return False

    def _should_retry_circuit(self) -> bool:
        if self._circuit_last_failure_time is None:
            return True
        return (
            time.monotonic() - self._circuit_last_failure_time
            >= self.config.circuit_recovery_timeout
        )

    async def _flush_persistence(self) -> None:
        batch: List[tuple[EventStoreEntry, asyncio.Future]] = []
        while not self._persistence_queue.empty():
            try:
                batch.append(self._persistence_queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        if batch:
            entries = [entry for entry, _future in batch]
            persisted = await self._persist_batch(entries)
            if persisted:
                for _entry, future in batch:
                    if not future.done():
                        future.set_result(True)
                    self._persistence_queue.task_done()
            else:
                # Shutdown cannot claim success when the durable write failed.
                for item in batch:
                    entry, future = item
                    await self._persistence_queue.put(item)
                    self._persistence_queue.task_done()
                    if not future.done():
                        future.set_exception(
                            RuntimeError(
                                f"Event {entry.event_id} remains queued; durable persistence failed during shutdown"
                            )
                        )

    # ═══════════════════════════════════════════════════════════════════
    # DEAD LETTER QUEUE
    # ═══════════════════════════════════════════════════════════════════

    def _capture_dead_letter(
        self, event_id, event_name, payload, error, handler_name,
    ) -> None:
        existing = None
        for dl in self._dead_letters:
            if dl.event_id == event_id and dl.handler_name == handler_name:
                existing = dl
                break

        if existing:
            existing.attempt_count += 1
            existing.last_failure = datetime.now(timezone.utc)
            existing.error = str(error)
        else:
            self._dead_letters.append(
                DeadLetter(
                    event_id=event_id,
                    event_name=event_name,
                    payload=payload,
                    error=str(error),
                    handler_name=handler_name,
                )
            )

    def get_dead_letters(self, limit: int = 50) -> List[DeadLetter]:
        return list(self._dead_letters)[-limit:]

    # ═══════════════════════════════════════════════════════════════════
    # EVENT REPLAY
    # ═══════════════════════════════════════════════════════════════════════

    def replay_events(
        self, from_sequence: int = 0,
        event_names: Optional[List[str]] = None,
        limit: int = 100,
    ) -> List[EventStoreEntry]:
        events = list(self._event_store)
        events = [e for e in events if e.sequence_number >= from_sequence]
        if event_names:
            events = [e for e in events if e.event_name in event_names]
        return events[-limit:]

    # ═══════════════════════════════════════════════════════════════════
    # CORRELATION TRACKING
    # ═══════════════════════════════════════════════════════════════════

    def _track_correlation(self, correlation_id: str, event_id: str) -> None:
        if correlation_id not in self._active_correlations:
            self._active_correlations[correlation_id] = {
                "events": [],
                "created_at": time.monotonic(),
            }
        self._active_correlations[correlation_id]["events"].append(event_id)

    async def _correlation_cleanup_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self.config.correlation_ttl_seconds // 2)
                now = time.monotonic()
                expired = [
                    cid for cid, data in self._active_correlations.items()
                    if now - data["created_at"] > self.config.correlation_ttl_seconds
                ]
                for cid in expired:
                    del self._active_correlations[cid]
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Correlation cleanup error: %s", exc)

    # ═══════════════════════════════════════════════════════════════════
    # DIAGNOSTICS
    # ═══════════════════════════════════════════════════════════════════

    def get_stats(self) -> Dict[str, Any]:
        return {
            "running": self._running,
            "events_published": self._events_published,
            "events_dropped": self._events_dropped,
            "handler_errors": self._handler_errors,
            "persistence_failures": self._persistence_failures,
            "subscriber_count": sum(len(h) for h in self._subscribers.values()) + len(self._wildcard),
            "event_types_tracked": len(self._subscribers),
            "wildcard_subscribers": len(self._wildcard),
            "dispatch_queue_size": self._dispatch_queue.qsize(),
            "persistence_queue_size": self._persistence_queue.qsize(),
            "dead_letters": len(self._dead_letters),
            "event_store_size": len(self._event_store),
            "sequence_number": self._sequence_number,
            "throttled_count": self._throttled_count,
            "active_correlations": len(self._active_correlations),
            "circuit_state": self._circuit_state.value,
            "uptime_seconds": (
                (datetime.now(timezone.utc) - self._started_at).total_seconds()
                if self._started_at else 0
            ),
        }


    def get_correlation_timeline(self, correlation_id: str, limit: int = 200) -> list:
        """Sprint 2: ordered events for one correlation_id."""
        if not correlation_id:
            return []
        rows = []
        for entry in list(self._event_store):
            payload = getattr(entry, "payload", None) or {}
            nerve = payload.get("_nerve") if isinstance(payload, dict) else None
            cid = None
            if isinstance(payload, dict):
                cid = payload.get("correlation_id")
                if not cid and isinstance(nerve, dict):
                    cid = nerve.get("correlation_id")
            if cid != correlation_id:
                # also match active correlation index
                continue
            rows.append({
                "event_id": getattr(entry, "event_id", None),
                "event_name": getattr(entry, "event_name", None),
                "published_at": getattr(entry, "published_at", None).isoformat()
                    if getattr(entry, "published_at", None) else None,
                "source": getattr(entry, "source", None) or (payload.get("source") if isinstance(payload, dict) else None),
                "payload": payload if isinstance(payload, dict) else {"raw": str(payload)[:500]},
                "sequence": getattr(entry, "sequence", None),
            })
        rows.sort(key=lambda r: (r.get("published_at") or "", r.get("sequence") or 0))
        return rows[-limit:]

    def get_decision_timeline(self, decision_id: str, limit: int = 200) -> list:
        if not decision_id:
            return []
        rows = []
        for entry in list(self._event_store):
            payload = getattr(entry, "payload", None) or {}
            if not isinstance(payload, dict):
                continue
            nerve = payload.get("_nerve") if isinstance(payload.get("_nerve"), dict) else {}
            did = payload.get("decision_id") or nerve.get("decision_id")
            if did != decision_id:
                continue
            rows.append({
                "event_id": getattr(entry, "event_id", None),
                "event_name": getattr(entry, "event_name", None),
                "published_at": getattr(entry, "published_at", None).isoformat()
                    if getattr(entry, "published_at", None) else None,
                "payload": payload,
            })
        rows.sort(key=lambda r: r.get("published_at") or "")
        return rows[-limit:]

    def health_check(self) -> Dict[str, Any]:
        stats = self.get_stats()
        status = "healthy"
        if not stats["running"]:
            status = "stopped"
        elif stats["handler_errors"] > stats["events_published"] * 0.1:
            status = "degraded"
        elif stats["circuit_state"] == "open":
            status = "degraded"
        elif stats["events_published"] == 0:
            status = "idle"
        return {"status": status, "stats": stats}


# ═══════════════════════════════════════════════════════════════════════════
# MODULE SINGLETON
# ═══════════════════════════════════════════════════════════════════════════

_bus: Optional[UnifiedEventBus] = None
_bus_lock = None


async def get_unified_bus(
    supabase_client: Any = None,
    config: EventBusConfig | None = None,
) -> UnifiedEventBus:
    global _bus, _bus_lock

    if _bus_lock is None:
        _bus_lock = asyncio.Lock()

    async with _bus_lock:
        if _bus is None:
            _bus = UnifiedEventBus(supabase_client, config)
            await _bus.start()

    return _bus


async def shutdown_unified_bus() -> None:
    global _bus
    if _bus is not None:
        await _bus.stop()
        _bus = None


__all__ = [
    "UnifiedEventBus",
    "EventBusConfig",
    "EventMetadata",
    "DeadLetter",
    "EventStoreEntry",
    "CircuitState",
    "CircuitBreakerOpenError",
    "ThrottlePolicy",
    "get_unified_bus",
    "shutdown_unified_bus",
]


def get_bus_sync() -> Optional[UnifiedEventBus]:
    """Phase E — sync accessor for dashboard routes."""
    return _bus


def set_bus_instance(bus: UnifiedEventBus) -> None:
    """Called by lifecycle after UnifiedEventBus starts."""
    global _bus
    _bus = bus
