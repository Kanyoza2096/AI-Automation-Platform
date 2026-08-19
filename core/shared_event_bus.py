"""
SharedEventBus — Cross-instance communication via Supabase.

v2.0 — UNIFIED EVENT SYSTEM
Uses single `events` table for both local and cross-instance events.

Table Schema:
    events (
        id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
        event_name TEXT NOT NULL,
        payload JSONB DEFAULT '{}'::jsonb,
        source_service TEXT DEFAULT 'unknown',
        processed BOOLEAN DEFAULT FALSE,
        processed_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ DEFAULT NOW()
    )
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

    POLL_INTERVAL_SECONDS = 15
    MAX_EVENTS_PER_POLL = 50

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

        # Stats
        self._events_published = 0
        self._events_received = 0

    # ═══════════════════════════════════════════════════════════════
    # PUBLISH (v2.0 — Fixed to match `events` table)
    # ═══════════════════════════════════════════════════════════════

    def publish(
        self,
        event_name: str,
        payload: Dict[str, Any] = None,
    ) -> bool:
        """Publish an event for other instances to receive."""
        if not self.supabase:
            logger.debug("SharedEventBus: no Supabase — publish skipped")
            return False

        try:
            # v2.0 FIX: Only send existing columns, payload as dict (not string)
            self.supabase.table("events").insert({
                "event_name": event_name,
                "payload": payload or {},       # ✅ Direct dict for JSONB
                "source_service": self.source_service,
                # id, processed, created_at → use table defaults
            }).execute()

            self._events_published += 1
            return True

        except Exception as exc:
            logger.error("SharedEventBus publish failed [%s]: %s", event_name, exc)
            return False

    # ═══════════════════════════════════════════════════════════════
    # SUBSCRIBE (unchanged)
    # ═══════════════════════════════════════════════════════════════

    def subscribe(self, event_name: str, handler: Callable) -> None:
        with self._lock:
            if event_name not in self._handlers:
                self._handlers[event_name] = []
            self._handlers[event_name].append(handler)
            logger.info(
                "SharedEventBus: subscribed to '%s' (%d handlers)",
                event_name, len(self._handlers[event_name]),
            )

    def unsubscribe(self, event_name: str, handler: Callable) -> None:
        with self._lock:
            if event_name in self._handlers:
                self._handlers[event_name] = [
                    h for h in self._handlers[event_name] if h != handler
                ]

    # ═══════════════════════════════════════════════════════════════
    # POLLING (unchanged logic, updated table name)
    # ═══════════════════════════════════════════════════════════════

    def start(self) -> None:
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
        self._running = False
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=3)
        self._poll_thread = None
        logger.info("SharedEventBus: polling stopped")

    def _polling_loop(self) -> None:
        while self._running:
            try:
                self.poll()
            except Exception:
                logger.exception("SharedEventBus polling error")
            time.sleep(self.poll_interval)

    def poll(self) -> int:
        if not self.supabase:
            return 0

        try:
            result = self.supabase.table("events") \
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
        event_name = event.get("event_name", "")
        event_id = event.get("id")
        source = event.get("source_service", "unknown")

        # Skip events from ourselves
        if source == self.source_service:
            self._mark_processed(event_id)
            return

        try:
            # v2.0 FIX: payload is already JSONB dict (not string)
            payload = event.get("payload", {})
            if isinstance(payload, str):
                payload = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            payload = {}

        handlers = self._handlers.get(event_name, [])

        if not handlers:
            self._mark_processed(event_id)
            return

        for handler in handlers:
            try:
                handler(payload)
                self._events_received += 1
            except Exception as exc:
                logger.error(
                    "SharedEventBus handler failed for '%s': %s",
                    event_name, exc,
                )

        self._mark_processed(event_id)

    def _mark_processed(self, event_id: Any) -> None:
        try:
            self.supabase.table("events") \
                .update({
                    "processed": True,
                    "processed_at": datetime.now(timezone.utc).isoformat(),
                }) \
                .eq("id", event_id) \
                .execute()
        except Exception:
            pass

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


# ═══════════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════════

_shared_bus: Optional[SharedEventBus] = None


def get_shared_event_bus() -> SharedEventBus:
    global _shared_bus
    if _shared_bus is None:
        _shared_bus = SharedEventBus(source_service="unknown")
    return _shared_bus


def init_shared_event_bus(
    supabase_client: Any = None,
    source_service: str = "unknown",
) -> SharedEventBus:
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