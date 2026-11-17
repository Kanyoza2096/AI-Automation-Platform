"""Isolated, deterministic acceptance harness for Director outcome verification.

These tests use synthetic in-process observations and a recording store only.
They never connect to Supabase or mutate live Director records.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from services.director import Director, create_app
from services.director.models import (
    Authority, Health, Observation, Risk, Sector, Status, Task,
)
from services.director.stores import InMemoryStore


def make_task(task_id="harness-platform-task", *, workspace_id=None):
    return Task(
        id=task_id,
        sector=Sector.BRANDS,
        description="Isolated outcome verification harness",
        capability="self_diagnostic",
        authority=Authority.SUGGEST,
        risk=Risk.LOW,
        status=Status.COMPLETED,
        result={"ok": True},
        success_criteria=[{
            "observer": "harness_observer",
            "metric_path": "$status",
            "operator": "equals",
            "expected": Health.NOMINAL.value,
        }],
        workspace_id=workspace_id,
    )


def observe(status):
    return Observation(
        source="isolated-verification-harness",
        sector=Sector.BRANDS,
        status=status,
        metrics={},
        timestamp=datetime.now(timezone.utc),
    )


class RecordingDurableLearningStore(InMemoryStore):
    """Deterministic durable-store stand-in with idempotent upsert semantics."""

    def __init__(self):
        super().__init__()
        self.learning_rows = {}

    async def save_learning(self, entry):
        assert entry["verification_status"] == "verified"
        assert entry["context"]["status"] == Status.VERIFIED.value
        self.learning_rows[entry["id"]] = dict(entry)
        self.learnings = list(self.learning_rows.values())


@pytest.mark.asyncio
async def test_isolated_degraded_to_verified_lifecycle_persists_one_learning_record():
    director = Director(supabase_client=None)
    store = RecordingDurableLearningStore()
    director.store = store
    task = make_task()
    director.tasks[task.id] = task

    director.observations["harness_observer"] = observe(Health.DEGRADED)
    ineffective = await director.evaluate_task_success_criteria(task.id)
    assert ineffective["outcome"] == "ineffective"
    assert task.status == Status.INEFFECTIVE
    assert store.learning_rows == {}

    # Recovery is represented only by a new synthetic observer snapshot.
    task.status = Status.COMPLETED
    director.observations["harness_observer"] = observe(Health.NOMINAL)
    verified = await director.evaluate_task_success_criteria(task.id)
    assert verified["outcome"] == "verified"
    assert task.status == Status.VERIFIED
    assert len(store.learning_rows) == 1

    # Re-verification is idempotent, including the durable row identity.
    learning_id = next(iter(store.learning_rows))
    repeated = await director.evaluate_task_success_criteria(task.id)
    assert repeated["outcome"] == "verified"
    assert len(store.learning_rows) == 1
    assert next(iter(store.learning_rows)) == learning_id
    row = store.learning_rows[learning_id]
    assert row["task_id"] == task.id
    assert row["verification_status"] == "verified"


def make_scoped_test_app(monkeypatch, director):
    """Attach explicit test identities; production auth middleware is untouched."""
    monkeypatch.setenv("MASTER_API_TOKEN", "isolated-harness-token")
    app = create_app(director)

    @app.middleware("http")
    async def inject_test_identity(request, call_next):
        scope = request.headers.get("X-Harness-Scope", "platform")
        if scope == "workspace":
            request.state.request_context = SimpleNamespace(
                workspace_id=request.headers.get("X-Harness-Workspace", "workspace-a"),
                species_privileges=False,
                role="owner",
                principal_id="harness-workspace-owner",
            )
        else:
            request.state.request_context = SimpleNamespace(
                workspace_id=None,
                species_privileges=True,
                role="owner",
                principal_id="harness-platform-super-admin",
            )
        request.state.auth_identity = SimpleNamespace(valid=True)
        return await call_next(request)

    return app


def test_platform_and_workspace_verification_authorization_are_isolated(monkeypatch):
    from fastapi.testclient import TestClient

    director = Director(supabase_client=None)
    platform_task = make_task("harness-platform-task")
    workspace_task = make_task("harness-workspace-task", workspace_id="workspace-a")
    director.tasks[platform_task.id] = platform_task
    director.tasks[workspace_task.id] = workspace_task
    director.observations["harness_observer"] = observe(Health.NOMINAL)

    client = TestClient(make_scoped_test_app(monkeypatch, director))
    platform_headers = {"Authorization": "Bearer isolated-harness-token"}
    workspace_headers = {
        **platform_headers,
        "X-Harness-Scope": "workspace",
        "X-Harness-Workspace": "workspace-a",
    }

    # Platform super-admin can verify platform tasks.
    response = client.post(
        f"/platform/tasks/{platform_task.id}/verify-outcome",
        headers=platform_headers,
    )
    assert response.status_code == 200
    assert response.json()["outcome"] == "verified"

    # A workspace owner cannot use the platform-only route.
    response = client.post(
        f"/platform/tasks/{platform_task.id}/verify-outcome",
        headers=workspace_headers,
    )
    assert response.status_code == 403

    # A workspace owner can verify only a task owned by that workspace.
    response = client.post(
        f"/tasks/{workspace_task.id}/verify-outcome",
        headers=workspace_headers,
    )
    assert response.status_code == 200

    # Neither side can cross the ownership boundary.
    response = client.post(
        f"/tasks/{platform_task.id}/verify-outcome",
        headers=workspace_headers,
    )
    assert response.status_code == 404

    response = client.post(
        f"/platform/tasks/{workspace_task.id}/verify-outcome",
        headers=platform_headers,
    )
    assert response.status_code == 404
