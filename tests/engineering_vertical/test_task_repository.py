import pytest
from services.kdo_engineering_task_repository import (
    EngineeringTaskConflict, EngineeringTaskNotFound, SQLiteEngineeringTaskRepository,
)
from services.kdo_engineering_vertical_contracts import EngineeringTaskContract, EngineeringTaskStatus


def task(**overrides):
    data = dict(task_id="task-1", tenant_id="tenant-1", workspace_id="workspace-1",
                objective="Add a health endpoint", acceptance_criteria=("endpoint returns status",),
                idempotency_key="request-1")
    data.update(overrides)
    return EngineeringTaskContract(**data)


def test_create_and_read_scoped_task():
    repo = SQLiteEngineeringTaskRepository()
    created = repo.create(task())
    assert repo.get("task-1", tenant_id="tenant-1", workspace_id="workspace-1") == created


def test_create_same_idempotency_and_same_payload_is_idempotent():
    repo = SQLiteEngineeringTaskRepository()
    first = repo.create(task())
    second = repo.create(task())
    assert first == second


def test_idempotency_key_cannot_be_reused_for_different_task():
    repo = SQLiteEngineeringTaskRepository()
    repo.create(task())
    with pytest.raises(EngineeringTaskConflict, match="idempotency_key_reused"):
        repo.create(task(task_id="task-2", objective="Different request"))


def test_other_tenant_cannot_read_task_or_learn_its_existence():
    repo = SQLiteEngineeringTaskRepository()
    repo.create(task())
    with pytest.raises(EngineeringTaskNotFound):
        repo.get("task-1", tenant_id="tenant-2", workspace_id="workspace-1")


def test_stale_revision_is_rejected():
    repo = SQLiteEngineeringTaskRepository()
    repo.create(task())
    repo.transition("task-1", EngineeringTaskStatus.PLANNED, tenant_id="tenant-1",
                    workspace_id="workspace-1", expected_revision=1)
    with pytest.raises(EngineeringTaskConflict, match="stale_revision"):
        repo.transition("task-1", EngineeringTaskStatus.PLANNED, tenant_id="tenant-1",
                        workspace_id="workspace-1", expected_revision=1)


def test_invalid_lifecycle_transition_does_not_mutate_row():
    repo = SQLiteEngineeringTaskRepository()
    repo.create(task())
    with pytest.raises(ValueError, match="invalid_transition"):
        repo.transition("task-1", EngineeringTaskStatus.COMPLETED, tenant_id="tenant-1",
                        workspace_id="workspace-1", expected_revision=1)
    assert repo.get("task-1", tenant_id="tenant-1", workspace_id="workspace-1").revision == 1


def test_completion_requires_test_and_independent_review_evidence():
    repo = SQLiteEngineeringTaskRepository()
    repo.create(task())
    repo.transition("task-1", EngineeringTaskStatus.PLANNED, tenant_id="tenant-1", workspace_id="workspace-1", expected_revision=1)
    repo.transition("task-1", EngineeringTaskStatus.QUEUED, tenant_id="tenant-1", workspace_id="workspace-1", expected_revision=2)
    repo.transition("task-1", EngineeringTaskStatus.RUNNING, tenant_id="tenant-1", workspace_id="workspace-1", expected_revision=3)
    repo.transition("task-1", EngineeringTaskStatus.AWAITING_REVIEW, tenant_id="tenant-1", workspace_id="workspace-1", expected_revision=4)
    with pytest.raises(ValueError, match="completion_evidence_required"):
        repo.transition("task-1", EngineeringTaskStatus.COMPLETED, tenant_id="tenant-1", workspace_id="workspace-1", expected_revision=5)


def test_evidence_backed_completion_persists():
    repo = SQLiteEngineeringTaskRepository()
    repo.create(task())
    statuses = [EngineeringTaskStatus.PLANNED, EngineeringTaskStatus.QUEUED, EngineeringTaskStatus.RUNNING, EngineeringTaskStatus.AWAITING_REVIEW]
    for revision, status in enumerate(statuses, start=1):
        repo.transition("task-1", status, tenant_id="tenant-1", workspace_id="workspace-1", expected_revision=revision)
    completed = repo.transition("task-1", EngineeringTaskStatus.COMPLETED, tenant_id="tenant-1", workspace_id="workspace-1", expected_revision=5,
        evidence={"tests_passed": True, "test_run_id": "run-123", "review_status": "approved", "review_id": "review-456"})
    assert completed.status is EngineeringTaskStatus.COMPLETED
    assert repo.get("task-1", tenant_id="tenant-1", workspace_id="workspace-1").evidence["test_run_id"] == "run-123"


def test_list_is_limited_to_exact_tenant_and_workspace():
    repo = SQLiteEngineeringTaskRepository()
    repo.create(task())
    repo.create(task(task_id="task-2", tenant_id="tenant-1", workspace_id="workspace-2", idempotency_key="request-2"))
    assert [x.task_id for x in repo.list_scope(tenant_id="tenant-1", workspace_id="workspace-1")] == ["task-1"]


def test_file_backed_repository_survives_close_and_reopen(tmp_path):
    db_path = tmp_path / "engineering.db"
    repo = SQLiteEngineeringTaskRepository(db_path)
    repo.create(task())
    repo.close()
    reopened = SQLiteEngineeringTaskRepository(db_path)
    restored = reopened.get("task-1", tenant_id="tenant-1", workspace_id="workspace-1")
    assert restored.objective == "Add a health endpoint"
    assert restored.revision == 1
    reopened.close()
