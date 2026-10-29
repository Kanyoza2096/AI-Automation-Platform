import asyncio
from datetime import datetime, timezone

import pytest

from services.director import Director
from services.director.core import _verify_restart, _verify_schedule_change, _verify_worker_scale
from services.director.models import Authority, Risk, Sector, Status, Task


def _observer(metrics, *, status="healthy", observed_at=None):
    return {
        "domain": {
            "status": status,
            "observed_at": observed_at or datetime.now(timezone.utc).isoformat(),
            "metrics": metrics,
        }
    }


def test_restart_verification_distinguishes_unknown_from_measured_failure():
    assert _verify_restart({"service_name": "api"}, {}, {}) is None
    assert _verify_restart(
        {"service_name": "api"},
        {"api": {"status": "offline"}},
        {"api": {"status": "healthy"}},
    ) is True
    assert _verify_restart(
        {"service_name": "api"},
        {"api": {"status": "offline"}},
        {"api": {"status": "critical"}},
    ) is False
    assert _verify_restart(
        {"service_name": "api"},
        {"api": {"status": "unknown"}},
        {"api": {"status": "healthy"}},
    ) is None


def test_schedule_and_worker_verifiers_do_not_call_missing_evidence_failure():
    assert _verify_schedule_change(
        {"brand_id": "b1", "posting_hours": [9, 12]}, {}, {}
    ) is None
    assert _verify_worker_scale(
        {"pool_name": "content", "target_workers": 4}, {}, {}
    ) is None

    schedule_state = _observer({"brands": {"b1": {
        "workspace_id": "w1", "posting_schedule": {"content_hours": [9, 12]}
    }}})
    assert _verify_schedule_change(
        {"brand_id": "b1", "workspace_id": "w1", "posting_hours": [9, 12]}, {}, schedule_state
    ) is True
    assert _verify_schedule_change(
        {"brand_id": "b1", "workspace_id": "w1", "posting_hours": [8, 12]}, {}, schedule_state
    ) is False

    worker_state = _observer({"worker_pools": {"content": {
        "workspace_id": "w1", "worker_count": 2
    }}})
    assert _verify_worker_scale(
        {"pool_name": "content", "workspace_id": "w1", "target_workers": 2}, {}, worker_state
    ) is True
    assert _verify_worker_scale(
        {"pool_name": "content", "workspace_id": "w1", "target_workers": 4}, {}, worker_state
    ) is False


@pytest.mark.asyncio
async def test_unknown_post_action_evidence_is_flagged_and_not_learned_as_success(monkeypatch):
    director = Director(supabase_client=None)
    cap = director.capabilities["restart_failed_service"]
    async def handler(_params):
        return {"ok": True, "status": "executed"}
    cap.handler = handler
    cap.outcome_predicate = lambda _params, _pre, _post: None
    cap.verification_window_seconds = 1
    monkeypatch.setattr(asyncio, "sleep", lambda _seconds: _immediate())

    task = Task(
        id="p23-unknown-outcome", sector=Sector.HEALTH, description="Verify recovery",
        capability="restart_failed_service", authority=Authority.AUTO, risk=Risk.MEDIUM,
        params={"service_name": "api"}, status=Status.PLANNED,
    )
    director.tasks[task.id] = task
    result = await director._execute_async(task.id)

    assert result.status is Status.FLAGGED
    assert result.result["verified"] is None
    assert result.result["outcome_verification"]["outcome"] == "unknown"
    assert director.learning_loop.capability_stats.get("restart_failed_service", {}).get("successes", 0) == 0
    assert director.learning_loop.capability_stats.get("restart_failed_service", {}).get("failures", 0) == 0


async def _immediate():
    return None


@pytest.mark.asyncio
async def test_verification_predicate_exception_is_unknown_not_ineffective(monkeypatch):
    director = Director(supabase_client=None)
    cap = director.capabilities["restart_failed_service"]
    async def handler(_params):
        return {"ok": True, "status": "executed"}
    cap.handler = handler
    def broken(_params, _pre, _post):
        raise RuntimeError("observer parsing failed")
    cap.outcome_predicate = broken
    cap.verification_window_seconds = 1
    monkeypatch.setattr(asyncio, "sleep", lambda _seconds: _immediate())
    task = Task(
        id="p23-predicate-error", sector=Sector.HEALTH, description="Verify recovery",
        capability="restart_failed_service", authority=Authority.AUTO, risk=Risk.MEDIUM,
        params={"service_name": "api"}, status=Status.PLANNED,
    )
    director.tasks[task.id] = task
    result = await director._execute_async(task.id)
    assert result.status is Status.FLAGGED
    assert result.result["outcome_verification"] == {
        "outcome": "unknown", "reason": "outcome_predicate_error"
    }
    assert director.learning_loop.capability_stats.get("restart_failed_service", {}).get("successes", 0) == 0
    assert director.learning_loop.capability_stats.get("restart_failed_service", {}).get("failures", 0) == 0
