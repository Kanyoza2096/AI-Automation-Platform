"""Tenant-scoped, read-only adapter from unified events into organism perception.

The bridge observes a small allowlist of operational event types. It never
forwards raw payloads, credentials, free-form error text, or execution authority.
The pure observation mapper is shared with durable event-ledger recovery so live
ingestion and replay enforce exactly the same field and tenant policy.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from datetime import datetime
from typing import Any
import uuid

from organism.perception import Observation, ObservationKind


EVENT_KINDS = {
    "service_heartbeat": ObservationKind.HEALTH,
    "engine_alert": ObservationKind.HEALTH,
    "backup_completed": ObservationKind.HEALTH,
    "task_failed": ObservationKind.EXECUTION,
    "content_failed": ObservationKind.EXECUTION,
    "post_published": ObservationKind.EXECUTION,
}
SAFE_FIELDS = {
    ObservationKind.HEALTH: frozenset({
        "service", "service_name", "status", "health", "severity",
        "component", "cpu_percent", "memory_percent",
        "operational_load", "latency_ms", "uptime_seconds",
        "duration_seconds", "backup_type",
    }),
    ObservationKind.EXECUTION: frozenset({
        "task_id", "execution_id", "task_type", "action", "status",
        "outcome", "success", "duration_seconds", "platform",
        "content_type", "post_id",
    }),
}


def observation_from_event(event: Mapping[str, Any]) -> Observation | None:
    """Convert one ledger/live event to a bounded, tenant-checked observation."""
    if not isinstance(event, Mapping):
        return None
    event_name = event.get("event_name")
    kind = EVENT_KINDS.get(event_name)
    if kind is None:
        return None
    workspace_id = event.get("workspace_id")
    if not isinstance(workspace_id, str) or not workspace_id.strip():
        return None
    workspace_id = workspace_id.strip()
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return None
    allowed = SAFE_FIELDS[kind]
    safe_value = {
        key: payload[key]
        for key in allowed
        if key in payload
        and isinstance(payload[key], (str, int, float, bool))
        and not isinstance(payload[key], (dict, list))
    }
    if not safe_value:
        return None
    observed_at = event.get("observed_at")
    if isinstance(observed_at, str):
        try:
            observed_at = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        except ValueError:
            observed_at = None
    if observed_at is not None and (
        not isinstance(observed_at, datetime) or observed_at.tzinfo is None
    ):
        observed_at = None
    event_id = str(event.get("event_id") or "").strip()
    return Observation(
        workspace_id=workspace_id,
        kind=kind,
        source=f"unified_event:{event_name}",
        value=safe_value,
        confidence=0.8,
        source_trust=0.8,
        **({"observed_at": observed_at} if observed_at is not None else {}),
        observation_id=f"event:{event_id}" if event_id else f"obs_{uuid.uuid4().hex[:16]}",
        metadata={
            "event_id": event_id or None,
            "source": str(event.get("source") or "unknown")[:80],
            "correlation_id": str(event.get("correlation_id") or "")[:128] or None,
        },
    )


class OrganismEventBridge:
    """Route selected, workspace-bound events into existing organism runtimes."""

    EVENT_KINDS = EVENT_KINDS
    SAFE_FIELDS = SAFE_FIELDS
    MAX_SEEN_EVENT_IDS = 5000

    def __init__(self, registry) -> None:
        self.registry = registry
        self._unsubscribe = None
        self._seen_ids: set[str] = set()
        self._seen_order: deque[str] = deque()

    async def start(self, bus) -> None:
        """Subscribe once to the unified bus wildcard; filtering stays local."""
        if self._unsubscribe is not None:
            return
        self._unsubscribe = await bus.subscribe_all(self.handle)

    async def stop(self) -> None:
        unsubscribe = self._unsubscribe
        self._unsubscribe = None
        if unsubscribe is not None:
            unsubscribe()

    async def handle(self, event: Mapping[str, Any]) -> None:
        if not isinstance(event, Mapping):
            return
        event_name = event.get("event_name")
        if event_name not in self.EVENT_KINDS:
            return
        workspace_id = event.get("workspace_id")
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            return
        workspace_id = workspace_id.strip()
        event_id = str(event.get("event_id") or "").strip()
        if event_id and event_id in self._seen_ids:
            return

        # Events may only enrich an organism already established by the
        # authenticated workspace lifecycle. The bridge must not create tenants.
        organism = self.registry.get(workspace_id)
        if organism is None:
            return
        runtime = organism.runtime
        if runtime is None or runtime.workspace_id != workspace_id:
            raise RuntimeError("organism runtime workspace boundary unavailable")
        observation = observation_from_event(event)
        if observation is None:
            return
        runtime.ingest(observation)
        if event_id:
            self._remember(event_id)

    def _remember(self, event_id: str) -> None:
        self._seen_ids.add(event_id)
        self._seen_order.append(event_id)
        while len(self._seen_order) > self.MAX_SEEN_EVENT_IDS:
            expired = self._seen_order.popleft()
            self._seen_ids.discard(expired)
