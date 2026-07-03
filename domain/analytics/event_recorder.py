from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.events import (
    DomainEvent,
    EventType,
    PostPublishedEvent,
    PostGeneratedEvent,
    ScanCompletedEvent,
)
from core.event_bus import EventBus

if TYPE_CHECKING:
    from repositories.analytics_repository import AnalyticsRepository

logger = logging.getLogger(__name__)


class EventRecorder:
    """Subscribes to the EventBus and persists analytics events via the
    AnalyticsRepository. Decouples the bus from the storage layer."""

    def __init__(self, repo: "AnalyticsRepository", bus: EventBus) -> None:
        self._repo = repo
        self._bus = bus

    def start(self) -> None:
        self._bus.subscribe(EventType.POST_PUBLISHED, self._on_post_published)
        self._bus.subscribe(EventType.POST_GENERATED, self._on_post_generated)
        self._bus.subscribe(EventType.SCAN_COMPLETED, self._on_scan_completed)
        self._bus.subscribe(EventType.MESSAGE_RECEIVED, self._on_message)
        self._bus.subscribe(EventType.MESSAGE_SENT, self._on_message)
        logger.info("EventRecorder: subscribed to bus")

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
