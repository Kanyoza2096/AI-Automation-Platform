import pytest

from services.kdo_engineering_vertical_contracts import (
    EngineeringTaskContract,
    EngineeringTaskStatus,
    transition_engineering_task,
)


def make_task(**overrides):
    values = dict(
        task_id="eng-task-1",
        tenant_id="tenant-1",
        workspace_id="workspace-1",
        objective="Fix a reproducible defect",
        acceptance_criteria=("Regression test passes", "No unrelated changes"),
        idempotency_key="eng-idem-1",
    )
    values.update(overrides)
    return EngineeringTaskContract(**values)


def transition(task, status, **kwargs):
    return transition_engineering_task(
        task, status,
        tenant_id=kwargs.pop("tenant_id", task.tenant_id),
        workspace_id=kwargs.pop("workspace_id", task.workspace_id),
        **kwargs,
    )


def test_task_requires_scope_objective_acceptance_and_idempotency():
    with pytest.raises(ValueError, match="invalid_task"):
        make_task(workspace_id="")
    with pytest.raises(ValueError, match="invalid_task"):
        make_task(acceptance_criteria=())


def test_contract_cannot_claim_execution_authority():
    with pytest.raises(ValueError, match="cannot_grant_execution_authority"):
        make_task(authority_granted=True)


def test_valid_lifecycle_transition_increments_revision():
    task = make_task()
    planned = transition(task, EngineeringTaskStatus.PLANNED)
    queued = transition(planned, EngineeringTaskStatus.QUEUED)
    assert (planned.revision, queued.revision) == (2, 3)


def test_invalid_transition_is_rejected():
    with pytest.raises(ValueError, match="invalid_transition"):
        transition(make_task(), EngineeringTaskStatus.COMPLETED)


def test_cross_workspace_transition_is_rejected():
    with pytest.raises(ValueError, match="tenant_workspace_mismatch"):
        transition(make_task(), EngineeringTaskStatus.PLANNED, workspace_id="other")


def test_completion_requires_test_evidence_and_independent_review():
    task = make_task(status=EngineeringTaskStatus.AWAITING_REVIEW)
    with pytest.raises(ValueError, match="completion_evidence_required"):
        transition(task, EngineeringTaskStatus.COMPLETED, evidence={"tests_passed": True})
    with pytest.raises(ValueError, match="review_not_approved"):
        transition(task, EngineeringTaskStatus.COMPLETED, evidence={"tests_passed": True, "test_run_id": "run-1"})
    completed = transition(task, EngineeringTaskStatus.COMPLETED, evidence={
        "tests_passed": True,
        "test_run_id": "run-1",
        "review_status": "approved",
        "review_id": "review-1",
    })
    assert completed.status == EngineeringTaskStatus.COMPLETED
    assert completed.revision == task.revision + 1


def test_terminal_task_cannot_be_reopened():
    task = make_task(status=EngineeringTaskStatus.COMPLETED)
    with pytest.raises(ValueError, match="invalid_transition"):
        transition(task, EngineeringTaskStatus.RUNNING)


def test_fingerprint_is_stable_and_changes_with_lifecycle():
    task = make_task()
    assert task.fingerprint() == make_task().fingerprint()
    planned = transition(task, EngineeringTaskStatus.PLANNED)
    assert planned.fingerprint() != task.fingerprint()
