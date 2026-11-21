import asyncio\nfrom types import SimpleNamespace


from services import governed_provider_recovery as recovery


class FakeRpcResponse:
    def __init__(self, data):
        self.data = data


class FakeClient:
    def __init__(self):
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        return SimpleNamespace(execute=lambda: FakeRpcResponse({
            "status": "queued",
            "job_id": params["p_job_id"],
            "attempt_count": 3,
            "recovery_key": params["p_recovery_key"],
        }))


class FakeJobService:
    def __init__(self):
        self.client = FakeClient()

    def _require_client(self):
        return self.client


class FakeGateway:
    def __init__(self, result):
        self.result = result

    def evaluate(self, **_kwargs):
        return self.result


@pytest.mark.asyncio
def test_provider_probe_failure_defers_without_mutating_jobs(monkeypatch):
    monkeypatch.setattr(
        "ai.gateway.get_llm_gateway",
        lambda: FakeGateway({
            "ok": False,
            "keyword_hits": 0,
            "provider": None,
            "request_id": "probe-failed",
        }),
    )
    service = FakeJobService()

    result = asyncio.run(recovery.recover_provider_availability_jobs(
        job_ids=["job-1"],
        recovery_key_prefix="event-1",
        job_service=service,
    )

    assert result["status"] == "deferred"
    assert service.client.calls == []


@pytest.mark.asyncio
def test_successful_probe_uses_bounded_governed_rpc_and_stable_keys(monkeypatch):
    monkeypatch.setattr(
        "ai.gateway.get_llm_gateway",
        lambda: FakeGateway({
            "ok": True,
            "keyword_hits": 1,
            "provider": "test-provider",
            "request_id": "probe-123",
        }),
    )
    service = FakeJobService()

    result = await recovery.recover_provider_availability_jobs(
        job_ids=["job-1", "job-2", "job-1"] + [f"job-{i}" for i in range(3, 15)],
        recovery_key_prefix="correlation-1",
        job_service=service,
        max_jobs=8,
    )

    assert result["status"] == "completed"
    assert len(result["queued"]) == 8
    assert len(service.client.calls) == 8
    assert all(name == "stage_governed_provider_retry" for name, _ in service.client.calls)
    params = service.client.calls[0][1]
    assert params["p_job_id"] == "job-1"
    assert params["p_probe_ok"] is True
    assert params["p_provider"] == "test-provider"
    assert params["p_probe_request_id"] == "probe-123"
    assert len(params["p_recovery_key"]) == 64


@pytest.mark.asyncio
def test_empty_candidate_set_does_not_probe_or_call_database(monkeypatch):
    def fail_if_called():
        raise AssertionError("provider probe must not run for an empty set")

    monkeypatch.setattr(recovery, "_probe_provider_recovery", fail_if_called)
    service = FakeJobService()

    result = await recovery.recover_provider_availability_jobs(
        job_ids=[],
        recovery_key_prefix="event-empty",
        job_service=service,
    )

    assert result == {"status": "no_candidates", "queued": [], "rejected": []}
    assert service.client.calls == []
