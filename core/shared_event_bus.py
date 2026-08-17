"""
SharedEventBus — Cross-instance communication via Supabase.

v1.0 — Connects all Kanyoza instances into an autonomous system.
Each instance publishes events that other instances can subscribe to.

Events:
  - trend_detected      → Director tells Scheduler to post
  - post_published       → Scheduler tells ConditionEngine to analyze
  - content_failed       → Scheduler tells Director about failure
  - strategy_change      → ConditionEngine tells Director to adjust
  - engine_alert         → MetaEngine tells Director about issues
  - publish_request      → Director tells Scheduler to publish now
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("kanyoza.shared_event_bus")


class SharedEventBus:
    """Cross-instance event bus using Supabase as transport."""

    POLL_INTERVAL_SECONDS = 15  # Check for new events every 15 seconds
    MAX_EVENTS_PER_POLL = 50    # Max events to process per poll
    EVENT_RETENTION_HOURS = 24  # Keep events for 24 hours

    def __init__(
        self,
        supabase_client: Any = None,
        source_service: str = "unknown",
        poll_interval: int = POLL_INTERVAL_SECONDS,
    ):
        self.supabase = supabase_client
        self.source_service = source_service
        self.poll_interval = poll_interval

        self._handlers: Dict[str, List[Callable]] = {}
        self._lock = threading.RLock()
        self._poll_thread: Optional[threading.Thread] = None
        self._running = False
        self._last_poll = 0.0

        # Stats
        self._events_published = 0
        self._events_received = 0

    # ═══════════════════════════════════════════════════════════════
    # PUBLISH
    # ═══════════════════════════════════════════════════════════════

    def publish(
        self,
        event_name: str,
        payload: Dict[str, Any] = None,
    ) -> bool:
        """Publish an event for other instances to receive."""
        if not self.supabase:
            logger.debug("SharedEventBus: no Supabase client — publish skipped")
            return False

        try:
            result = self.supabase.table("shared_event_bus").insert({
                "event_name": event_name,
                "payload": json.dumps(payload or {}),
                "source_service": self.source_service,
            }).execute()

            self._events_published += 1
            return True

        except Exception as exc:
            logger.error("SharedEventBus publish failed [%s]: %s", event_name, exc)
            return False

    # ═══════════════════════════════════════════════════════════════
    # SUBSCRIBE
    # ═══════════════════════════════════════════════════════════════

    def subscribe(
        self,
        event_name: str,
        handler: Callable[[Dict[str, Any]], None],
    ) -> None:
        """Register a handler for a specific event."""
        with self._lock:
            if event_name not in self._handlers:
                self._handlers[event_name] = []
            self._handlers[event_name].append(handler)
            logger.info(
                "SharedEventBus: subscribed to '%s' (%d handlers)",
                event_name,
                len(self._handlers[event_name]),
            )

    def unsubscribe(
        self,
        event_name: str,
        handler: Callable[[Dict[str, Any]], None],
    ) -> None:
        """Remove a handler."""
        with self._lock:
            if event_name in self._handlers:
                self._handlers[event_name] = [
                    h for h in self._handlers[event_name] if h != handler
                ]

    # ═══════════════════════════════════════════════════════════════
    # POLLING LOOP
    # ═══════════════════════════════════════════════════════════════

    def start(self) -> None:
        """Start the background polling thread."""
        if not self.supabase:
            logger.warning("SharedEventBus: no Supabase — polling disabled")
            return

        if self._running:
            return

        self._running = True
        self._poll_thread = threading.Thread(
            target=self._polling_loop,
            name=f"event-bus-{self.source_service}",
            daemon=True,
        )
        self._poll_thread.start()
        logger.info("SharedEventBus: polling started for '%s'", self.source_service)

    def stop(self) -> None:
        """Stop the polling thread."""
        self._running = False
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=3)
        self._poll_thread = None
        logger.info("SharedEventBus: polling stopped")

    def _polling_loop(self) -> None:
        """Background loop that polls for new events."""
        while self._running:
            try:
                self.poll()
            except Exception:
                logger.exception("SharedEventBus polling error")
            time.sleep(self.poll_interval)

    def poll(self) -> int:
        """Poll for new events and dispatch to handlers."""
        if not self.supabase:
            return 0

        try:
            # Get unprocessed events
            result = self.supabase.table("shared_event_bus") \
                .select("*") \
                .eq("processed", False) \
                .order("created_at") \
                .limit(self.MAX_EVENTS_PER_POLL) \
                .execute()

            events = result.data or []

            for event in events:
                self._dispatch_event(event)

            return len(events)

        except Exception as exc:
            logger.error("SharedEventBus poll failed: %s", exc)
            return 0

    def _dispatch_event(self, event: Dict[str, Any]) -> None:
        """Dispatch a single event to registered handlers."""
        event_name = event.get("event_name", "")
        event_id = event.get("id")
        source = event.get("source_service", "unknown")

        # Skip events from ourselves
        if source == self.source_service:
            self._mark_processed(event_id)
            return

        try:
            payload = json.loads(event.get("payload", "{}"))
        except json.JSONDecodeError:
            payload = {}

        handlers = self._handlers.get(event_name, [])

        if not handlers:
            # No handlers — mark as processed anyway
            self._mark_processed(event_id)
            return

        for handler in handlers:
            try:
                handler(payload)
                self._events_received += 1
            except Exception as exc:
                logger.error(
                    "SharedEventBus handler failed for '%s': %s",
                    event_name,
                    exc,
                )

        self._mark_processed(event_id)

    def _mark_processed(self, event_id: Any) -> None:
        """Mark an event as processed."""
        try:
            self.supabase.table("shared_event_bus") \
                .update({
                    "processed": True,
                    "processed_at": datetime.now(timezone.utc).isoformat(),
                }) \
                .eq("id", event_id) \
                .execute()
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════════
    # CLEANUP
    # ═══════════════════════════════════════════════════════════════

    def cleanup_old_events(self) -> int:
        """Delete processed events older than retention period."""
        try:
            cutoff = datetime.now(timezone.utc).isoformat()
            result = self.supabase.table("shared_event_bus") \
                .delete() \
                .eq("processed", True) \
                .lt("created_at", cutoff) \
                .execute()
            return len(result.data or [])
        except Exception:
            return 0

    # ═══════════════════════════════════════════════════════════════
    # STATS
    # ═══════════════════════════════════════════════════════════════

    def get_stats(self) -> Dict[str, Any]:
        return {
            "service": self.source_service,
            "running": self._running,
            "events_published": self._events_published,
            "events_received": self._events_received,
            "subscriptions": {
                event: len(handlers)
                for event, handlers in self._handlers.items()
            },
        }

    def is_running(self) -> bool:
        return self._running


# ═══════════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════════

_shared_bus: Optional[SharedEventBus] = None


def get_shared_event_bus() -> SharedEventBus:
    """Get the singleton SharedEventBus."""
    global _shared_bus
    if _shared_bus is None:
        _shared_bus = SharedEventBus(source_service="unknown")
    return _shared_bus


def init_shared_event_bus(
    supabase_client: Any = None,
    source_service: str = "unknown",
) -> SharedEventBus:
    """Initialize the singleton SharedEventBus."""
    global _shared_bus

    _shared_bus = SharedEventBus(
        supabase_client=supabase_client,
        source_service=source_service,
    )
    _shared_bus.start()
    return _shared_bus


__all__ = [
    "SharedEventBus",
    "get_shared_event_bus",
    "init_shared_event_bus",
]