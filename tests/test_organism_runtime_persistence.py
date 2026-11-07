import hashlib
import json
from types import SimpleNamespace

import pytest

from core.organism_state import OperatingMode, OrganismState
from organism.lifecycle import OrganismRuntime
from organism.persistence import OrganismRuntimeRecoveryError, OrganismRuntimeStore


class _Query:
    def __init__(self, client, table):
        self.client = client
        self.table_name = table
        self.filters = {}
        self.event_types = None
        self.operation = "select"
        self.record = None

    def select(self, _columns):
        self.operation = "select"
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def in_(self, key, values):
        assert key == "event_type"
        self.event_types = set(values)
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, _limit):
        return self

    def upsert(self, record, on_conflict=None):
        assert on_conflict == "workspace_id"
        self.operation = "upsert"
        self.record = record
        return self

    def execute(self):
        if self.client.fail:
            raise ConnectionError("database unavailable")
        if self.operation == "upsert":
            self.client.snapshots[self.record["workspace_id"]] = self.record
            return SimpleNamespace(data=[self.record])
        if self.table_name == "organism_runtime_snapshots":
            row = self.client.snapshots.get(self.filters.get("workspace_id"))
            return SimpleNamespace(data=[row] if row else [])
        rows = [
            row for row in self.client.events
            if row.get("workspace_id") == self.filters.get("workspace_id")
            and (self.event_types is None or row.get("event_type") in self.event_types)
        ]
        rows.sort(key=lambda row: row.get("created_at", ""), reverse=True)
        return SimpleNamespace(data=rows[:1000])


class _Client:
    def __init__(self, snapshots=None, events=None, fail=False):
        self.snapshots = snapshots or {}
        self.events = events or []
        self.fail = fail

    def table(self, name):
        return _Query(self, name)


def _snapshot(workspace_id, *, mode="executing", authority="execute", schema=1):
    state = OrganismState(
        workspace_id=workspace_id,
        mode=OperatingMode(mode),
        authority=authority,
        health=0.6,
    ).to_dict()
    encoded = json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return {
        "workspace_id": workspace_id,
        "schema_version": schema,
        "state": state,
        "state_sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    }


@pytest.mark.asyncio
async def test_restore_recovers_snapshot_and_only_safe_workspace_events():
    client = _Client(
        snapshots={"workspace-a": _snapshot("workspace-a")},
        events=[
            {
                "id": "evt-a",
                "event_type": "service_heartbeat",
                "workspace_id": "workspace-a",
                "created_at": "2026-11-07T10:00:00+00:00",
                "payload": {"status": "healthy", "cpu_percent": 12, "api_key": "secret"},
            },
            {
                "id": "evt-b",
                "event_type": "task_failed",
                "workspace_id": "workspace-b",
                "created_at": "2026-11-07T10:01:00+00:00",
                "payload": {"status": "failed", "action": "publish"},
            },
        ],
    )
    runtime = OrganismRuntime("workspace-a")

    status = await OrganismRuntimeStore(client).restore(runtime)

    assert status == "restored"
    assert runtime.recovery_status == "restored"
    assert runtime.state.workspace_id == "workspace-a"
    assert runtime.state.authority == "suggest"
    assert runtime.state.mode is OperatingMode.OBSERVING
    observations = runtime.perception.recent("workspace-a")
    assert len(observations) == 1
    assert observations[0].value == {"status": "healthy", "cpu_percent": 12}
    assert observations[0].metadata["event_id"] == "evt-a"


@pytest.mark.asyncio
async def test_empty_database_is_distinguished_from_recovery_failure():
    runtime = OrganismRuntime("workspace-empty")
    status = await OrganismRuntimeStore(_Client()).restore(runtime)
    assert status == "empty_initial_state"
    assert runtime.state is None


@pytest.mark.asyncio
async def test_restore_rejects_unknown_snapshot_schema_and_does_not_mark_recovered():
    runtime = OrganismRuntime("workspace-a")
    client = _Client(snapshots={"workspace-a": _snapshot("workspace-a", schema=99)})
    with pytest.raises(OrganismRuntimeRecoveryError, match="incompatible_snapshot_schema"):
        await OrganismRuntimeStore(client).restore(runtime)
    assert runtime.recovery_status == "recovery_failed"
    assert runtime.state is None


@pytest.mark.asyncio
async def test_restore_rejects_snapshot_tampering():
    row = _snapshot("workspace-a")
    row["state"]["health"] = 0.99
    runtime = OrganismRuntime("workspace-a")
    with pytest.raises(OrganismRuntimeRecoveryError, match="snapshot_integrity_mismatch"):
        await OrganismRuntimeStore(_Client(snapshots={"workspace-a": row})).restore(runtime)
    assert runtime.recovery_status == "recovery_failed"


@pytest.mark.asyncio
async def test_database_unavailable_is_not_reported_as_empty_state():
    runtime = OrganismRuntime("workspace-a")
    with pytest.raises(OrganismRuntimeRecoveryError, match="recovery failed"):
        await OrganismRuntimeStore(_Client(fail=True)).restore(runtime)
    assert runtime.recovery_status == "recovery_failed"


@pytest.mark.asyncio
async def test_persist_strips_execution_authority_and_round_trips_workspace_state():
    client = _Client()
    runtime = OrganismRuntime("workspace-a")
    runtime.state = OrganismState(
        workspace_id="workspace-a",
        mode=OperatingMode.EXECUTING,
        authority="execute",
        health=0.7,
    )

    await OrganismRuntimeStore(client).persist(runtime)

    stored = client.snapshots["workspace-a"]
    assert stored["schema_version"] == 1
    assert stored["state"]["authority"] == "suggest"
    assert stored["state"]["mode"] == "observing"
    restored = OrganismRuntime("workspace-a")
    await OrganismRuntimeStore(client).restore(restored)
    assert restored.state.authority == "suggest"
    assert restored.state.mode is OperatingMode.OBSERVING


@pytest.mark.asyncio
async def test_persist_rejects_cross_workspace_state():
    runtime = OrganismRuntime("workspace-a")
    runtime.state = OrganismState(workspace_id="workspace-b")
    with pytest.raises(ValueError, match="does not match runtime"):
        await OrganismRuntimeStore(_Client()).persist(runtime)


@pytest.mark.asyncio
async def test_live_and_replayed_copy_of_same_event_is_idempotent():
    from organism.event_bridge import observation_from_event

    runtime = OrganismRuntime("workspace-a")
    event = {
        "event_name": "service_heartbeat",
        "event_id": "evt-shared",
        "workspace_id": "workspace-a",
        "payload": {"status": "healthy"},
    }
    first = observation_from_event(event)
    replayed = observation_from_event(event)
    runtime.ingest(first)
    runtime.ingest(replayed)
    assert runtime.perception.recent("workspace-a") == (first,)


@pytest.mark.asyncio
async def test_malformed_ledger_payload_does_not_abort_valid_event_recovery():
    client = _Client(events=[
        {
            "id": "evt-malformed",
            "event_type": "service_heartbeat",
            "workspace_id": "workspace-a",
            "created_at": "2026-11-07T10:00:00+00:00",
            "payload": ["unexpected", "list"],
        },
        {
            "id": "evt-valid",
            "event_type": "service_heartbeat",
            "workspace_id": "workspace-a",
            "created_at": "2026-11-07T10:01:00+00:00",
            "payload": {"status": "healthy"},
        },
    ])
    runtime = OrganismRuntime("workspace-a")

    status = await OrganismRuntimeStore(client).restore(runtime)

    assert status == "restored"
    observations = runtime.perception.recent("workspace-a")
    assert len(observations) == 1
    assert observations[0].observation_id == "event:evt-valid"


@pytest.mark.asyncio
async def test_repeated_restore_is_idempotent_and_recovered_observations_reach_ooda():
    from organism.lifecycle import cycle_async

    client = _Client(events=[{
        "id": "evt-replayed",
        "event_type": "service_heartbeat",
        "workspace_id": "workspace-a",
        "created_at": "2026-11-07T10:00:00+00:00",
        "payload": {"service": "api", "status": "healthy"},
    }])
    store = OrganismRuntimeStore(client)
    runtime = OrganismRuntime("workspace-a")

    assert await store.restore(runtime) == "restored"
    assert await store.restore(runtime) == "restored"
    assert len(runtime.perception.recent("workspace-a")) == 1

    result = await cycle_async(runtime)
    assert result.workspace_id == "workspace-a"
    assert result.observations == runtime.perception.recent("workspace-a")
    assert result.state.workspace_id == "workspace-a"
    assert result.state.authority == "suggest"
