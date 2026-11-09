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
from types import MappingProxyType
from typing import Any, Callable, Dict, List, Optional

from utils.ids import get_process_instance_id
from core.event_provenance import (
    ProducerCapability, ProducerRegistry, VerifiedEventProvenance,
    current_actor_principal,
)

logger = logging.getLogger("kanyoza.shared_event_bus")


def _freeze_event_context(value: Any) -> Any:
    """Recursively freeze event context containers shared across handlers."""
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_event_context(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_event_context(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze_event_context(item) for item in value)
    return value


class SharedEventBus:
    """Cross-instance event bus using Supabase as transport."""

    POLL_INTERVAL_SECONDS = 15
    MAX_EVENTS_PER_POLL = 50
    HANDLER_LEASE_SECONDS = 300
    HANDLER_LEASE_HEARTBEAT_SECONDS = 60

    def __init__(
        self, supabase_client: Any = None, source_service: str = "unknown",
        poll_interval: int = POLL_INTERVAL_SECONDS, *,
        producer_registry: ProducerRegistry | None = None,
        provenance_signer: Callable[..., dict] | None = None,
        provenance_verifier: Callable[..., VerifiedEventProvenance] | None = None,
        provenance_storage_enabled: bool = False,
    ):
        self.supabase = supabase_client
        # Explicitly injected by trusted worker composition. Legacy labels do
        # not establish provenance; durable signing is opt-in until migration
        # 158 is installed and producer keys are configured.
        self._producer_registry = producer_registry
        self._provenance_signer = provenance_signer
        self._provenance_verifier = provenance_verifier
        self._provenance_storage_enabled = bool(provenance_storage_enabled)
        self.source_service = source_service
        # Service mode identifies a role, not a process. A unique instance
        # identity prevents one worker from mistaking another worker in the
        # same service mode for its own event producer.
        self.instance_id = f"{source_service}:{uuid.uuid4().hex[:12]}"
        self.process_instance_id = get_process_instance_id()
        self.poll_interval = poll_interval

        # Durable receipts require stable handler identities across restarts.
        self._handlers: Dict[str, List[tuple[str, Callable]]] = {}
        # Per-subscription pagination prevents large relevant backlogs from
        # repeatedly presenting only their oldest page.
        self._poll_offsets: Dict[tuple[str, ...], int] = {}
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
        producer_capability: ProducerCapability | None = None,
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
            event_payload = envelope.to_payload()
            provenance_envelope = None
            if producer_capability is not None:
                if (
                    not self._provenance_storage_enabled
                    or self._producer_registry is None
                    or self._provenance_signer is None
                ):
                    logger.error("SharedEventBus provenance capability rejected: signing/storage is not configured")
                    return False
                try:
                    verified = self._producer_registry.verify(
                        producer_capability,
                        workspace_id=envelope.workspace_id,
                        actor=current_actor_principal(),
                        brand_id=envelope.brand_id,
                    )
                    provenance_envelope = self._provenance_signer(
                        verified,
                        event_id=event_id,
                        event_name=envelope.event_name,
                        payload=event_payload,
                        brand_id=envelope.brand_id,
                    )
                    if not isinstance(provenance_envelope, dict):
                        raise TypeError("provenance signer must return a JSON object")
                    json.dumps(provenance_envelope, allow_nan=False)
                except Exception as exc:
                    logger.warning("SharedEventBus provenance signing rejected: %s", exc)
                    return False

            record = {
                "id": event_id,
                "event_type": envelope.event_name,
                "payload": event_payload,
                "workspace_id": envelope.workspace_id,
                "correlation_id": envelope.correlation_id,
                "decision_id": envelope.decision_id,
                "brand_id": envelope.brand_id,
                "parent_event_id": envelope.parent_event_id,
                "source_service": envelope.source,
                "source_instance": self.process_instance_id,
                "processed": False,
                "schema_version": envelope.schema_version,
            }
            # Omit the new column for legacy unsigned writes until explicitly
            # enabled; this keeps older staging schemas backward compatible.
            if provenance_envelope is not None:
                record["provenance_envelope"] = provenance_envelope
            self.supabase.table("events").insert(record).execute()

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

    @staticmethod
    def _handler_identity(handler: Callable, handler_id: Optional[str] = None) -> str:
        """Return a stable receipt key; explicit IDs disambiguate callable instances."""
        if handler_id is not None:
            key = str(handler_id).strip()
        else:
            module = getattr(handler, "__module__", type(handler).__module__)
            qualname = getattr(handler, "__qualname__", getattr(handler, "__name__", type(handler).__qualname__))
            code = getattr(handler, "__code__", None)
            # Named functions keep the same key when unrelated edits move
            # their source lines. Lambdas need a line suffix to distinguish
            # multiple anonymous registrations in the same module.
            line = getattr(code, "co_firstlineno", None) if "<lambda>" in qualname else None
            key = f"{module}:{qualname}" + (f":{line}" if line else "")
        if not key or len(key) > 512:
            raise ValueError("handler_id must contain between 1 and 512 characters")
        return key

    def subscribe(
        self, event_name: str, handler: Callable, *, handler_id: Optional[str] = None
    ) -> Callable[[], None]:
        """Subscribe with a stable identity for durable per-handler receipts."""
        key = self._handler_identity(handler, handler_id)
        with self._lock:
            registrations = self._handlers.setdefault(event_name, [])
            existing = next(
                ((existing_key, existing_handler) for existing_key, existing_handler in registrations
                 if existing_key == key),
                None,
            )
            if existing is not None:
                if existing[1] != handler:
                    raise ValueError(
                        f"Handler identity '{key}' is already registered for event '{event_name}'; "
                        "supply a distinct handler_id"
                    )
                # Repeated wiring of the same bound method is idempotent.
            else:
                registrations.append((key, handler))
            logger.info("SharedEventBus: subscribed to '%s' (%d handlers)", event_name, len(registrations))

        def unsubscribe() -> None:
            with self._lock:
                self._handlers[event_name] = [
                    item for item in self._handlers.get(event_name, []) if item[0] != key
                ]
        return unsubscribe

    def subscribe_with_context(
        self, event_name: str, handler: Callable, *, handler_id: Optional[str] = None
    ) -> Callable[[], None]:
        """Subscribe to a read-only event context, including verified proof.

        The context's provenance is None unless this worker independently
        verifies the dedicated durable envelope. Legacy payload handlers are
        unchanged and never receive a trust-bearing payload field.
        """
        def context_handler(event_context: Dict[str, Any]):
            return handler(event_context)

        context_handler.__kdo_event_context__ = True
        stable_id = handler_id or f"context:{self._handler_identity(handler)}"
        return self.subscribe(event_name, context_handler, handler_id=stable_id)

    def unsubscribe(
        self, event_name: str, handler: Callable, *, handler_id: Optional[str] = None
    ) -> None:
        key = self._handler_identity(handler, handler_id)
        with self._lock:
            if event_name in self._handlers:
                self._handlers[event_name] = [
                    item for item in self._handlers[event_name] if item[0] != key
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

        with self._lock:
            event_names = tuple(sorted(name for name, handlers in self._handlers.items() if handlers))
            if not event_names:
                return 0
            offset = self._poll_offsets.get(event_names, 0)

        try:
            # Filter at the database before applying the page limit. Scanning the
            # oldest global events lets unrelated event types permanently occupy
            # every page and starve newly published events this group handles.
            query = self.supabase.table("events").select("*").in_("event_type", list(event_names))
            query = query.order("created_at").order("id").range(
                offset, offset + self.MAX_EVENTS_PER_POLL - 1
            )
            result = query.execute()
            events = result.data or []

            # Advance through large backlogs over successive polls. Reset only
            # after the final page so >MAX_EVENTS_PER_POLL matching events cannot
            # pin the consumer to the same oldest page forever.
            with self._lock:
                self._poll_offsets[event_names] = (
                    offset + len(events) if len(events) == self.MAX_EVENTS_PER_POLL else 0
                )

            delivered = 0
            for event in events:
                event_name = event.get("event_type") or event.get("event_name", "")
                event_id = event.get("id")
                if not event_id or event_name not in event_names:
                    continue
                # A producer instance should not synchronously consume its own
                # transport copy; local UnifiedEventBus already handled it.
                source_instance = event.get("source_instance")
                if source_instance in (self.process_instance_id, self.instance_id):
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
                "event_id": event_id, "consumer_group": self.source_service,
                "event_type": event_name, "processed": False,
            }).execute()
            return True
        except Exception as exc:
            try:
                existing = self.supabase.table("event_deliveries").select("id").eq(
                    "event_id", event_id
                ).eq("consumer_group", self.source_service).limit(1).execute()
                return bool(existing.data)
            except Exception:
                logger.warning("Unable to ensure event delivery %s: %s", event_id, exc)
                return False

    def _ensure_handler_delivery(self, event_id: str, handler_key: str) -> Optional[Dict[str, Any]]:
        """Create or retrieve one durable event/consumer/handler receipt."""
        try:
            self.supabase.table("event_handler_deliveries").insert({
                "event_id": event_id, "consumer_group": self.source_service,
                "handler_key": handler_key, "processed": False,
            }).execute()
        except Exception:
            # A unique event/group/handler constraint makes insert idempotent.
            pass
        try:
            result = self.supabase.table("event_handler_deliveries").select("*").eq(
                "event_id", event_id
            ).eq("consumer_group", self.source_service).eq(
                "handler_key", handler_key
            ).limit(1).execute()
            return dict(result.data[0]) if result.data else None
        except Exception as exc:
            logger.warning("Unable to ensure handler receipt %s/%s: %s", event_id, handler_key, exc)
            return None

    def _claim_handler_delivery(self, receipt_id: str) -> Optional[tuple[str, str]]:
        """Claim one handler receipt with a lease-fenced token."""
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=self.HANDLER_LEASE_SECONDS)).isoformat()
        claim_token = f"{self.instance_id}:{uuid.uuid4().hex}"
        try:
            result = self.supabase.table("event_handler_deliveries").update({
                "claimed_by": claim_token, "claimed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", receipt_id).eq("processed", False).or_(
                f"claimed_at.is.null,claimed_at.lt.{cutoff}"
            ).select("id").execute()
            if result.data:
                return str(result.data[0].get("id")), claim_token
        except Exception as exc:
            logger.warning("Handler receipt claim failed for %s: %s", receipt_id, exc)
        return None

    def _renew_handler_delivery_lease(self, receipt_id: str, claim_token: str) -> bool:
        """Extend a handler lease only while this worker still owns its token."""
        if not receipt_id or not claim_token or not self.supabase:
            return False
        try:
            result = self.supabase.table("event_handler_deliveries").update({
                "claimed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", receipt_id).eq("claimed_by", claim_token).eq(
                "processed", False
            ).select("id").execute()
            return bool(result.data)
        except Exception:
            logger.exception("Failed to renew handler lease %s", receipt_id)
            return False

    def _start_handler_lease_heartbeat(self, receipt_id: str, claim_token: str):
        """Keep long-running handlers leased; stop if fencing says ownership was lost."""
        stop_event = threading.Event()

        def heartbeat() -> None:
            while not stop_event.wait(self.HANDLER_LEASE_HEARTBEAT_SECONDS):
                if not self._renew_handler_delivery_lease(receipt_id, claim_token):
                    logger.warning(
                        "Handler lease renewal lost for receipt %s; stopping heartbeat",
                        receipt_id,
                    )
                    return

        thread = threading.Thread(
            target=heartbeat,
            name=f"handler-lease-{receipt_id}",
            daemon=True,
        )
        thread.start()
        return stop_event, thread

    def _mark_handler_delivery_processed(
        self, receipt_id: Optional[str], *, claim_token: Optional[str],
        success: bool = True, error: Optional[str] = None,
    ) -> bool:
        """Acknowledge a handler only while the caller owns its lease."""
        if not receipt_id or not claim_token or not self.supabase:
            return False
        # The consumer-group delivery lease is the retry/backoff gate. Once a
        # handler has definitively completed (success or failure), release its
        # per-handler lease; crashes still retain the lease until expiry.
        update = {
            "processed": bool(success),
            "processed_at": datetime.now(timezone.utc).isoformat() if success else None,
            "processing_error": (error or "")[:500] if error else None,
            "claimed_by": None,
            "claimed_at": None,
        }
        try:
            result = self.supabase.table("event_handler_deliveries").update(update).eq(
                "id", receipt_id
            ).eq("claimed_by", claim_token).eq("processed", False).select("id").execute()
            return bool(result.data)
        except Exception:
            logger.exception("Failed to acknowledge handler receipt %s", receipt_id)
            return False

    def _claim_delivery(self, event_id: str) -> Optional[tuple[str, str]]:
        """Claim one consumer delivery and return its fencing token."""
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        now = datetime.now(timezone.utc).isoformat()
        claim_token = f"{self.instance_id}:{uuid.uuid4().hex}"
        try:
            result = self.supabase.table("event_deliveries").update({
                "claimed_by": claim_token, "claimed_at": now,
            }).eq("event_id", event_id).eq("consumer_group", self.source_service).eq(
                "processed", False
            ).is_("dead_lettered_at", "null").lte(
                "next_attempt_at", now
            ).or_(f"claimed_at.is.null,claimed_at.lt.{cutoff}").select("id").execute()
            if result.data:
                return str(result.data[0].get("id")), claim_token
        except Exception as exc:
            logger.warning("SharedEventBus delivery claim failed for %s: %s", event_id, exc)
        return None

    def _dispatch_event(
        self, event: Dict[str, Any], *, delivery_id: Optional[str] = None,
        claim_token: Optional[str] = None,
    ) -> None:
        """Dispatch at-least-once with durable per-handler completion receipts.

        Completed handlers are skipped on a group retry. A crash after an
        external side effect but before its receipt commits can still duplicate
        that effect, so this is not an exactly-once guarantee.
        """
        event_name = event.get("event_type") or event.get("event_name", "")
        event_id = event.get("id")
        source_instance = event.get("source_instance", "")
        if source_instance and source_instance in (self.process_instance_id, self.instance_id):
            return
        try:
            payload = event.get("payload", {})
            if isinstance(payload, str):
                payload = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            payload = {}

        provenance = None
        signed_envelope = event.get("provenance_envelope")
        if signed_envelope is not None and self._provenance_verifier is not None:
            try:
                provenance = self._provenance_verifier(
                    signed_envelope,
                    event_id=str(event_id),
                    event_name=event_name,
                    workspace_id=str(event.get("workspace_id") or ""),
                    brand_id=event.get("brand_id"),
                    payload=payload,
                )
                if (
                    not isinstance(provenance, VerifiedEventProvenance)
                    or provenance.workspace_id != event.get("workspace_id")
                    or provenance.brand_id != event.get("brand_id")
                ):
                    raise ValueError("verifier returned provenance for a different event scope")
            except Exception as exc:
                provenance = None
                logger.warning("SharedEventBus provenance verification failed for %s: %s", event_id, exc)

        event_context = _freeze_event_context({
            "event_id": str(event_id),
            "event_name": event_name,
            "workspace_id": event.get("workspace_id"),
            "brand_id": event.get("brand_id"),
            "source_service": event.get("source_service"),
            "source_instance": event.get("source_instance"),
            "payload": payload,
            "provenance": provenance,
        })

        with self._lock:
            handlers = list(self._handlers.get(event_name, []))
        if not handlers:
            return

        handler_failed = False
        for handler_key, handler in handlers:
            receipt = self._ensure_handler_delivery(str(event_id), handler_key)
            if receipt is None:
                handler_failed = True
                continue
            if receipt.get("processed") is True:
                continue
            claim = self._claim_handler_delivery(str(receipt["id"]))
            if claim is None:
                handler_failed = True
                continue
            receipt_id, receipt_token = claim
            stop_heartbeat, heartbeat_thread = self._start_handler_lease_heartbeat(
                receipt_id, receipt_token
            )
            try:
                if getattr(handler, "__kdo_event_context__", False):
                    result = handler(event_context)
                else:
                    result = handler(payload)
                if inspect.isawaitable(result):
                    asyncio.run(result)
                if self._mark_handler_delivery_processed(
                    receipt_id, claim_token=receipt_token, success=True
                ):
                    self._events_received += 1
                else:
                    handler_failed = True
            except Exception as exc:
                handler_failed = True
                self._mark_handler_delivery_processed(
                    receipt_id, claim_token=receipt_token, success=False,
                    error=str(exc) or type(exc).__name__,
                )
                logger.error("SharedEventBus handler failed for '%s' (%s): %s", event_name, handler_key, exc)
            finally:
                stop_heartbeat.set()
                heartbeat_thread.join(timeout=2)

        if handler_failed:
            self._mark_delivery_processed(
                delivery_id, claim_token=claim_token, success=False,
                error="one or more handlers failed or remain leased",
            )
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
        """Complete a consumer delivery through the lease-fenced retry RPC."""
        if not delivery_id or not claim_token or not self.supabase:
            return False
        try:
            result = self.supabase.rpc(
                "complete_shared_event_delivery",
                {
                    "p_delivery_id": delivery_id,
                    "p_consumer_group": self.source_service,
                    "p_claim_token": claim_token,
                    "p_success": bool(success),
                    "p_error": (error or "")[:1000] if error else None,
                    "p_max_attempts": 5,
                },
            ).execute()
            acknowledged = result.data is True
            if not acknowledged:
                logger.warning(
                    "SharedEventBus completion rejected for stale claim %s",
                    delivery_id,
                )
            return acknowledged
        except Exception:
            logger.exception("SharedEventBus failed to complete delivery %s", delivery_id)
            return False

    def get_dead_letters(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return durable deliveries that exhausted their retry budget."""
        if not self.supabase:
            return []
        result = self.supabase.table("event_deliveries").select(
            "id,event_id,event_type,consumer_group,attempt_count,processing_error,dead_lettered_at"
        ).eq("consumer_group", self.source_service).not_.is_(
            "dead_lettered_at", "null"
        ).order("dead_lettered_at", desc=True).limit(max(1, min(int(limit), 200))).execute()
        return result.data or []

    def requeue_dead_letter(self, delivery_id: str) -> bool:
        """Explicitly return one of this consumer group's dead letters to retry."""
        if not delivery_id or not self.supabase:
            return False
        result = self.supabase.table("event_deliveries").update({
            "attempt_count": 0,
            "dead_lettered_at": None,
            "next_attempt_at": datetime.now(timezone.utc).isoformat(),
            "processing_error": None,
            "claimed_by": None,
            "claimed_at": None,
        }).eq("id", delivery_id).eq(
            "consumer_group", self.source_service
        ).not_.is_("dead_lettered_at", "null").select("id").execute()
        return bool(result.data)

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

    def get_delivery_metrics(self) -> Dict[str, Any]:
        """Return database-aggregated delivery health for this consumer group."""
        empty = {
            "consumer_group": self.source_service,
            "total_deliveries": 0,
            "pending_count": 0,
            "due_count": 0,
            "scheduled_retry_count": 0,
            "in_flight_count": 0,
            "completed_count": 0,
            "dead_letter_count": 0,
            "retrying_count": 0,
            "retry_rate_percent": 0,
            "average_delivery_latency_ms": None,
            "p95_delivery_latency_ms": None,
            "oldest_pending_age_seconds": 0,
        }
        if not self.supabase:
            return {**empty, "available": False}
        try:
            result = self.supabase.rpc(
                "get_shared_event_delivery_metrics",
                {"p_consumer_group": self.source_service},
            ).execute()
            rows = result.data or []
            if isinstance(rows, dict):
                row = rows
            else:
                row = rows[0] if rows else empty
            return {**empty, **dict(row), "available": True}
        except Exception:
            logger.exception(
                "SharedEventBus delivery metrics unavailable for '%s'",
                self.source_service,
            )
            return {**empty, "available": False}

    def health_check(self) -> dict:
        stats = self.get_stats()
        status = "healthy" if self.supabase else "degraded"
        # Keep transport health and delivery evidence separate. A missing
        # metrics migration is reported as unavailable, not misrepresented as
        # a healthy zero-backlog measurement.
        delivery_metrics = self.get_delivery_metrics()
        return {
            "status": status,
            "stats": stats,
            "delivery_metrics": delivery_metrics,
        }


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
