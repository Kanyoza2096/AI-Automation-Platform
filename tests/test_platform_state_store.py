import asyncio
from datetime import datetime, timezone

import pytest

from core.platform_state import (
    EvidenceRef,
    PlatformStateSnapshot,
    Readiness,
    ServiceState,
)
from core.platform_state_store import (
    PlatformStateConflict,
    PlatformStateStore,
)


NOW = datetime(2026, 11, 11, 14, 30, tzinfo=timezone.utc)


class Result:
    def __init__(self, data):
        self.data = data


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
        return Result([self.row] if self.row else [])


class FakeClient:
    def __init__(self, *, row=None, write_result=1, write_error=None):
        self.row = row
        self.write_result = write_result
        self.write_error = write_error
        self.rpc_calls = []

    def table(self, _name):
        return Query(self.row)

    def rpc(self, name, args):
        self.rpc_calls.append((name, args))
        if self.write_error:
            raise self.write_error
        return QueryResult(self.write_result)


class QueryResult:
    def __init__(self, data):
        self.data = data

    def execute(self):
        return Result(self.data)


def snapshot(revision=1):
    return PlatformStateSnapshot(
        revision=revision,
        updated_at=NOW,
        services=(
            ServiceState(
                service_id="director",
                owner="director",
                readiness=Readiness.HEALTHY,
                observed_at=NOW,
                source_version="v1",
                dependencies=(),
                capabilities=(),
                critical=True,
                evidence=(EvidenceRef("health_registry:director", NOW, "v1"),),
            ),
        ),
    )


def test_store_save_uses_atomic_rpc_and_expected_revision():
    client = FakeClient(write_result=1)
    store = PlatformStateStore(client)
    revision = asyncio.run(store.save(snapshot(1), expected_revision=0))
    assert revision == 1
    name, args = client.rpc_calls[0]
    assert name == "write_platform_state_snapshot"
    assert args["p_expected_revision"] == 0
    assert args["p_state"]["services"][0]["service_id"] == "director"


def test_store_translates_revision_conflict():
    client = FakeClient(write_error=RuntimeError("platform_state_revision_conflict"))
    store = PlatformStateStore(client)
    with pytest.raises(PlatformStateConflict):
        asyncio.run(store.save(snapshot(2), expected_revision=1))


def test_store_rejects_nonconsecutive_revision_before_rpc():
    client = FakeClient()
    store = PlatformStateStore(client)
    with pytest.raises(ValueError, match="expected_revision"):
        asyncio.run(store.save(snapshot(3), expected_revision=1))
    assert client.rpc_calls == []


def test_store_loads_json_object_snapshot():
    original = snapshot(1)
    from core.platform_state_store import _state_payload

    client = FakeClient(row={
        "scope_type": "platform",
        "scope_id": "platform",
        "revision": 1,
        "schema_version": 1,
        "state": _state_payload(original),
        "updated_at": NOW.isoformat(),
    })
    loaded = asyncio.run(PlatformStateStore(client).load())
    assert loaded is not None
    assert loaded.revision == original.revision
    assert loaded.services[0].service_id == "director"
    assert loaded.services[0].readiness is Readiness.HEALTHY
    assert loaded.services[0].critical is True
    assert loaded.services[0].evidence[0].source == "health_registry:director"
