from datetime import datetime, timedelta, timezone

import pytest

from services.director import Director
from services.director.models import Authority, Health, Observation, Proposal, ProposalStatus, Risk, Sector, Status, Task


def make_task(criteria, status=Status.COMPLETED, *, workspace_id=None):
    return Task(
        id="task-criteria", sector=Sector.BRANDS, description="Verify brand outcome",
        capability="self_diagnostic", authority=Authority.SUGGEST, risk=Risk.LOW,
        status=status, success_criteria=criteria, result={"ok": True},
        workspace_id=workspace_id,
    )

@pytest.mark.asyncio
async def test_platform_scheduler_skips_workspace_owned_task_in_legacy_registry():
    director = Director(supabase_client=None)
    task = make_task([], status=Status.APPROVED, workspace_id="workspace-a")
    director.tasks[task.id] = task

    await director._execute_due_async()

    assert task.status == Status.APPROVED
    assert task.executed_at is None


@pytest.mark.asyncio
async def test_direct_platform_executor_refuses_workspace_owned_task():
    director = Director(supabase_client=None)
    task = make_task([], status=Status.APPROVED, workspace_id="workspace-a")
    director.tasks[task.id] = task

    returned = await director._execute_async(task.id)

    assert returned is task
    assert task.status == Status.APPROVED
    assert task.executed_at is None



def make_observation(metrics, *, age_seconds=0, status=Health.NOMINAL, workspace_id=None):
    return Observation(
        source="test-authoritative-observer", sector=Sector.BRANDS, status=status,
        metrics=metrics, timestamp=datetime.now(timezone.utc) - timedelta(seconds=age_seconds),
        workspace_id=workspace_id,
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
    assert len(director.learning_loop.signals) == 0
    assert director.learning_loop.capability_stats == {}
    assert director.learning_loop.context_stats == {}
    assert director.learning_loop.time_stats == {}
    assert director.learning_loop.duration_stats == {}
    assert director.learning_loop.verification_stats == {}


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
    director.workspace_tasks.setdefault("workspace-test", {})[workspace_task.id] = workspace_task

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
    director.workspace_observations.setdefault("workspace-test", {})["event_bus"] = make_observation(
        {}, status=Health.NOMINAL, workspace_id="workspace-test",
    )
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


def test_mission_graph_isolated_by_workspace_and_platform_scope():
    director = Director(supabase_client=None)
    platform_task = make_task([], status=Status.APPROVED)
    platform_task.id = "platform-task"
    platform_task.mission_id = "shared-mission"
    workspace_a_task = make_task([], status=Status.APPROVAL, workspace_id="workspace-a")
    workspace_a_task.id = "workspace-a-task"
    workspace_a_task.mission_id = "shared-mission"
    workspace_b_task = make_task([], status=Status.APPROVAL, workspace_id="workspace-b")
    workspace_b_task.id = "workspace-b-task"
    workspace_b_task.mission_id = "shared-mission"
    director.tasks[platform_task.id] = platform_task
    director.workspace_tasks["workspace-a"] = {workspace_a_task.id: workspace_a_task}
    director.workspace_tasks["workspace-b"] = {workspace_b_task.id: workspace_b_task}

    platform_graph = director.mission_graph(mission_id="shared-mission")
    workspace_a_graph = director.mission_graph(
        mission_id="shared-mission", workspace_id="workspace-a",
    )
    workspace_b_graph = director.mission_graph(
        mission_id="shared-mission", workspace_id="workspace-b",
    )

    assert {node["task_id"] for node in platform_graph["nodes"]} == {"platform-task"}
    assert {node["task_id"] for node in workspace_a_graph["nodes"]} == {"workspace-a-task"}
    assert {node["task_id"] for node in workspace_b_graph["nodes"]} == {"workspace-b-task"}


@pytest.mark.asyncio
async def test_dependency_rejects_cross_workspace_and_platform_edges():
    director = Director(supabase_client=None)
    workspace_a_task = make_task([], status=Status.APPROVAL, workspace_id="workspace-a")
    workspace_a_task.id = "workspace-a-task"
    workspace_b_task = make_task([], status=Status.APPROVAL, workspace_id="workspace-b")
    workspace_b_task.id = "workspace-b-task"
    platform_task = make_task([], status=Status.APPROVAL)
    platform_task.id = "platform-task"
    director.workspace_tasks["workspace-a"] = {workspace_a_task.id: workspace_a_task}
    director.workspace_tasks["workspace-b"] = {workspace_b_task.id: workspace_b_task}
    director.tasks[platform_task.id] = platform_task

    # Out-of-scope IDs are indistinguishable from missing tasks to the caller.
    with pytest.raises(KeyError):
        await director.add_task_dependency("workspace-a-task", "workspace-b-task", workspace_id="workspace-a")
    with pytest.raises(KeyError):
        await director.add_task_dependency("workspace-a-task", "platform-task", workspace_id="workspace-a")


def test_recovery_assessment_hides_cross_workspace_dependency_state():
    director = Director(supabase_client=None)
    task = make_task([], status=Status.BLOCKED, workspace_id="workspace-a")
    task.id = "workspace-a-blocked"
    task.depends_on = ["workspace-b-secret"]
    other_workspace_task = make_task([], status=Status.FAILED, workspace_id="workspace-b")
    other_workspace_task.id = "workspace-b-secret"
    director.workspace_tasks["workspace-a"] = {task.id: task}
    director.workspace_tasks["workspace-b"] = {other_workspace_task.id: other_workspace_task}

    assessment = director.assess_task_recovery(
        task.id, workspace_id="workspace-a",
    )

    assert assessment["dependencies"] == [
        {"task_id": "workspace-b-secret", "status": "missing"},
    ]
    assert all(item["status"] != Status.FAILED.value for item in assessment["dependencies"])


def test_platform_recovery_cannot_read_workspace_task():
    director = Director(supabase_client=None)
    task = make_task([], status=Status.BLOCKED, workspace_id="workspace-a")
    director.workspace_tasks["workspace-a"] = {task.id: task}

    with pytest.raises(KeyError, match="task not found"):
        director.assess_task_recovery(task.id)


def test_approval_mutations_require_exact_task_ownership_scope():
    director = Director(supabase_client=None)
    workspace_a = make_task([], status=Status.APPROVAL, workspace_id="workspace-a")
    workspace_a.id = "approval-workspace-a"
    workspace_b = make_task([], status=Status.APPROVAL, workspace_id="workspace-b")
    workspace_b.id = "approval-workspace-b"
    platform = make_task([], status=Status.APPROVAL)
    platform.id = "approval-platform"
    director.workspace_tasks["workspace-a"] = {workspace_a.id: workspace_a}
    director.workspace_tasks["workspace-b"] = {workspace_b.id: workspace_b}
    director.tasks[platform.id] = platform

    with pytest.raises(KeyError, match="not found"):
        director.approve(workspace_a.id)
    with pytest.raises(KeyError, match="not found"):
        director.approve(workspace_b.id, workspace_id="workspace-a")
    with pytest.raises(KeyError, match="not found"):
        director.reject(workspace_a.id, workspace_id="workspace-b")

    approved = director.approve(workspace_a.id, workspace_id="workspace-a")
    rejected = director.reject(workspace_b.id, workspace_id="workspace-b", reason="not approved")
    platform_approved = director.approve(platform.id)

    assert approved.status == Status.APPROVED
    assert rejected.status == Status.CANCELLED
    assert platform_approved.status == Status.APPROVED


def test_approval_api_uses_authenticated_workspace_and_platform_scope(monkeypatch):
    from fastapi.testclient import TestClient
    from services.director import create_app

    monkeypatch.setenv("MASTER_API_TOKEN", "approval-scope-token")
    director = Director(supabase_client=None)
    workspace_a = make_task([], status=Status.APPROVAL, workspace_id="workspace-a")
    workspace_a.id = "approval-api-a"
    workspace_b = make_task([], status=Status.APPROVAL, workspace_id="workspace-b")
    workspace_b.id = "approval-api-b"
    platform = make_task([], status=Status.APPROVAL)
    platform.id = "approval-api-platform"
    director.workspace_tasks["workspace-a"] = {workspace_a.id: workspace_a}
    director.workspace_tasks["workspace-b"] = {workspace_b.id: workspace_b}
    director.tasks[platform.id] = platform
    headers = {"Authorization": "Bearer approval-scope-token"}

    tenant_app = create_app(director)
    _install_tenant_context(tenant_app, workspace_id="workspace-a")
    tenant_client = TestClient(tenant_app)
    assert tenant_client.post(
        f"/tasks/{workspace_b.id}/approve", headers=headers,
    ).status_code == 404
    approved = tenant_client.post(
        f"/tasks/{workspace_a.id}/approve", headers=headers,
    )
    assert approved.status_code == 200
    assert workspace_a.status == Status.APPROVED

    platform_app = create_app(director)
    _install_platform_context(platform_app)
    platform_client = TestClient(platform_app)
    platform_approved = platform_client.post(
        f"/tasks/{platform.id}/approve", headers=headers,
    )
    assert platform_approved.status_code == 200
    assert platform.status == Status.APPROVED


def test_workspace_task_creation_uses_separate_registry_and_deduplicates_per_workspace():
    director = Director(supabase_client=None)
    first = director._create_task(
        Sector.BRANDS, "Review workspace asset", "self_diagnostic",
        "workspace scoped test", params={"asset_id": "asset-1"},
        workspace_id="workspace-a",
    )
    repeated_same_workspace = director._create_task(
        Sector.BRANDS, "Review workspace asset", "self_diagnostic",
        "repeat workspace scoped test", params={"asset_id": "asset-1"},
        workspace_id="workspace-a",
    )
    same_intent_other_workspace = director._create_task(
        Sector.BRANDS, "Review workspace asset", "self_diagnostic",
        "different workspace scoped test", params={"asset_id": "asset-1"},
        workspace_id="workspace-b",
    )

    assert first.id == repeated_same_workspace.id
    assert same_intent_other_workspace.id != first.id
    assert first.id not in director.tasks
    assert same_intent_other_workspace.id not in director.tasks
    assert director.get_task(first.id, workspace_id="workspace-a") is first
    assert director.get_task(first.id, workspace_id="workspace-b") is None


def test_workspace_verification_never_falls_back_to_platform_observations():
    director = Director(supabase_client=None)
    task = make_task([{
        "observer": "domain", "metric_path": "$status",
        "operator": "equals", "expected": Health.NOMINAL.value,
    }], workspace_id="workspace-a")
    task.id = "workspace-evidence-task"
    director.workspace_tasks["workspace-a"] = {task.id: task}
    director.observations["domain"] = make_observation({}, status=Health.NOMINAL)

    import asyncio
    result = asyncio.run(director.evaluate_task_success_criteria(
        task.id, workspace_id="workspace-a",
    ))

    assert result["outcome"] == "unknown"
    assert result["criteria"][0]["reason"] == "observer_missing"


@pytest.mark.asyncio
async def test_workspace_task_restore_loads_only_explicit_workspace_scope():
    source = Director(supabase_client=None)
    workspace_a = make_task([], status=Status.APPROVAL, workspace_id="workspace-a")
    workspace_a.id = "persisted-workspace-a"
    workspace_b = make_task([], status=Status.APPROVAL, workspace_id="workspace-b")
    workspace_b.id = "persisted-workspace-b"
    assert await source.store.save_task(workspace_a)
    assert await source.store.save_task(workspace_b)

    restored = Director(supabase_client=None)
    restored.store = source.store
    summary = await restored.restore_workspace_tasks("workspace-a")

    assert summary["restored"] == 1
    assert restored.get_task(workspace_a.id, workspace_id="workspace-a") is not None
    assert restored.get_task(workspace_b.id, workspace_id="workspace-a") is None
    assert workspace_b.id not in restored.workspace_tasks.get("workspace-a", {})
    assert workspace_a.id not in restored.tasks


@pytest.mark.asyncio
async def test_workspace_observation_restore_is_exactly_scoped():
    source = Director(supabase_client=None)
    workspace_a_observation = make_observation(
        {"tenant": {"value": "a"}}, workspace_id="workspace-a",
    )
    workspace_a_observation.source = "tenant-observer"
    workspace_b_observation = make_observation(
        {"tenant": {"value": "b"}}, workspace_id="workspace-b",
    )
    workspace_b_observation.source = "tenant-observer"
    platform_observation = make_observation({"platform": {"value": True}})
    platform_observation.source = "platform-observer"
    assert await source.store.save_observation(workspace_a_observation)
    assert await source.store.save_observation(workspace_b_observation)
    assert await source.store.save_observation(platform_observation)

    restored = Director(supabase_client=None)
    restored.store = source.store
    summary = await restored.restore_workspace_observations("workspace-a")

    assert summary["restored"] == 1
    assert set(restored.workspace_observations["workspace-a"]) == {"tenant-observer"}
    assert restored.workspace_observations["workspace-a"]["tenant-observer"].workspace_id == "workspace-a"
    assert "workspace-b" not in restored.workspace_observations
    assert restored.observations == {}


def test_workspace_task_api_lazily_restores_only_authenticated_workspace(monkeypatch):
    from fastapi.testclient import TestClient
    from services.director import create_app

    monkeypatch.setenv("MASTER_API_TOKEN", "workspace-restore-token")
    director = Director(supabase_client=None)
    workspace_a = make_task([], status=Status.APPROVAL, workspace_id="workspace-a")
    workspace_a.id = "restored-api-a"
    workspace_b = make_task([], status=Status.APPROVAL, workspace_id="workspace-b")
    workspace_b.id = "restored-api-b"

    import asyncio
    asyncio.run(director.store.save_task(workspace_a))
    asyncio.run(director.store.save_task(workspace_b))

    app = create_app(director)
    _install_tenant_context(app, workspace_id="workspace-a")
    client = TestClient(app)
    response = client.get(
        "/tasks", headers={"Authorization": "Bearer workspace-restore-token"},
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == ["restored-api-a"]
    assert "workspace-b" not in director.workspace_tasks


def test_workspace_proposal_approval_creates_task_in_same_workspace_registry():
    director = Director(supabase_client=None)
    proposal = Proposal(
        id="workspace-proposal",
        workspace_id="workspace-a",
        sector=Sector.BRANDS,
        title="Review brand health",
        rationale="A workspace-owned review proposal",
        capability="self_diagnostic",
        params={"brand_id": "brand-a"},
        target_param_value="brand-a",
        expected_benefit="Improved visibility",
        estimated_cost="0",
        risk=Risk.LOW,
        confidence=0.9,
        signal_ids=[],
        status=ProposalStatus.PENDING,
    )
    director.proposals[proposal.id] = proposal

    task = director.approve_proposal(proposal.id, actor="workspace-owner")

    assert task.workspace_id == "workspace-a"
    assert task.scope_type == "workspace"
    assert task.id not in director.tasks
    assert director.workspace_tasks["workspace-a"][task.id] is task
