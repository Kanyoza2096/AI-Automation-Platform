import pytest

from domain.publishing.canonical_pipeline import run_canonical_publish


@pytest.mark.asyncio
async def test_live_publish_generates_idempotency_key(monkeypatch):
    captured = {}
    async def fake_guardian(*args, **kwargs):
        return {"status": "allow"}
    async def fake_metabolism(*args, **kwargs):
        return {"status": "ok"}
    async def fake_council(*args, **kwargs):
        return {"status": "skipped"}
    def fake_allowed(*args, **kwargs):
        class D: decision_id = "d1"
        return D()
    async def fake_connector(**kwargs):
        captured.update(kwargs)
        return {"status":"ok", "platform_post_id":"p1"}
    monkeypatch.setattr("domain.publishing.canonical_pipeline._guardian_evaluate", fake_guardian)
    monkeypatch.setattr("domain.publishing.canonical_pipeline._metabolism_check", fake_metabolism)
    monkeypatch.setattr("domain.publishing.canonical_pipeline._council_deliberate", fake_council)
    monkeypatch.setattr("core.publish_authorization.assert_publish_allowed", fake_allowed)
    monkeypatch.setattr("domain.publishing.canonical_pipeline._connector_execute", fake_connector)
    result = await run_canonical_publish(workspace_id="w1", topic="hello", durable=False)
    assert result["ok"] is True
    assert captured["idempotency_key"].startswith("exec_")


@pytest.mark.asyncio
async def test_guardian_unavailable_blocks_live_publish(monkeypatch):
    async def unavailable(*args, **kwargs):
        return {"status":"unavailable", "reason":"missing"}
    monkeypatch.setattr("domain.publishing.canonical_pipeline._guardian_evaluate", unavailable)
    result = await run_canonical_publish(workspace_id="w1", topic="hello", durable=False)
    assert result["ok"] is False
    assert result["code"] == "guardian_unavailable"
