"""V69.17 — Cross-System Business Event Fabric.

Durable, tenant-scoped event routing for canonical connector/workflow events.
This plane transports events; it does not execute providers, grant authority,
or bypass workflow/policy/execution boundaries.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib, json
from typing import Any, Mapping
from uuid import uuid4

from services.v69_integration_evidence import EvidenceSink, emit


class EventFabricBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    DUPLICATE_EVENT = "duplicate_event"
    INVALID_EVENT = "invalid_event"
    SUBSCRIPTION_MISMATCH = "subscription_mismatch"
    DESTINATION_MISMATCH = "destination_mismatch"
    LOOP_DETECTED = "loop_detected"
    HOP_LIMIT = "hop_limit"
    ORDERING_CONFLICT = "ordering_conflict"


class EventFabricError(ValueError):
    pass


@dataclass(frozen=True)
class BusinessEvent:
    event_id: str
    workspace_id: str
    event_type: str
    source: str
    payload: Mapping[str, Any]
    correlation_id: str
    causation_id: str | None = None
    sequence: int | None = None
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    trace: tuple[str, ...] = ()
    hop: int = 0
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if not self.event_id.strip() or not self.workspace_id.strip() or not self.event_type.strip() or not self.source.strip():
            raise EventFabricError(EventFabricBlock.INVALID_EVENT.value)
        if self.occurred_at.tzinfo is None:
            raise EventFabricError("occurred_at_must_be_timezone_aware")
        if self.hop < 0:
            raise EventFabricError("invalid_hop")
        object.__setattr__(self, "fingerprint", _fingerprint(self.payload))


@dataclass(frozen=True)
class EventSubscription:
    subscription_id: str
    workspace_id: str
    event_type: str
    destination: str
    enabled: bool = True
    max_hops: int = 8
    require_sequence: bool = False

    def __post_init__(self) -> None:
        if not self.subscription_id.strip() or not self.workspace_id.strip() or not self.event_type.strip() or not self.destination.strip():
            raise EventFabricError("invalid_subscription")
        if self.max_hops < 1:
            raise EventFabricError("invalid_hop_limit")


@dataclass(frozen=True)
class Delivery:
    delivery_id: str
    workspace_id: str
    event_id: str
    subscription_id: str
    destination: str
    state: str
    attempt: int = 0


@dataclass(frozen=True)
class FabricResult:
    accepted: bool
    event: BusinessEvent | None = None
    deliveries: tuple[Delivery, ...] = ()
    reasons: tuple[str, ...] = ()


def _fingerprint(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


class MemoryEventFabric:
    """Deterministic in-memory reference implementation of the durable contract."""
    def __init__(self, workspace_id: str, *, evidence_sink: EvidenceSink | None = None) -> None:
        if not workspace_id.strip():
            raise ValueError(EventFabricBlock.TENANT_MISMATCH.value)
        self.workspace_id = workspace_id
        self.evidence_sink = evidence_sink
        self._events: dict[str, BusinessEvent] = {}
        self._subscriptions: dict[str, EventSubscription] = {}
        self._deliveries: dict[tuple[str, str], Delivery] = {}
        self._last_sequence: dict[str, int] = {}

    def subscribe(self, subscription: EventSubscription) -> EventSubscription:
        if subscription.workspace_id != self.workspace_id:
            raise ValueError(EventFabricBlock.TENANT_MISMATCH.value)
        if subscription.subscription_id in self._subscriptions:
            raise ValueError("duplicate_subscription")
        self._subscriptions[subscription.subscription_id] = subscription
        return subscription

    def publish(self, event: BusinessEvent) -> FabricResult:
        reasons: list[str] = []
        if event.workspace_id != self.workspace_id:
            reasons.append(EventFabricBlock.TENANT_MISMATCH.value)
        if event.event_id in self._events:
            reasons.append(EventFabricBlock.DUPLICATE_EVENT.value)
        if event.source in event.trace or event.hop > 16:
            reasons.append(EventFabricBlock.LOOP_DETECTED.value if event.source in event.trace else EventFabricBlock.HOP_LIMIT.value)
        if event.sequence is not None:
            previous = self._last_sequence.get(event.source)
            if previous is not None and event.sequence <= previous:
                reasons.append(EventFabricBlock.ORDERING_CONFLICT.value)
        if reasons:
            if self.evidence_sink:
                emit(self.evidence_sink, evidence_id=str(uuid4()), workspace_id=self.workspace_id, connection_id="event-fabric", connector_key="event-fabric", event_type="event_publish_reject", outcome="blocked", reason_codes=reasons, action=event.event_type, correlation_id=event.correlation_id, payload=event.payload)
            return FabricResult(False, reasons=tuple(dict.fromkeys(reasons)))
        self._events[event.event_id] = event
        if event.sequence is not None:
            self._last_sequence[event.source] = event.sequence
        deliveries: list[Delivery] = []
        for sub in self._subscriptions.values():
            if not sub.enabled or sub.workspace_id != self.workspace_id or sub.event_type != event.event_type:
                continue
            if event.hop >= sub.max_hops:
                continue
            key = (event.event_id, sub.subscription_id)
            if key in self._deliveries:
                continue
            d = Delivery(str(uuid4()), self.workspace_id, event.event_id, sub.subscription_id, sub.destination, "PENDING")
            self._deliveries[key] = d
            deliveries.append(d)
        if self.evidence_sink:
            emit(self.evidence_sink, evidence_id=str(uuid4()), workspace_id=self.workspace_id, connection_id="event-fabric", connector_key="event-fabric", event_type="event_publish", outcome="accepted", action=event.event_type, correlation_id=event.correlation_id, payload=event.payload, metadata={"delivery_count": len(deliveries)})
        return FabricResult(True, event=event, deliveries=tuple(deliveries))

    def mark_delivery(self, event_id: str, subscription_id: str, state: str, *, attempt: int = 0) -> Delivery:
        if (event_id, subscription_id) not in self._deliveries:
            raise KeyError("delivery_not_found")
        old = self._deliveries[(event_id, subscription_id)]
        if old.workspace_id != self.workspace_id:
            raise ValueError(EventFabricBlock.TENANT_MISMATCH.value)
        if state not in {"PENDING", "DELIVERED", "FAILED", "RETRYABLE", "BLOCKED"}:
            raise ValueError("invalid_delivery_state")
        d = Delivery(old.delivery_id, old.workspace_id, old.event_id, old.subscription_id, old.destination, state, attempt)
        self._deliveries[(event_id, subscription_id)] = d
        return d

    def deliveries_for(self, event_id: str) -> tuple[Delivery, ...]:
        return tuple(d for (eid, _), d in self._deliveries.items() if eid == event_id and d.workspace_id == self.workspace_id)

    def event(self, event_id: str) -> BusinessEvent | None:
        e = self._events.get(event_id)
        return e if e and e.workspace_id == self.workspace_id else None


__all__ = ["EventFabricBlock", "EventFabricError", "BusinessEvent", "EventSubscription", "Delivery", "FabricResult", "MemoryEventFabric"]
