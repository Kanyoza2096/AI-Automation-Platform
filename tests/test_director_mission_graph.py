import pytest

from services.director import Director
from services.director.models import Authority, Risk, Sector, Status, Task


def _task(task_id, status=Status.PLANNED):
    return Task(
        id=task_id, sector=Sector.SELF, description=task_id,
        capability="self_diagnostic", authority=Authority.SUGGEST,
        risk=Risk.LOW, status=status,
    )


@pytest.mark.asyncio
async def test_mission_dependency_is_persisted_and_graph_exposes_edge():
    director = Director(supabase_client=None)
    director.tasks = {"a": _task("a"), "b": _task("b")}

    result = await director.add_task_dependency("b", "a")
    graph = director.mission_graph()

    assert result["changed"] is True
    assert director.tasks["b"].depends_on == ["a"]
    assert director.tasks["a"].mission_id == director.tasks["b"].mission_id
    assert graph["edges"] == [{"prerequisite": "a", "dependent": "b", "resolved": True}]
    assert await director.store.get_task("b") is not None


@pytest.mark.asyncio
async def test_mission_dependency_rejects_cycles_and_self_edges():
    director = Director(supabase_client=None)
    director.tasks = {"a": _task("a"), "b": _task("b"), "c": _task("c")}
    await director.add_task_dependency("b", "a")
    await director.add_task_dependency("c", "b")

    with pytest.raises(ValueError, match="cycle"):
        await director.add_task_dependency("a", "c")
    with pytest.raises(ValueError, match="itself"):
        await director.add_task_dependency("a", "a")


@pytest.mark.asyncio
async def test_scheduler_does_not_run_dependent_before_prerequisite_succeeds(monkeypatch):
    director = Director(supabase_client=None)
    prerequisite = _task("a")
    dependent = _task("b")
    dependent.depends_on = ["a"]
    director.tasks = {"a": prerequisite, "b": dependent}
    executed = []

    async def record(task_id):
        executed.append(task_id)

    monkeypatch.setattr(director, "_execute_with_semaphore", record)
    await director._execute_due_async()
    assert executed == ["a"]

    prerequisite.status = Status.COMPLETED
    await director._execute_due_async()
    assert executed == ["a", "b"]


@pytest.mark.asyncio
async def test_scheduler_blocks_dependent_when_prerequisite_fails(monkeypatch):
    director = Director(supabase_client=None)
    prerequisite = _task("a", Status.FAILED)
    dependent = _task("b")
    dependent.depends_on = ["a"]
    director.tasks = {"a": prerequisite, "b": dependent}
    executed = []

    async def record(task_id):
        executed.append(task_id)

    monkeypatch.setattr(director, "_execute_with_semaphore", record)
    await director._execute_due_async()

    assert executed == []
    assert dependent.status == Status.BLOCKED
    assert dependent.error == "mission_dependency_unavailable:a"


@pytest.mark.asyncio
async def test_mission_dependency_rejects_cross_mission_edge():
    director = Director(supabase_client=None)
    a, b = _task("a"), _task("b")
    a.mission_id, b.mission_id = "mission-a", "mission-b"
    director.tasks = {"a": a, "b": b}

    with pytest.raises(ValueError, match="different missions"):
        await director.add_task_dependency("b", "a")


@pytest.mark.asyncio
async def test_startup_restore_rebuilds_persisted_mission_graph():
    director = Director(supabase_client=None)
    prerequisite = _task("persisted-a", Status.COMPLETED)
    dependent = _task("persisted-b")
    dependent.depends_on = ["persisted-a"]
    prerequisite.mission_id = dependent.mission_id = "mission-persisted"
    await director.store.save_task(prerequisite)
    await director.store.save_task(dependent)

    restarted = Director(supabase_client=None)
    restarted.store = director.store
    result = await restarted._restore_tasks()

    assert result == {"restored": 2, "flagged_for_reconciliation": 0, "errors": 0}
    graph = restarted.mission_graph("mission-persisted")
    assert {node["task_id"] for node in graph["nodes"]} == {"persisted-a", "persisted-b"}
    assert graph["edges"] == [{"prerequisite": "persisted-a", "dependent": "persisted-b", "resolved": True}]


@pytest.mark.asyncio
async def test_restore_flags_in_flight_task_to_prevent_duplicate_side_effect():
    director = Director(supabase_client=None)
    in_flight = _task("ambiguous", Status.EXECUTING)
    await director.store.save_task(in_flight)

    restarted = Director(supabase_client=None)
    restarted.store = director.store
    result = await restarted._restore_tasks()

    restored = restarted.tasks["ambiguous"]
    assert result["flagged_for_reconciliation"] == 1
    assert restored.status == Status.FLAGGED
    assert restored.result["status"] == "reconciliation_required"
    persisted = await restarted.store.get_task("ambiguous")
    assert persisted["status"] == Status.FLAGGED.value
