from datetime import datetime, timedelta, timezone

import pytest

from services.director import Director
from services.director.models import Authority, Health, Observation, Risk, Sector, Status, Task


def make_task(criteria, status=Status.COMPLETED):
    return Task(
        id="task-criteria", sector=Sector.BRANDS, description="Verify brand outcome",
        capability="self_diagnostic", authority=Authority.SUGGEST, risk=Risk.LOW,
        status=status, success_criteria=criteria, result={"ok": True},
    )


def make_observation(metrics, *, age_seconds=0, status=Health.NOMINAL):
    return Observation(
        source="test-authoritative-observer", sector=Sector.BRANDS, status=status,
        metrics=metrics, timestamp=datetime.now(timezone.utc) - timedelta(seconds=age_seconds),
    )


@pytest.mark.asyncio
async def test_structured_success_criteria_verifies_only_from_fresh_healthy_observation():
    director = Director(supabase_client=None)
    task = make_task([{
        "observer": "domain", "metric_path": "brands.brand-1.active", "operator": "equals", "expected": True,
    }])
    director.tasks[task.id] = task
    director.observations["domain"] = make_observation({"brands": {"brand-1": {"active": True}}})

    result = await director.evaluate_task_success_criteria(task.id)

    assert result["outcome"] == "verified"
    assert task.status == Status.VERIFIED
    assert result["criteria"][0]["outcome"] == "met"
    assert (await director.store.get_task(task.id))["status"] == Status.VERIFIED.value


@pytest.mark.asyncio
async def test_recovery_status_criterion_can_verify_recovery_or_detect_persistent_failure():
    director = Director(supabase_client=None)
    task = make_task([{
        "observer": "event_bus", "metric_path": "$status",
        "operator": "equals", "expected": Health.NOMINAL.value,
    }])
    director.tasks[task.id] = task
    director.observations["event_bus"] = make_observation({}, status=Health.DEGRADED)

    failed_result = await director.evaluate_task_success_criteria(task.id)
    assert failed_result["outcome"] == "ineffective"
    assert failed_result["criteria"][0]["observed_value"] == Health.DEGRADED.value

    task.status = Status.COMPLETED
    director.observations["event_bus"] = make_observation({}, status=Health.NOMINAL)
    verified_result = await director.evaluate_task_success_criteria(task.id)
    assert verified_result["outcome"] == "verified"
    assert task.status == Status.VERIFIED


@pytest.mark.asyncio
async def test_mismatched_evidence_marks_completed_task_ineffective():
    director = Director(supabase_client=None)
    task = make_task([{
        "observer": "domain", "metric_path": "worker_pools.content.worker_count", "operator": "gte", "expected": 4,
    }])
    director.tasks[task.id] = task
    director.observations["domain"] = make_observation({"worker_pools": {"content": {"worker_count": 2}}})

    result = await director.evaluate_task_success_criteria(task.id)

    assert result["outcome"] == "ineffective"
    assert task.status == Status.INEFFECTIVE
    assert task.result["outcome_verification"]["outcome"] == "ineffective"


@pytest.mark.asyncio
@pytest.mark.parametrize("observation", [
    None,
    "stale",
    "unknown_health",
])
async def test_missing_stale_or_unknown_evidence_stays_unknown(observation):
    director = Director(supabase_client=None)
    task = make_task([{
        "observer": "domain", "metric_path": "value", "operator": "equals", "expected": 1,
    }])
    director.tasks[task.id] = task
    if observation == "stale":
        director.observations["domain"] = make_observation({"value": 1}, age_seconds=301)
    elif observation == "unknown_health":
        director.observations["domain"] = make_observation({"value": 1}, status=Health.UNKNOWN)

    result = await director.evaluate_task_success_criteria(task.id)

    assert result["outcome"] == "unknown"
    assert task.status == Status.COMPLETED
    assert "outcome_verification" not in task.result


@pytest.mark.asyncio
async def test_free_text_criteria_are_not_guessed_and_unexecuted_task_is_not_completed():
    director = Director(supabase_client=None)
    task = make_task(["Brand engagement improves"], status=Status.PLANNED)
    director.tasks[task.id] = task

    result = await director.evaluate_task_success_criteria(task.id)

    assert result["outcome"] == "unknown"
    assert task.status == Status.PLANNED


@pytest.mark.asyncio
async def test_missing_metric_and_type_mismatch_are_unknown_not_false():
    director = Director(supabase_client=None)
    task = make_task([
        {"observer": "domain", "metric_path": "missing.value", "operator": "equals", "expected": 1},
        {"observer": "domain", "metric_path": "value", "operator": "gt", "expected": "not-a-number"},
    ])
    director.tasks[task.id] = task
    director.observations["domain"] = make_observation({"value": 2})

    result = await director.evaluate_task_success_criteria(task.id)

    assert result["outcome"] == "unknown"
    assert [item["reason"] for item in result["criteria"]] == ["metric_missing", "incompatible_metric_type"]


def test_authenticated_api_exposes_outcome_reconciliation(monkeypatch):
    from fastapi.testclient import TestClient
    from services.director import create_app

    monkeypatch.setenv("MASTER_API_TOKEN", "master-test-token")
    director = Director(supabase_client=None)
    task = make_task([{
        "observer": "domain", "metric_path": "value", "operator": "equals", "expected": 7,
    }])
    director.tasks[task.id] = task
    director.observations["domain"] = make_observation({"value": 7})
    client = TestClient(create_app(director))
    headers = {"Authorization": "Bearer master-test-token"}

    response = client.post(f"/tasks/{task.id}/verify-outcome", headers=headers)
    assert response.status_code == 200
    assert response.json()["outcome"] == "verified"
    assert response.json()["task_status"] == "verified"

    invalid_window = client.post(
        f"/tasks/{task.id}/verify-outcome?max_age_seconds=0", headers=headers,
    )
    assert invalid_window.status_code == 400


def test_outcome_reconciliation_api_requires_authentication(monkeypatch):
    from fastapi.testclient import TestClient
    from services.director import create_app

    monkeypatch.setenv("MASTER_API_TOKEN", "master-test-token")
    director = Director(supabase_client=None)
    director.tasks["task-criteria"] = make_task(["unstructured"])
    client = TestClient(create_app(director))

    response = client.post("/tasks/task-criteria/verify-outcome")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_verified_learning_is_not_marked_recorded_when_persistence_is_unacknowledged(monkeypatch):
    director = Director(supabase_client=None)
    task = make_task([{
        "observer": "event_bus", "metric_path": "$status",
        "operator": "equals", "expected": Health.NOMINAL.value,
    }])
    task.status = Status.VERIFIED
    director.tasks[task.id] = task

    async def reject_persistence(entry):
        return False

    monkeypatch.setattr(director.store, "save_learning", reject_persistence)
    recorded = await director._record_verified_learning(task)

    assert recorded is False
    assert task.id not in director._learning_recorded_task_ids
    assert len(director.store.learnings) == 0


async def test_post_completion_verification_emits_learning_once_only_after_verified_outcome():
    director = Director(supabase_client=None)
    task = make_task([{
        "observer": "event_bus", "metric_path": "$status",
        "operator": "equals", "expected": Health.NOMINAL.value,
    }])
    director.tasks[task.id] = task

    director.observations["event_bus"] = make_observation({}, status=Health.DEGRADED)
    ineffective = await director.evaluate_task_success_criteria(task.id)
    assert ineffective["outcome"] == "ineffective"
    assert len(director.store.learnings) == 0

    task.status = Status.COMPLETED
    director.observations["event_bus"] = make_observation({}, status=Health.NOMINAL)
    verified = await director.evaluate_task_success_criteria(task.id)
    assert verified["outcome"] == "verified"
    assert task.status == Status.VERIFIED
    assert len(director.store.learnings) == 1
    assert director.store.learnings[0]["task_id"] == task.id
    assert director.store.learnings[0]["verification_status"] == "verified"
    assert director.store.learnings[0]["context"]["status"] == Status.VERIFIED.value

    # A repeated verification must not train the same task twice.
    repeated = await director.evaluate_task_success_criteria(task.id)
    assert repeated["outcome"] == "verified"
    assert len(director.store.learnings) == 1


def _install_platform_context(app):
    from types import SimpleNamespace

    @app.middleware("http")
    async def platform_context(request, call_next):
        request.state.request_context = SimpleNamespace(
            workspace_id="admin-home-workspace",
            principal_id="platform-test-super-admin",
            role="owner",
            species_privileges=False,
            platform_role="super_admin",
        )
        request.state.auth_identity = SimpleNamespace(valid=True, is_species=False, platform_role="super_admin")
        return await call_next(request)


def _install_tenant_context(app, workspace_id="workspace-test", role="owner"):
    from types import SimpleNamespace

    @app.middleware("http")
    async def tenant_context(request, call_next):
        request.state.request_context = SimpleNamespace(
            workspace_id=workspace_id,
            principal_id="tenant-test-owner",
            role=role,
            species_privileges=False,
            platform_role="",
        )
        request.state.auth_identity = SimpleNamespace(valid=True, is_species=False, platform_role="")
        return await call_next(request)


def test_platform_verification_api_enforces_platform_scope_and_task_ownership(monkeypatch):
    from fastapi.testclient import TestClient
    from services.director import create_app

    monkeypatch.setenv("MASTER_API_TOKEN", "platform-harness-token")
    director = Director(supabase_client=None)
    platform_task = make_task([{
        "observer": "event_bus", "metric_path": "$status",
        "operator": "equals", "expected": Health.NOMINAL.value,
    }])
    platform_task.id = "platform-harness-task"
    director.tasks[platform_task.id] = platform_task

    workspace_task = make_task([{
        "observer": "event_bus", "metric_path": "$status",
        "operator": "equals", "expected": Health.NOMINAL.value,
    }])
    workspace_task.id = "workspace-harness-task"
    workspace_task.workspace_id = "workspace-test"
    workspace_task.scope_type = "workspace"
    director.tasks[workspace_task.id] = workspace_task

    tenant_app = create_app(director)
    _install_tenant_context(tenant_app)
    tenant_client = TestClient(tenant_app)
    headers = {"Authorization": "Bearer platform-harness-token"}

    # A workspace owner must not use the platform-only endpoint.
    denied = tenant_client.post(
        f"/platform/tasks/{platform_task.id}/verify-outcome", headers=headers,
    )
    assert denied.status_code == 403

    platform_app = create_app(director)
    _install_platform_context(platform_app)
    platform_client = TestClient(platform_app)

    # Platform authority still cannot use this endpoint to verify tenant data.
    hidden_workspace_task = platform_client.post(
        f"/platform/tasks/{workspace_task.id}/verify-outcome", headers=headers,
    )
    assert hidden_workspace_task.status_code == 404

    # Workspace verification remains available for the matching tenant task.
    director.observations["event_bus"] = make_observation({}, status=Health.NOMINAL)
    tenant_verified = tenant_client.post(
        f"/tasks/{workspace_task.id}/verify-outcome", headers=headers,
    )
    assert tenant_verified.status_code == 200
    assert tenant_verified.json()["outcome"] == "verified"
    assert workspace_task.status == Status.VERIFIED

    # Tenant endpoint also does not expose a platform-scoped task.
    hidden_platform_task = tenant_client.post(
        f"/tasks/{platform_task.id}/verify-outcome", headers=headers,
    )
    assert hidden_platform_task.status_code == 404


def test_platform_verification_harness_runs_ineffective_to_verified_and_deduplicates_learning(monkeypatch):
    from fastapi.testclient import TestClient
    from services.director import create_app

    monkeypatch.setenv("MASTER_API_TOKEN", "platform-harness-token")
    director = Director(supabase_client=None)
    task = make_task([{
        "observer": "event_bus", "metric_path": "$status",
        "operator": "equals", "expected": Health.NOMINAL.value,
    }])
    task.id = "platform-lifecycle-harness"
    director.tasks[task.id] = task

    app = create_app(director)
    _install_platform_context(app)
    client = TestClient(app)
    headers = {"Authorization": "Bearer platform-harness-token"}
    endpoint = f"/platform/tasks/{task.id}/verify-outcome"

    director.observations["event_bus"] = make_observation({}, status=Health.DEGRADED)
    ineffective = client.post(endpoint, headers=headers)
    assert ineffective.status_code == 200
    assert ineffective.json()["outcome"] == "ineffective"
    assert ineffective.json()["task_status"] == Status.INEFFECTIVE.value
    assert len(director.store.learnings) == 0

    # A fresh nominal observation allows verification; no task execution occurs.
    task.status = Status.COMPLETED
    director.observations["event_bus"] = make_observation({}, status=Health.NOMINAL)
    verified = client.post(endpoint, headers=headers)
    assert verified.status_code == 200
    assert verified.json()["outcome"] == "verified"
    assert verified.json()["task_status"] == Status.VERIFIED.value
    assert len(director.store.learnings) == 1
    learning = director.store.learnings[0]
    assert learning["task_id"] == task.id
    assert learning["verification_status"] == "verified"
    assert learning["context"]["status"] == Status.VERIFIED.value

    repeated = client.post(endpoint, headers=headers)
    assert repeated.status_code == 200
    assert repeated.json()["outcome"] == "verified"
    assert len(director.store.learnings) == 1
    assert task.status == Status.VERIFIED
