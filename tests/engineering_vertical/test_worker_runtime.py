from datetime import datetime, timezone
import time

import pytest

from services.kdo_engineering_phase16_3 import EngineeringBuildJob, Phase163Error
from services.kdo_engineering_worker_runtime import (
    WorkerLeaseHeartbeatError,
    run_claimed_build_job_with_heartbeat,
)


def make_job():
    now = datetime.now(timezone.utc).isoformat()
    return EngineeringBuildJob(
        job_id="job-1",
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        task_id="task-1",
        plan_digest="a" * 64,
        idempotency_key="idem-1",
        status="running",
        attempts=1,
        lease_owner="worker-a",
        lease_token="lease-token",
        lease_expires_at=now,
        created_at=now,
        updated_at=now,
    )


class FakeQueue:
    def __init__(self, *, renew_error=None):
        self.renew_error = renew_error
        self.renew_calls = 0
        self.complete_calls = []

    def renew(self, job, *, worker_id, lease_token, lease_seconds):
        self.renew_calls += 1
        assert worker_id == "worker-a"
        assert lease_token == "lease-token"
        assert lease_seconds == 30
        if self.renew_error:
            raise self.renew_error
        return job

    def complete(self, job, *, worker_id, lease_token, status):
        self.complete_calls.append(status)
        return job


def test_worker_runtime_renews_lease_during_governed_execution(monkeypatch):
    import services.kdo_engineering_worker_runtime as runtime

    queue = FakeQueue()
    job = make_job()

    def fake_run_claimed_build_job(**kwargs):
        time.sleep(0.06)
        kwargs["queue"].complete(
            job, worker_id="worker-a", lease_token="lease-token",
            status="awaiting_review",
        )
        return {"status": "awaiting_review"}

    monkeypatch.setattr(runtime, "run_claimed_build_job", fake_run_claimed_build_job)
    result = run_claimed_build_job_with_heartbeat(
        workflow=object(), queue=queue, job=job, worker_id="worker-a",
        lease_token="lease-token", actor_id="builder", executor=lambda **_: {},
            admission_receipt_verifier=lambda scope, receipt: True,
        lease_seconds=30, heartbeat_interval_seconds=0.005,
    )

    assert result == {"status": "awaiting_review"}
    assert queue.renew_calls >= 1
    assert queue.complete_calls == ["awaiting_review"]


def test_heartbeat_failure_prevents_terminal_completion_and_fails_closed(monkeypatch):
    import services.kdo_engineering_worker_runtime as runtime

    queue = FakeQueue(renew_error=Phase163Error("database_unavailable"))
    job = make_job()

    def fake_run_claimed_build_job(**kwargs):
        time.sleep(0.03)
        return kwargs["queue"].complete(
            job, worker_id="worker-a", lease_token="lease-token",
            status="awaiting_review",
        )

    monkeypatch.setattr(runtime, "run_claimed_build_job", fake_run_claimed_build_job)
    with pytest.raises(WorkerLeaseHeartbeatError, match="worker_lease_heartbeat_failed"):
        run_claimed_build_job_with_heartbeat(
            workflow=object(), queue=queue, job=job, worker_id="worker-a",
            lease_token="lease-token", actor_id="builder", executor=lambda **_: {},
            admission_receipt_verifier=lambda scope, receipt: True,
            lease_seconds=30, heartbeat_interval_seconds=0.005,
        )

    assert queue.renew_calls >= 1
    assert queue.complete_calls == []


@pytest.mark.parametrize("interval", [0, -1, 30, 31, True, float("nan"), "1"])
def test_worker_runtime_rejects_invalid_heartbeat_intervals(interval):
    with pytest.raises(Phase163Error, match="heartbeat_interval"):
        run_claimed_build_job_with_heartbeat(
            workflow=object(), queue=FakeQueue(), job=make_job(),
            worker_id="worker-a", lease_token="lease-token", actor_id="builder",
            executor=lambda **_: {},
            admission_receipt_verifier=lambda scope, receipt: True, lease_seconds=30,
            heartbeat_interval_seconds=interval,
        )


def test_worker_runtime_forwards_shutdown_cancellation_event(monkeypatch):
    import threading
    import services.kdo_engineering_worker_runtime as runtime

    cancellation = threading.Event()
    captured = {}

    def fake_run_claimed_build_job(**kwargs):
        captured.update(kwargs)
        return {"status": "cancelled"}

    monkeypatch.setattr(runtime, "run_claimed_build_job", fake_run_claimed_build_job)
    result = run_claimed_build_job_with_heartbeat(
        workflow=object(), queue=FakeQueue(), job=make_job(), worker_id="worker-a",
        lease_token="lease-token", actor_id="builder", executor=lambda **_: {},
            admission_receipt_verifier=lambda scope, receipt: True,
        lease_seconds=30, heartbeat_interval_seconds=1,
        cancellation_event=cancellation,
    )
    assert result == {"status": "cancelled"}
    assert captured["cancellation_event"] is cancellation
