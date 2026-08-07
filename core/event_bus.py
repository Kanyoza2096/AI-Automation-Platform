from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import Callable

from core.events import DomainEvent, EventType

logger = logging.getLogger(__name__)

Subscriber = Callable[[DomainEvent], None]


class EventBus:
    """Thread-safe in-process pub/sub event bus.

    Subscribers run synchronously in the publisher's thread by default.
    Use the worker pool for async subscribers.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[EventType, list[Subscriber]] = defaultdict(list)
        self._wildcard: list[Subscriber] = []

    def subscribe(self, event_type: EventType, handler: Subscriber) -> None:
        with self._lock:
            self._subscribers[event_type].append(handler)
        logger.debug("EventBus: subscribed %s → %s", event_type.value, handler.__qualname__)

    def subscribe_all(self, handler: Subscriber) -> None:
        """Receive every event regardless of type."""
        with self._lock:
            self._wildcard.append(handler)

    def unsubscribe(self, event_type: EventType, handler: Subscriber) -> None:
        with self._lock:
            self._subscribers[event_type] = [
                h for h in self._subscribers[event_type] if h is not handler
            ]

    def publish(self, event: DomainEvent) -> None:
        with self._lock:
            handlers = list(self._subscribers.get(event.event_type, []))
            wildcards = list(self._wildcard)

        for handler in handlers + wildcards:
            try:
                handler(event)
            except Exception as exc:
                logger.error(
                    "EventBus: handler %s raised %s for %s",
                    handler.__qualname__,
                    exc,
                    event,
                    exc_info=True,
                )

    def subscriber_count(self, event_type: EventType) -> int:
        with self._lock:
            return len(self._subscribers.get(event_type, []))


_bus: EventBus | None = None
_bus_lock = threading.Lock()


def get_event_bus() -> EventBus:
    global _bus
    if _bus is None:
        with _bus_lock:
            if _bus is None:
                _bus = EventBus()
    return _bus
