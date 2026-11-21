from types import SimpleNamespace

import pytest

from services import failed_job_sweep_scheduler as scheduler


@pytest.mark.asyncio
async def test_run_once_uses_service_role_client_and_bounded_batch(monkeypatch):
    service_client = object()
    calls = {}

    def get_service_client():
        return service_client

    class FakeJobService:
        def __init__(self, *, supabase_client):
            calls["client"] = supabase_client

    async def fake_sweep(*, job_service, batch_size):
        calls["job_service"] = job_service
        calls["batch_size"] = batch_size
        return {
            "status": "completed",
            "requeued_count": 2,
            "archived_count": 1,
            "protected_count": 1,
        }

    monkeypatch.setattr("services.supabase.get_service_client_from_settings", get_service_client)
    monkeypatch.setattr("services.job_service.JobService", FakeJobService)
    monkeypatch.setattr("services.failed_job_sweep.sweep_failed_automation_jobs", fake_sweep)

    result = await scheduler.run_failed_job_sweep_once()

    assert result["status"] == "completed"
    assert calls["client"] is service_client
    assert calls["batch_size"] == 25


@pytest.mark.asyncio
async def test_run_once_fails_closed_without_service_role_client(monkeypatch):
    monkeypatch.setattr("services.supabase.get_service_client_from_settings", lambda: None)

    with pytest.raises(RuntimeError, match="failed_job_sweep_service_role_client_unavailable"):
        await scheduler.run_failed_job_sweep_once()


def test_interval_is_bounded(monkeypatch):
    monkeypatch.setenv("KDO_FAILED_JOB_SWEEP_INTERVAL_SECONDS", "1")
    assert scheduler._interval_seconds() == scheduler.MIN_INTERVAL_SECONDS
    monkeypatch.setenv("KDO_FAILED_JOB_SWEEP_INTERVAL_SECONDS", "999999")
    assert scheduler._interval_seconds() == scheduler.MAX_INTERVAL_SECONDS
    monkeypatch.setenv("KDO_FAILED_JOB_SWEEP_INTERVAL_SECONDS", "invalid")
    assert scheduler._interval_seconds() == scheduler.DEFAULT_INTERVAL_SECONDS
