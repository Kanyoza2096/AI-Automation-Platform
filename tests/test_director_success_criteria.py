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
    assert director.store.learnings == []

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
