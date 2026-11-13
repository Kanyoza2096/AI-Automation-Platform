from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Callable

from core.events import DomainEvent, EventType
from core.unified_bus import UnifiedEventBus

if TYPE_CHECKING:
    from repositories.analytics_repository import AnalyticsRepository

logger = logging.getLogger(__name__)


class EventRecorder:
    """Persist selected domain events from the authoritative async event bus."""

    def __init__(self, repo: "AnalyticsRepository", bus: UnifiedEventBus) -> None:
        self._repo = repo
        self._bus = bus
        self._unsubscribe_callbacks: list[Callable[[], None]] = []
        self._started = False
        self._lifecycle_lock = asyncio.Lock()

    @property
    def is_started(self) -> bool:
        return self._started

    def health_check(self) -> dict:
        """Report subscription and analytics-storage readiness without writing data."""
        expected_subscriptions = 4
        active_subscriptions = len(self._unsubscribe_callbacks)
        issues = []

        if not self._started or active_subscriptions != expected_subscriptions:
            issues.append("event_subscriptions_incomplete")

        storage_probe = getattr(self._repo, "check_storage", None)
        storage_readable = False
        if callable(storage_probe):
            try:
                storage_readable = storage_probe() is True
            except Exception:
                logger.warning("EventRecorder storage health probe failed")
        if not storage_readable:
            issues.append("analytics_storage_unavailable")

        return {
            "status": "healthy" if not issues else "degraded",
            "metrics": {
                "started": self._started,
                "active_subscriptions": active_subscriptions,
                "expected_subscriptions": expected_subscriptions,
                "storage_readable": storage_readable,
            },
            "issues": [{"type": issue} for issue in issues],
        }

    # Both names are supported by the platform's health-provider adapters.
    def check_health(self) -> dict:
        return self.health_check()

    async def start(self) -> None:
        """Await all subscriptions and roll back if any registration fails."""
        async with self._lifecycle_lock:
            if self._started:
                return

            event_subscriptions = (
                (EventType.POST_GENERATED.value, EventType.POST_GENERATED),
                (EventType.SCAN_COMPLETED.value, EventType.SCAN_COMPLETED),
                (EventType.MESSAGE_RECEIVED.value, EventType.MESSAGE_RECEIVED),
                (EventType.MESSAGE_SENT.value, EventType.MESSAGE_SENT),
            )
            registered: list[Callable[[], None]] = []
            try:
                for event_name, event_type in event_subscriptions:
                    unsubscribe = await self._bus.subscribe(
                        event_name,
                        self._make_handler(event_type),
                    )
                    if not callable(unsubscribe):
                        raise RuntimeError(
                            f"UnifiedEventBus did not return an unsubscribe callback for {event_type.value}"
                        )
                    registered.append(unsubscribe)
            except Exception:
                self._unsubscribe_all(registered)
                raise

            self._unsubscribe_callbacks = registered
            self._started = True
            logger.info("EventRecorder: subscribed to %d non-publish analytics event types", len(registered))

    async def stop(self) -> None:
        """Remove this recorder's subscriptions; safe to call repeatedly."""
        async with self._lifecycle_lock:
            callbacks, self._unsubscribe_callbacks = self._unsubscribe_callbacks, []
            self._started = False
            self._unsubscribe_all(callbacks)
            if callbacks:
                logger.info("EventRecorder: unsubscribed from %d event types", len(callbacks))

    @staticmethod
    def _unsubscribe_all(callbacks: list[Callable[[], None]]) -> None:
        for unsubscribe in reversed(callbacks):
            try:
                unsubscribe()
            except Exception:
                logger.exception("EventRecorder: failed to remove event subscription")

    def _make_handler(self, event_type: EventType):
        async def handle(event_data, **_metadata) -> None:
            # UnifiedEventBus invokes handlers with an envelope containing
            # event metadata and the original domain payload.
            if isinstance(event_data, dict) and "payload" in event_data:
                payload = event_data.get("payload")
                source = event_data.get("source", "unified")
                event_id = event_data.get("event_id")
            else:
                payload = event_data
                source = "unified"
                event_id = None
            event_kwargs = {}
            if event_id:
                event_kwargs["event_id"] = event_id
            event = DomainEvent(
                event_type=event_type,
                payload=payload if isinstance(payload, dict) else {},
                source=source or "unified",
                **event_kwargs,
            )
            # Repository methods are synchronous and may perform blocking DB I/O.
            # Never run them on the UnifiedEventBus dispatcher event loop.
            if event_type == EventType.POST_GENERATED:
                await asyncio.to_thread(self._handle_post_generated_from, event)
            elif event_type == EventType.SCAN_COMPLETED:
                await asyncio.to_thread(self._handle_scan_completed_from, event)
            else:
                await asyncio.to_thread(self._handle_message_from, event)

        return handle

    def _on_post_published(self, event: DomainEvent) -> None:
        self._handle_post_published_from(event)

    def _on_post_generated(self, event: DomainEvent) -> None:
        self._handle_post_generated_from(event)

    def _on_scan_completed(self, event: DomainEvent) -> None:
        self._handle_scan_completed_from(event)

    def _on_message(self, event: DomainEvent) -> None:
        self._handle_message_from(event)

    def _handle_post_published_from(self, event: DomainEvent) -> None:
        p = event.payload
        self._repo.record_post_published(
            topic=p.get("topic", ""),
            platform=p.get("platform", ""),
            post_id=p.get("post_id", ""),
        )

    def _handle_post_generated_from(self, event: DomainEvent) -> None:
        self._repo.save({
            "event": "post_generated",
            "topic": event.payload.get("topic", ""),
            "theme": event.payload.get("theme", ""),
            "provider": event.payload.get("provider", ""),
            "latency_ms": event.payload.get("latency_ms", 0),
        })

    def _handle_scan_completed_from(self, event: DomainEvent) -> None:
        self._repo.save({
            "event": "scan_completed",
            **event.payload,
        })

    def _handle_message_from(self, event: DomainEvent) -> None:
        direction = "inbound" if event.event_type == EventType.MESSAGE_RECEIVED else "outbound"
        self._repo.record_message(
            sender_id=event.payload.get("sender_id", ""),
            direction=direction,
        )
