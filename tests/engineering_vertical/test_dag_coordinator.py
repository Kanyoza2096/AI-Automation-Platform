from dataclasses import replace

import pytest

from services.kdo_engineering_agent_registry import AgentRegistry
from services.kdo_engineering_dag_coordinator import (
    EngineeringDagCoordinator,
    EngineeringDagError,
)
from services.kdo_engineering_task_repository import SQLiteEngineeringTaskRepository
from services.kdo_engineering_vertical_contracts import (
    EngineeringTaskContract,
    EngineeringTaskStatus,
)


def make_task(task_id, *, status=EngineeringTaskStatus.PLANNED, depends_on=(), role_id="backend_engineer"):
    return EngineeringTaskContract(
        task_id=task_id,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        objective=f"Work on {task_id}",
        acceptance_criteria=("deliver the assigned change",),
        idempotency_key=f"idem-{task_id}",
        status=status,
        metadata={"depends_on": list(depends_on), "role_id": role_id},
    )


def make_coordinator(tasks):
    repository = SQLiteEngineeringTaskRepository()
    for task in {item.task_id: item for item in tasks}.values():
        repository.create(task)
    registry = AgentRegistry()
    for role_id in ("backend_engineer", "test_engineer"):
        registry.register_adapter(role_id, lambda **kwargs: {"status": "not_invoked"})
    return EngineeringDagCoordinator(task_repository=repository, agent_registry=registry), repository


def test_queues_only_root_tasks_and_waits_for_dependencies():
    first = make_task("first")
    second = make_task("second", depends_on=("first",))
    coordinator, repository = make_coordinator([first, second])

    result = coordinator.reconcile([first, second], tenant_id="tenant-a", workspace_id="workspace-a")

    assert result["queued_task_ids"] == ["first"]
    assert result["waiting_task_ids"] == ["second"]
    assert result["execution_started"] is False
    assert repository.get("first", tenant_id="tenant-a", workspace_id="workspace-a").status == EngineeringTaskStatus.QUEUED


def test_queues_dependent_task_only_after_prerequisite_is_completed():
    first = make_task("first", status=EngineeringTaskStatus.COMPLETED)
    second = make_task("second", depends_on=("first",))
    coordinator, repository = make_coordinator([first, second])

    result = coordinator.reconcile([first, second], tenant_id="tenant-a", workspace_id="workspace-a")

    assert result["queued_task_ids"] == ["second"]
    assert result["waiting_task_ids"] == []
    assert repository.get("second", tenant_id="tenant-a", workspace_id="workspace-a").status == EngineeringTaskStatus.QUEUED


def test_blocks_descendant_when_prerequisite_failed():
    first = make_task("first", status=EngineeringTaskStatus.FAILED)
    second = make_task("second", depends_on=("first",))
    coordinator, repository = make_coordinator([first, second])

    result = coordinator.reconcile([first, second], tenant_id="tenant-a", workspace_id="workspace-a")

    assert result["blocked_task_ids"] == ["second"]
    stored = repository.get("second", tenant_id="tenant-a", workspace_id="workspace-a")
    assert stored.status == EngineeringTaskStatus.BLOCKED
    assert stored.evidence["dag_block"]["blocked_by"] == ["first"]


@pytest.mark.parametrize(
    "tasks,error",
    [
        ([make_task("a", depends_on=("missing",))], "unknown_dependencies"),
        ([make_task("a", depends_on=("b",)), make_task("b", depends_on=("a",))], "dependency_cycle_detected"),
        ([make_task("a"), make_task("a")], "duplicate_task_id"),
    ],
)
def test_rejects_invalid_graph_before_state_changes(tasks, error):
    coordinator, repository = make_coordinator(tasks)
    with pytest.raises(EngineeringDagError, match=error):
        coordinator.reconcile(tasks, tenant_id="tenant-a", workspace_id="workspace-a")
    assert all(
        repository.get(task.task_id, tenant_id=task.tenant_id, workspace_id=task.workspace_id).status
        == task.status
        for task in {item.task_id: item for item in tasks}.values()
    )


def test_does_not_queue_task_when_adapter_is_missing():
    task = make_task("a", role_id="frontend_engineer")
    coordinator, repository = make_coordinator([task])

    result = coordinator.reconcile([task], tenant_id="tenant-a", workspace_id="workspace-a")

    assert result["unavailable_task_ids"] == ["a"]
    assert repository.get("a", tenant_id="tenant-a", workspace_id="workspace-a").status == EngineeringTaskStatus.PLANNED


def test_rejects_cross_scope_graph():
    task = make_task("a")
    coordinator, _ = make_coordinator([task])
    with pytest.raises(EngineeringDagError, match="scope_mismatch"):
        coordinator.reconcile([replace(task, workspace_id="other-workspace")],
                              tenant_id="tenant-a", workspace_id="other-workspace")


def test_rejects_stale_task_revision():
    task = make_task("a")
    coordinator, repository = make_coordinator([task])
    repository.transition(task.task_id, EngineeringTaskStatus.QUEUED,
                          tenant_id=task.tenant_id, workspace_id=task.workspace_id,
                          expected_revision=task.revision)
    with pytest.raises(Exception, match="stale_supplied_task_revision"):
        coordinator.reconcile([task], tenant_id=task.tenant_id, workspace_id=task.workspace_id)
