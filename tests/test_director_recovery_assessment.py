import pytest

from services.director import Director, create_app
from services.director.models import Authority, Risk, Sector, Status, Task


def task(task_id, status=Status.PLANNED, depends_on=None):
    return Task(
        id=task_id, sector=Sector.SELF, description=task_id,
        capability="self_diagnostic", authority=Authority.SUGGEST,
        risk=Risk.LOW, status=status, depends_on=list(depends_on or []),
    )


def test_interrupted_execution_requires_reconciliation_and_never_auto_retries():
    director = Director(supabase_client=None)
    director.tasks["task-1"] = task("task-1", Status.FLAGGED)

    result = director.assess_task_recovery("task-1")

    assert result["recovery_category"] == "reconcile_interrupted_execution"
    assert result["automatic_action_taken"] is False
    assert result["automatic_retry_allowed"] is False
    assert result["requires_human_review"] is True


def test_blocked_task_reports_missing_and_unsuccessful_prerequisites():
    director = Director(supabase_client=None)
    director.tasks["failed"] = task("failed", Status.INEFFECTIVE)
    director.tasks["blocked"] = task("blocked", Status.BLOCKED, ["failed", "missing"])

    result = director.assess_task_recovery("blocked")

    assert result["recovery_category"] == "resolve_dependencies"
    assert result["missing_dependencies"] == ["missing"]
    assert result["unsuccessful_dependencies"] == ["failed"]
    assert result["automatic_retry_allowed"] is False


def test_completed_task_does_not_receive_unnecessary_recovery_action():
    director = Director(supabase_client=None)
    director.tasks["done"] = task("done", Status.COMPLETED)

    result = director.assess_task_recovery("done")

    assert result["recovery_category"] == "no_recovery_needed"
    assert result["requires_human_review"] is False


def test_recovery_assessment_requires_authentication_and_is_read_only(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("MASTER_API_TOKEN", "recovery-test-token")
    director = Director(supabase_client=None)
    director.tasks["task-1"] = task("task-1", Status.FAILED)
    client = TestClient(create_app(director))

    assert client.get("/tasks/task-1/recovery-assessment").status_code == 401
    response = client.get(
        "/tasks/task-1/recovery-assessment",
        headers={"Authorization": "Bearer recovery-test-token"},
    )
    assert response.status_code == 200
    assert response.json()["recovery_category"] == "review_failed_execution"
    assert response.json()["automatic_action_taken"] is False
    assert director.tasks["task-1"].status == Status.FAILED


def test_recovery_assessment_does_not_disclose_cross_workspace_dependency(monkeypatch):
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from services.director.models import Proposal, ProposalStatus

    monkeypatch.setenv("MASTER_API_TOKEN", "recovery-test-token")
    director = Director(supabase_client=None)
    parent = task("parent", Status.FAILED, ["other-workspace"])
    parent.proposal_id = "proposal-a"
    other = task("other-workspace", Status.FAILED)
    other.proposal_id = "proposal-b"
    director.tasks[parent.id] = parent
    director.tasks[other.id] = other
    director.proposals["proposal-a"] = SimpleNamespace(workspace_id="workspace-a")
    director.proposals["proposal-b"] = SimpleNamespace(workspace_id="workspace-b")
    app = create_app(director)

    @app.middleware("http")
    async def trusted_workspace_context(request, call_next):
        request.state.request_context = SimpleNamespace(workspace_id="workspace-a", species_privileges=False)
        request.state.auth_identity = SimpleNamespace(valid=True)
        return await call_next(request)

    client = TestClient(app)
    response = client.get(
        "/tasks/parent/recovery-assessment",
        headers={"Authorization": "Bearer recovery-test-token"},
    )
    assert response.status_code == 404
