"""
SharedEventBus — Cross-instance communication via Supabase.

PHASE 1: This bus is ONLY for multi-process SERVICE_MODE workers
(worker.py). In-process API traffic uses UnifiedEventBus via core.event_bus bridge.
Do not use SharedEventBus inside the FastAPI process for domain events.

v2.1 — UNIFIED EVENT SYSTEM with Sync/Async Bridge
Uses single `events` table for both local and cross-instance events.

Fixes in v2.1:
  - Added sync/async publish compatibility
  - Added get_event_bus() for unified API
  - Added health_check()
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("kanyoza.shared_event_bus")


class SharedEventBus:
    """Cross-instance event bus using Supabase as transport."""

    POLL_INTERVAL_SECONDS = 15
    MAX_EVENTS_PER_POLL = 50

    def __init__(self, supabase_client: Any = None, source_service: str = "unknown", poll_interval: int = POLL_INTERVAL_SECONDS):
        self.supabase = supabase_client
        self.source_service = source_service
        # Service mode identifies a role, not a process. A unique instance
        # identity prevents one worker from mistaking another worker in the
        # same service mode for its own event producer.
        self.instance_id = f"{source_service}:{uuid.uuid4().hex[:12]}"
        self.poll_interval = poll_interval

        self._handlers: Dict[str, List[Callable]] = {}
        self._lock = threading.RLock()
        self._poll_thread: Optional[threading.Thread] = None
        self._running = False

        self._events_published = 0
        self._events_received = 0

    @property
    def service_name(self) -> str:
        """Stable health-registry identity for this transport."""
        return "shared_event_bus"

    def publish(
        self,
        event_name: str,
        payload: Dict[str, Any] = None,
        *,
        workspace_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        decision_id: Optional[str] = None,
        brand_id: Optional[str] = None,
        parent_event_id: Optional[str] = None,
        schema_version: str = "1.0",
    ) -> bool:
        """Publish using the same nerve contract and persistence shape as UnifiedEventBus."""
        if not self.supabase:
            logger.debug("SharedEventBus: no Supabase — publish skipped")
            return False

        try:
            from core.nerve import validate_and_normalize

            event_id = uuid.uuid4().hex
            envelope = validate_and_normalize(
                event_name,
                payload or {},
                source=self.source_service,
                event_id=event_id,
                correlation_id=correlation_id,
                workspace_id=workspace_id,
                brand_id=brand_id,
                decision_id=decision_id,
                parent_event_id=parent_event_id,
                schema_version=schema_version,
                strict=False,
            )
            self.supabase.table("events").insert({
                "id": event_id,
                "event_type": envelope.event_name,
                "payload": envelope.to_payload(),
                "workspace_id": envelope.workspace_id,
                "correlation_id": envelope.correlation_id,
                "decision_id": envelope.decision_id,
                "brand_id": envelope.brand_id,
                "parent_event_id": envelope.parent_event_id,
                "source_service": envelope.source,
                "source_instance": self.instance_id,
                "processed": False,
                "schema_version": envelope.schema_version,
            }).execute()

            self._events_published += 1
            return True

        except Exception as exc:
            logger.error("SharedEventBus publish failed [%s]: %s", event_name, exc)
            return False

    async def publish_async(
        self,
        event_name: str,
        payload: Dict[str, Any] = None,
        **kwargs: Any,
    ) -> bool:
        """Publish an event (async wrapper)."""
        return await asyncio.get_event_loop().run_in_executor(
            None, lambda: self.publish(event_name, payload, **kwargs)
        )

    def subscribe(self, event_name: str, handler: Callable) -> Callable[[], None]:
        """Subscribe to event. Returns unsubscribe function."""
        with self._lock:
            if event_name not in self._handlers:
                self._handlers[event_name] = []
            self._handlers[event_name].append(handler)
            logger.info("SharedEventBus: subscribed to '%s' (%d handlers)", event_name, len(self._handlers[event_name]))

        def unsubscribe() -> None:
            with self._lock:
                self._handlers[event_name] = [
                    h for h in self._handlers[event_name] if h != handler
                ]

        return unsubscribe

    def unsubscribe(self, event_name: str, handler: Callable) -> None:
        with self._lock:
            if event_name in self._handlers:
                self._handlers[event_name] = [
                    h for h in self._handlers[event_name] if h != handler
                ]

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
        """Fan events into this consumer group, then claim individual deliveries.

        The legacy ``events.processed`` bit is a single global acknowledgement and
        cannot safely represent multiple independent consumers.  Each service now
        owns an ``event_deliveries`` row per event/consumer group.
        """
        if not self.supabase:
            return 0

        try:
            result = self.supabase.table("events") \
                .select("*") \
                .order("created_at") \
                .limit(self.MAX_EVENTS_PER_POLL) \
                .execute()

            delivered = 0
            for event in result.data or []:
                event_name = event.get("event_type") or event.get("event_name", "")
                with self._lock:
                    has_handler = bool(self._handlers.get(event_name))
                if not has_handler:
                    continue
                event_id = event.get("id")
                if not event_id:
                    continue
                # A producer instance should not synchronously consume its own
                # transport copy; local UnifiedEventBus already handled it.
                if event.get("source_instance") == self.instance_id:
                    continue
                if not self._ensure_delivery(event_id, event_name):
                    continue
                delivery = self._claim_delivery(event_id)
                if delivery:
                    delivery_id, claim_token = delivery
                    self._dispatch_event(
                        event,
                        delivery_id=delivery_id,
                        claim_token=claim_token,
                    )
                    delivered += 1
            return delivered

        except Exception as exc:
            logger.error("SharedEventBus poll failed: %s", exc)
            return 0

    def _ensure_delivery(self, event_id: str, event_name: str) -> bool:
        try:
            self.supabase.table("event_deliveries").insert({
                "event_id": event_id,
                "consumer_group": self.source_service,
                "event_type": event_name,
                "processed": False,
            }).execute()
            return True
        except Exception as exc:
            # Unique(event_id, consumer_group) makes this idempotent.  Supabase
            # clients differ in how they expose duplicate-conflict errors, so a
            # failed insert is followed by a lookup before treating it as fatal.
            try:
                existing = self.supabase.table("event_deliveries").select("id").eq(
                    "event_id", event_id
                ).eq("consumer_group", self.source_service).limit(1).execute()
                return bool(existing.data)
            except Exception:
                logger.warning("Unable to ensure event delivery %s: %s", event_id, exc)
                return False

    def _claim_delivery(self, event_id: str) -> Optional[tuple[str, str]]:
        """Claim one consumer delivery and return its fencing token.

        The token must accompany acknowledgement so a worker whose lease has
        expired cannot complete work claimed by a replacement worker.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        claim_token = f"{self.instance_id}:{uuid.uuid4().hex}"
        try:
            result = self.supabase.table("event_deliveries").update({
                "claimed_by": claim_token,
                "claimed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("event_id", event_id).eq("consumer_group", self.source_service).eq(
                "processed", False
            ).or_(f"claimed_at.is.null,claimed_at.lt.{cutoff}").select("id").execute()
            if result.data:
                return str(result.data[0].get("id")), claim_token
        except Exception as exc:
            logger.warning("SharedEventBus delivery claim failed for %s: %s", event_id, exc)
        return None

    def _dispatch_event(
        self,
        event: Dict[str, Any],
        *,
        delivery_id: Optional[str] = None,
        claim_token: Optional[str] = None,
    ) -> None:
        event_name = event.get("event_type") or event.get("event_name", "")
        event_id = event.get("id")
        source_instance = event.get("source_instance", "")

        if source_instance and source_instance == self.instance_id:
            return

        try:
            payload = event.get("payload", {})
            if isinstance(payload, str):
                payload = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            payload = {}

        handlers = self._handlers.get(event_name, [])

        if not handlers:
            return

        handler_failed = False
        for handler in handlers:
            try:
                result = handler(payload)
                if inspect.isawaitable(result):
                    # Polling runs in a dedicated thread. There is normally no
                    # running asyncio loop here, so create_task() would fail and
                    # the event would be acknowledged without executing. Run
                    # the coroutine to completion before acknowledging.
                    asyncio.run(result)
                self._events_received += 1
            except Exception as exc:
                handler_failed = True
                logger.error("SharedEventBus handler failed for '%s': %s", event_name, exc)

        if handler_failed:
            self._mark_delivery_processed(delivery_id, claim_token=claim_token, success=False, error="one or more handlers failed")
        else:
            self._mark_delivery_processed(delivery_id, claim_token=claim_token, success=True)

    def _claim_event(self, event_id: Any) -> bool:
        """Atomically claim an event so only one worker dispatches it."""
        if not event_id or not self.supabase:
            return False
        claim_token = f"{self.instance_id}:{uuid.uuid4().hex}"
        try:
            # Claims must be recoverable after a worker crash. A stale claim is
            # eligible for takeover after 5 minutes, matching the execution
            # lease used by durable workers.
            cutoff = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
            result = self.supabase.table("events").update({
                "claimed_by": claim_token,
                "claimed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", event_id).eq("processed", False).or_(
                f"claimed_at.is.null,claimed_at.lt.{cutoff}"
            ).select("id").execute()
            return bool(result.data)
        except Exception as exc:
            logger.warning("SharedEventBus claim failed for %s: %s", event_id, exc)
            return False

    def _mark_delivery_processed(
        self,
        delivery_id: Optional[str],
        *,
        claim_token: Optional[str],
        success: bool = True,
        error: str | None = None,
    ) -> bool:
        """Acknowledge only the currently owned delivery lease.

        Failed work remains unprocessed and retains its lease timestamp, making
        it retryable after lease expiry instead of silently dead-lettering it.
        """
        if not delivery_id or not claim_token or not self.supabase:
            return False
        try:
            update = {
                "processed": bool(success),
                "processed_at": datetime.now(timezone.utc).isoformat() if success else None,
                "processing_error": (error or "")[:500] if error else None,
            }
            if success:
                update.update({"claimed_by": None, "claimed_at": None})
            result = self.supabase.table("event_deliveries").update(update).eq(
                "id", delivery_id
            ).eq("consumer_group", self.source_service).eq(
                "claimed_by", claim_token
            ).eq("processed", False).select("id").execute()
            return bool(result.data)
        except Exception:
            logger.exception("SharedEventBus failed to acknowledge delivery %s", delivery_id)
            return False

    def _mark_processed(self, event_id: Any, success: bool = True, error: str | None = None) -> None:
        try:
            self.supabase.table("events") \
                .update({
                    "processed": True,
                    "processed_at": datetime.now(timezone.utc).isoformat(),
                    "processing_error": (error or "")[:500] if error else None,
                }) \
                .eq("id", event_id) \
                .execute()
        except Exception:
            logger.exception("SharedEventBus failed to mark event %s processed", event_id)

    def get_stats(self) -> Dict[str, Any]:
        return {
            "service": self.source_service,
            "running": self._running,
            "events_published": self._events_published,
            "events_received": self._events_received,
            "subscriptions": {event: len(handlers) for event, handlers in self._handlers.items()},
        }

    def health_check(self) -> dict:
        stats = self.get_stats()
        status = "healthy" if self.supabase else "degraded"
        return {"status": status, "stats": stats}


_shared_bus: Optional[SharedEventBus] = None


def get_shared_event_bus() -> SharedEventBus:
    global _shared_bus
    if _shared_bus is None:
        _shared_bus = SharedEventBus(source_service="unknown")
    return _shared_bus


def init_shared_event_bus(supabase_client: Any = None, source_service: str = "unknown") -> SharedEventBus:
    global _shared_bus
    _shared_bus = SharedEventBus(supabase_client=supabase_client, source_service=source_service)
    _shared_bus.start()
    return _shared_bus


__all__ = [
    "SharedEventBus",
    "get_shared_event_bus",
    "init_shared_event_bus",
]
