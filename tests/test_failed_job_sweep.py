from types import SimpleNamespace

import pytest

from services.failed_job_sweep import sweep_failed_automation_jobs


class _RPC:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def execute(self):
        return SimpleNamespace(data=self.response)


class _Client:
    def __init__(self, response):
        self.rpc_call = _RPC(response)
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        return self.rpc_call


class _JobService:
    def __init__(self, client):
        self.client = client

    def _require_client(self):
        return self.client


@pytest.mark.asyncio
async def test_sweep_uses_transactional_rpc_and_caps_batch_size():
    client = _Client([{
        "status": "completed",
        "requeued": ["job-a"],
        "archived_and_removed": ["job-b"],
        "protected_for_publish_reconciliation": ["job-c"],
        "requeued_count": 1,
        "archived_count": 1,
        "protected_count": 1,
    }])

    result = await sweep_failed_automation_jobs(
        job_service=_JobService(client),
        batch_size=1000,
    )

    assert client.calls == [(
        "sweep_failed_automation_jobs",
        {"p_batch_size": 100},
    )]
    assert result["requeued_count"] == 1
    assert result["archived_count"] == 1
    assert result["protected_count"] == 1


@pytest.mark.asyncio
async def test_sweep_rejects_unexpected_rpc_result():
    client = _Client([{"status": "error"}])

    with pytest.raises(RuntimeError, match="failed_job_sweep_rpc_returned_unexpected_result"):
        await sweep_failed_automation_jobs(
            job_service=_JobService(client),
            batch_size=25,
        )
