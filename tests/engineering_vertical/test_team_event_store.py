import pytest
from services.kdo_engineering_team_events import TeamEventStore


def test_scoped_event_chain_appends_and_verifies():
    store = TeamEventStore()
    first = store.append(task_id="t", tenant_id="tenant-a", workspace_id="w",
        event_type="task.created", actor_id="coordinator", payload={"status": "queued"})
    store.append(task_id="t", tenant_id="tenant-a", workspace_id="w",
        event_type="agent.assigned", actor_id="coordinator", payload={"role": "planner"},
        expected_previous_hash=first["event_hash"])
    events = store.list_events(task_id="t", tenant_id="tenant-a", workspace_id="w")
    assert len(events) == 2
    assert store.verify_chain(task_id="t", tenant_id="tenant-a", workspace_id="w")


def test_event_compare_and_swap_rejects_stale_chain_head():
    store = TeamEventStore()
    store.append(task_id="t", tenant_id="a", workspace_id="w",
        event_type="created", actor_id="coordinator", payload={})
    with pytest.raises(ValueError, match="compare_and_swap"):
        store.append(task_id="t", tenant_id="a", workspace_id="w",
            event_type="second", actor_id="coordinator", payload={},
            expected_previous_hash="stale")


def test_event_scope_isolation():
    store = TeamEventStore()
    store.append(task_id="t", tenant_id="a", workspace_id="w",
        event_type="created", actor_id="coordinator", payload={})
    assert store.list_events(task_id="t", tenant_id="b", workspace_id="w") == []
    assert store.verify_chain(task_id="t", tenant_id="b", workspace_id="w")


def test_event_payload_must_be_object():
    store = TeamEventStore()
    with pytest.raises(ValueError, match="payload_must_be_object"):
        store.append(task_id="t", tenant_id="a", workspace_id="w",
            event_type="created", actor_id="coordinator", payload=[])
