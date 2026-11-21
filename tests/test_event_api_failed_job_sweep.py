from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from routes import event_api


@pytest.mark.asyncio
async def test_operator_failed_job_sweep_uses_service_role_client(monkeypatch):
    client = object()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                container=SimpleNamespace(
                    supabase_service=SimpleNamespace(client=client)
                )
            )
        )
    )
    expected = {
        "status": "completed",
        "requeued_count": 0,
        "archived_count": 1,
        "protected_count": 0,
    }
    calls = {}

    class FakeJobService:
        def __init__(self, *, supabase_client):
            calls["client"] = supabase_client

    async def fake_sweep(*, job_service, batch_size):
        calls["job_service"] = job_service
        calls["batch_size"] = batch_size
        return expected

    monkeypatch.setattr(
        "services.job_service.JobService",
        FakeJobService,
    )
    monkeypatch.setattr(
        "services.failed_job_sweep.sweep_failed_automation_jobs",
        fake_sweep,
    )

    result = await event_api.sweep_failed_jobs(
        request,
        event_api.FailedJobSweepRequest(batch_size=25),
        _auth=object(),
        _species=object(),
    )

    assert result == {"ok": True, "sweep": expected}
    assert calls["client"] is client
    assert calls["batch_size"] == 25


@pytest.mark.asyncio
async def test_operator_failed_job_sweep_fails_closed_without_service_client():
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(container=None))
    )

    with pytest.raises(event_api.HTTPException) as exc:
        await event_api.sweep_failed_jobs(
            request,
            event_api.FailedJobSweepRequest(),
            _auth=object(),
            _species=object(),
        )

    assert exc.value.status_code == 503
