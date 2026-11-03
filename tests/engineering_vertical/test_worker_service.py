from datetime import datetime, timezone
import threading

import pytest

from services.kdo_engineering_phase16_3 import EngineeringBuildJob, Phase163Error
from services.kdo_engineering_worker_service import run_engineering_build_worker


def make_job(**overrides):
    now = datetime.now(timezone.utc).isoformat()
    values = dict(
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
    values.update(overrides)
    return EngineeringBuildJob(**values)


class FakeQueue:
    def __init__(self, job=None, claim_error=None):
        self.job = job
        self.claim_error = claim_error
        self.claim_calls = []
        self.returned = False

    def claim_next(self, **kwargs):
        self.claim_calls.append(kwargs)
        if self.claim_error:
            raise self.claim_error
        if self.returned:
            return None
        self.returned = True
        return self.job


def test_worker_claims_only_configured_scope_and_runs_governed_heartbeat_path(monkeypatch):
    import services.kdo_engineering_worker_service as service

    job = make_job()
    queue = FakeQueue(job)
    stop = threading.Event()
    observed = []

    def fake_run(**kwargs):
        assert kwargs["queue"] is queue
        assert kwargs["job"] is job
        assert kwargs["worker_id"] == "worker-a"
        assert kwargs["lease_token"] == "lease-token"
        return {"status": "awaiting_review"}

    monkeypatch.setattr(service, "run_claimed_build_job_with_heartbeat", fake_run)
    result = run_engineering_build_worker(
        queue=queue,
        workflow=object(),
        worker_id="worker-a",
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="engineering-worker",
        executor=lambda **_: {},
        stopping=stop,
        max_jobs=1,
        on_result=lambda claimed_job, output: observed.append((claimed_job, output)),
    )

    assert queue.claim_calls == [{
        "worker_id": "worker-a",
        "lease_seconds": 300,
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
    }]
    assert result == {
        "claimed": 1,
        "completed": 1,
        "execution_errors": 0,
        "claim_errors": 0,
        "stopped": False,
    }
    assert observed == [(job, {"status": "awaiting_review"})]


def test_worker_fails_closed_on_out_of_scope_claim(monkeypatch):
    import services.kdo_engineering_worker_service as service

    queue = FakeQueue(make_job(workspace_id="workspace-b"))
    monkeypatch.setattr(
        service,
        "run_claimed_build_job_with_heartbeat",
        lambda **_: pytest.fail("out-of-scope job must never execute"),
    )

    with pytest.raises(Phase163Error, match="queue_returned_out_of_scope_claim"):
        run_engineering_build_worker(
            queue=queue,
            workflow=object(),
            worker_id="worker-a",
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            actor_id="engineering-worker",
            executor=lambda **_: {},
            stopping=threading.Event(),
            max_jobs=1,
        )


def test_execution_error_is_not_retried(monkeypatch):
    import services.kdo_engineering_worker_service as service

    queue = FakeQueue(make_job())
    calls = []

    def fail(**_):
        calls.append("execute")
        raise RuntimeError("uncertain execution")

    monkeypatch.setattr(service, "run_claimed_build_job_with_heartbeat", fail)
    result = run_engineering_build_worker(
        queue=queue,
        workflow=object(),
        worker_id="worker-a",
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="engineering-worker",
        executor=lambda **_: {},
        stopping=threading.Event(),
        max_jobs=1,
    )

    assert calls == ["execute"]
    assert len(queue.claim_calls) == 1
    assert result["claimed"] == 1
    assert result["completed"] == 0
    assert result["execution_errors"] == 1


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"worker_id": ""}, "identity_and_scope"),
        ({"poll_interval_seconds": 0}, "poll_interval"),
        ({"max_poll_interval_seconds": 1, "poll_interval_seconds": 2}, "max_poll_interval"),
        ({"max_jobs": 0}, "max_jobs"),
        ({"lease_seconds": True}, "lease_seconds"),
    ],
)
def test_worker_rejects_invalid_configuration(overrides, message):
    args = dict(
        queue=FakeQueue(make_job()),
        workflow=object(),
        worker_id="worker-a",
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="engineering-worker",
        executor=lambda **_: {},
        stopping=threading.Event(),
        max_jobs=1,
    )
    args.update(overrides)
    with pytest.raises(Phase163Error, match=message):
        run_engineering_build_worker(**args)


def test_worker_does_not_claim_after_stop_is_requested():
    stop = threading.Event()
    stop.set()
    queue = FakeQueue(make_job())
    result = run_engineering_build_worker(
        queue=queue,
        workflow=object(),
        worker_id="worker-a",
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="engineering-worker",
        executor=lambda **_: {},
        stopping=stop,
    )

    assert queue.claim_calls == []
    assert result["claimed"] == 0
    assert result["stopped"] is True
