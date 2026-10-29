import threading
import time
import pytest
from services.kdo_engineering_scheduler import ScheduledTask, SchedulerError, run_dependency_graph, send_agent_message
from services.kdo_engineering_team_events import TeamEventStore


def test_independent_tasks_can_overlap_and_dependency_receives_output():
    lock = threading.Lock(); active = 0; max_active = 0
    def work(value):
        def run(inputs):
            nonlocal active, max_active
            with lock:
                active += 1; max_active = max(max_active, active)
            time.sleep(0.08)
            with lock: active -= 1
            return value
        return run
    result = run_dependency_graph([
        ScheduledTask("a", (), work("A")), ScheduledTask("b", (), work("B")),
        ScheduledTask("c", ("a", "b"), lambda inputs: inputs["a"] + inputs["b"]),
    ], max_workers=2)
    assert max_active == 2
    assert result["c"]["output"] == "AB"


def test_failed_dependency_blocks_descendants():
    result = run_dependency_graph([
        ScheduledTask("a", (), lambda _: (_ for _ in ()).throw(RuntimeError("boom"))),
        ScheduledTask("b", ("a",), lambda _: "must not run"),
    ])
    assert result["a"]["status"] == "failed"
    assert result["b"]["status"] == "blocked"


def test_cycle_is_rejected_before_work_starts():
    with pytest.raises(SchedulerError, match="cycle"):
        run_dependency_graph([ScheduledTask("a", ("b",), lambda _: 1), ScheduledTask("b", ("a",), lambda _: 2)])


def test_agent_message_is_scoped_and_not_authority():
    store = TeamEventStore()
    event = send_agent_message(event_store=store, task_id="t", tenant_id="tenant", workspace_id="w",
        sender_agent_id="planner", recipient_agent_id="implementer", message_type="handoff", body={"plan":"x"})
    assert event["payload"]["authority"] == "coordination_only"
    assert store.verify_chain(task_id="t", tenant_id="tenant", workspace_id="w")
