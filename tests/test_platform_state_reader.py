import asyncio
from datetime import datetime, timezone

from core.platform_state import (
    CapabilityState,
    ObserverObservation,
    PlatformStateSnapshot,
    Readiness,
    ServiceState,
)
from core.platform_state_reader import PlatformStateReader


NOW = datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc)


class Query:
    def __init__(self, row):
        self.row = row

    def select(self, _columns):
        return self

    def eq(self, _key, _value):
        return self

    def limit(self, _limit):
        return self

    def execute(self):
        return type("Result", (), {"data": [self.row] if self.row else []})()


class FakeClient:
    def __init__(self, row):
        self.row = row

    def table(self, _name):
        return Query(self.row)


def test_reader_exposes_service_and_capability_without_write_api():
    from core.platform_state_store import _state_payload

    snapshot = PlatformStateSnapshot(
        revision=2,
        updated_at=NOW,
        services=(
            ServiceState(
                service_id="director",
                owner="director",
                readiness=Readiness.DEGRADED,
                observed_at=NOW,
                source_version="health-v1",
                dependencies=(),
                capabilities=("strategic_planning",),
                critical=True,
                evidence=(),
            ),
        ),
        observer_observations=(
            ObserverObservation(
                service_id="director",
                observer_id="meta_engine",
                readiness=Readiness.DEGRADED,
                observed_at=NOW,
                source_version="meta-v7.1",
            ),
        ),
        capabilities=(
            CapabilityState(
                capability_id="strategic_planning",
                owner_service="director",
                readiness=Readiness.DEGRADED,
                observed_at=NOW,
            ),
        ),
    )
    client = FakeClient({
        "scope_type": "platform",
        "scope_id": "platform",
        "revision": 2,
        "schema_version": 1,
        "state": _state_payload(snapshot),
        "updated_at": NOW.isoformat(),
    })
    reader = PlatformStateReader(client)

    service = asyncio.run(reader.get_service("director"))
    capability = asyncio.run(reader.get_capability("strategic_planning"))

    assert service is not None and service.readiness is Readiness.DEGRADED
    assert capability is not None and capability.readiness is Readiness.DEGRADED
    restored = asyncio.run(reader.get_snapshot())
    assert restored is not None
    assert restored.observer_observations[0].observer_id == "meta_engine"
    assert restored.observer_observations[0].readiness is Readiness.DEGRADED
    assert not hasattr(reader, "save")


def test_reader_returns_none_for_unknown_ids():
    reader = PlatformStateReader(FakeClient(None))

    assert asyncio.run(reader.get_service("missing")) is None
    assert asyncio.run(reader.get_capability("missing")) is None
