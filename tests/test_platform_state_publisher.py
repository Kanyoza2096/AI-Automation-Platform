import asyncio
from dataclasses import replace
from datetime import datetime, timezone

from core.platform_state import (
    EvidenceRef,
    ObserverObservation,
    PlatformStateSnapshot,
    Readiness,
    ServiceState,
)
from core.platform_state_publisher import PlatformStatePublisher
from core.platform_state_store import PlatformStateConflict


NOW = datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc)


def snapshot(revision=1, status=Readiness.HEALTHY, observed_at=NOW):
    return PlatformStateSnapshot(
        revision=revision,
        updated_at=NOW,
        services=(
            ServiceState(
                service_id="director",
                owner="director",
                readiness=status,
                observed_at=observed_at,
                source_version="health-v1",
                dependencies=(),
                capabilities=("strategic_planning",),
                critical=True,
                evidence=(
                    EvidenceRef(
                        "health_registry:director",
                        observed_at,
                        "health-v1",
                        correlation_id="corr-director",
                    ),
                ),
            ),
        ),
    )


class FakeStore:
    def __init__(self, current=None, conflict_once=False):
        self.current = current
        self.saved = []
        self.conflict_once = conflict_once

    async def load(self, *, scope_type="platform", scope_id="platform"):
        assert (scope_type, scope_id) == ("platform", "platform")
        return self.current

    async def save(self, candidate, *, expected_revision=None):
        if self.conflict_once:
            self.conflict_once = False
            self.current = snapshot(revision=2, observed_at=NOW)
            raise PlatformStateConflict("concurrent writer")
        assert expected_revision == (self.current.revision if self.current else 0)
        self.saved.append((candidate, expected_revision))
        self.current = candidate
        return candidate.revision


class FakeBus:
    supabase = object()

    def __init__(self, result="evt_123"):
        self.result = result
        self.events = []

    async def publish(self, event_name, payload, **kwargs):
        self.events.append((event_name, payload, kwargs))
        return self.result


def test_publisher_persists_before_emitting_revision_notification():
    store = FakeStore()
    bus = FakeBus()
    publisher = PlatformStatePublisher(store, bus)

    persisted = asyncio.run(publisher.publish(snapshot()))

    assert persisted.revision == 1
    assert store.current is persisted
    assert len(store.saved) == 1
    event_name, payload, kwargs = bus.events[0]
    assert event_name == "platform.state.updated"
    assert payload["revision"] == 1
    assert payload["scope_id"] == "platform"
    assert payload["changed_service_ids"] == ["director"]
    assert payload["changed_capability_ids"] == ["strategic_planning"]
    assert len(payload["snapshot_digest"]) == 64
    assert kwargs["workspace_id"] == "platform"
    assert kwargs["source"] == "health_monitor"


def test_observer_evidence_changes_emit_affected_service_id():
    candidate = replace(
        snapshot(),
        observer_observations=(
            ObserverObservation(
                service_id="director",
                observer_id="meta_engine",
                readiness=Readiness.DEGRADED,
                observed_at=NOW,
                source_version="meta-v7.1",
            ),
        ),
    )
    store = FakeStore()
    bus = FakeBus()

    result = asyncio.run(PlatformStatePublisher(store, bus).publish(candidate))

    assert result.revision == 1
    assert bus.events[0][1]["changed_service_ids"] == ["director"]


def test_publisher_does_not_write_or_emit_when_facts_are_unchanged():
    existing = snapshot(revision=7)
    candidate = replace(existing, revision=1, updated_at=NOW.replace(minute=5))
    store = FakeStore(current=existing)
    bus = FakeBus()

    result = asyncio.run(PlatformStatePublisher(store, bus).publish(candidate))

    assert result is existing
    assert store.saved == []
    assert bus.events == []


def test_publisher_reloads_and_retries_compare_and_swap_conflict():
    store = FakeStore(conflict_once=True)
    bus = FakeBus()

    result = asyncio.run(PlatformStatePublisher(store, bus).publish(
        snapshot(status=Readiness.DEGRADED, observed_at=NOW.replace(minute=1))
    ))

    assert result.revision == 3
    assert len(store.saved) == 1
    assert store.saved[0][1] == 2
    assert bus.events[0][1]["revision"] == 3


def test_snapshot_persists_without_event_transport_and_skips_notification():
    store = FakeStore()
    bus = FakeBus()
    bus.supabase = None

    result = asyncio.run(PlatformStatePublisher(store, bus).publish(snapshot()))

    assert result.revision == 1
    assert store.current is result
    assert len(store.saved) == 1
    assert bus.events == []


def test_notification_failure_does_not_rollback_authoritative_snapshot():
    store = FakeStore()
    bus = FakeBus(result=None)

    result = asyncio.run(PlatformStatePublisher(store, bus).publish(snapshot()))

    assert result.revision == 1
    assert store.current is result
    assert len(store.saved) == 1
    assert len(bus.events) == 1


def test_platform_state_event_is_a_platform_scoped_nerve_event():
    from core.nerve import validate_and_normalize

    envelope = validate_and_normalize(
        "platform.state.updated",
        {"revision": 1},
        source="health_monitor",
        strict=True,
    )
    assert envelope.workspace_id == "platform"
