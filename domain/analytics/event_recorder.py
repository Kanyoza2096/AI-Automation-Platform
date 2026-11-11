from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

from core.events import DomainEvent, EventType
from core.event_bus import EventBus

if TYPE_CHECKING:
    from repositories.analytics_repository import AnalyticsRepository

logger = logging.getLogger(__name__)


class EventRecorder:
    """Persist selected domain events through the lifecycle-owned event bridge.

    The recorder deliberately accepts the synchronous DomainEvent-compatible
    EventBus interface. Dependency injection supplies EventBusBridge, which
    forwards to the one lifecycle-owned UnifiedEventBus instance.
    """

    def __init__(self, repo: "AnalyticsRepository", bus: EventBus) -> None:
        self._repo = repo
        self._bus = bus
        self._unsubscribe_callbacks: list[Callable[[], None]] = []
        self._started = False

    @property
    def is_started(self) -> bool:
        return self._started

    def start(self) -> None:
        """Install all subscriptions once, rolling back partial registration."""
        if self._started:
            return

        subscriptions = (
            (EventType.POST_PUBLISHED, self._on_post_published),
            (EventType.POST_GENERATED, self._on_post_generated),
            (EventType.SCAN_COMPLETED, self._on_scan_completed),
            (EventType.MESSAGE_RECEIVED, self._on_message),
            (EventType.MESSAGE_SENT, self._on_message),
        )
        registered: list[Callable[[], None]] = []
        try:
            for event_type, handler in subscriptions:
                unsubscribe = self._bus.subscribe(event_type, handler)
                if not callable(unsubscribe):
                    raise RuntimeError(
                        f"Event bus did not return an unsubscribe callback for {event_type.value}"
                    )
                registered.append(unsubscribe)
        except Exception:
            self._unsubscribe_all(registered)
            raise

        self._unsubscribe_callbacks = registered
        self._started = True
        logger.info("EventRecorder: subscribed to %d event types", len(registered))

    def stop(self) -> None:
        """Remove this recorder's subscriptions; safe to call repeatedly."""
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
                # Continue cleanup so one faulty handler cannot leak the rest.
                logger.exception("EventRecorder: failed to remove event subscription")

    def _on_post_published(self, event: DomainEvent) -> None:
        p = event.payload
        self._repo.record_post_published(
            topic=p.get("topic", ""),
            platform=p.get("platform", ""),
            post_id=p.get("post_id", ""),
        )

    def _on_post_generated(self, event: DomainEvent) -> None:
        self._repo.save({
            "event": "post_generated",
            "topic": event.payload.get("topic", ""),
            "theme": event.payload.get("theme", ""),
            "provider": event.payload.get("provider", ""),
            "latency_ms": event.payload.get("latency_ms", 0),
        })

    def _on_scan_completed(self, event: DomainEvent) -> None:
        self._repo.save({
            "event": "scan_completed",
            **event.payload,
        })

    def _on_message(self, event: DomainEvent) -> None:
        direction = "inbound" if event.event_type == EventType.MESSAGE_RECEIVED else "outbound"
        self._repo.record_message(
            sender_id=event.payload.get("sender_id", ""),
            direction=direction,
        )
