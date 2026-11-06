"""Council approval must be explicit before Condition Engine side effects."""
from types import SimpleNamespace

import pytest

from council.integration import condition_engine_integration as integration


class FakeDecision:
    def __init__(self, *, approved=False, blocked=False, escalation=False, state="unknown"):
        self.is_approved = approved
        self.is_blocked = blocked
        self.needs_escalation = escalation
        self.reason = f"Council {state}"
        self.state = state

    def to_dict(self):
        return {"decision": self.state}


class FakePlan:
    action = "increase_similar_content"
    brand_id = "brand-test"
    parameters = {"workspace_id": "workspace-test", "correlation_id": "correlation-test", "content_mix_change": 0.1}
    reason = "test-only"
    decision_id = "decision-test"
    risk = "low"
    confidence = 0.9


@pytest.mark.asyncio
async def test_unknown_council_decision_fails_closed_before_executor(monkeypatch):
    called = {"side_effect": False}

    async def unknown_decision(**kwargs):
        return FakeDecision(state="unknown")

    class NeverExecutor:
        def __init__(self, *args, **kwargs):
            called["side_effect"] = True

    monkeypatch.setattr(integration, "deliberate_action", unknown_decision)
    monkeypatch.setattr(integration, "ExternalActionExecutor", NeverExecutor)
    monkeypatch.setattr(integration, "ExecutionLedger", NeverExecutor)

    engine = SimpleNamespace(supabase=None, set_action_executor=lambda fn: setattr(engine, "action_executor", fn))
    integration.bind_condition_engine_with_council(engine)
    result = await engine.action_executor(FakePlan())

    assert result["ok"] is False
    assert result["governance_unverified"] is True
    assert result["error"] == "council_approval_not_confirmed"
    assert called["side_effect"] is False


@pytest.mark.asyncio
async def test_explicit_council_approval_reaches_governed_execution_boundary(monkeypatch):
    called = {"execute": 0}

    async def approved_decision(**kwargs):
        return FakeDecision(approved=True, state="approved")

    class FakeContentEngine:
        async def boost_content(self, **kwargs):
            return {"ok": True, "post_id": "test-object-1"}

    class FakeLedger:
        def __init__(self, *args, **kwargs):
            assert kwargs.get("db") is not None
            assert kwargs.get("allow_in_memory") is False

        @staticmethod
        def make_idempotency_key(*parts):
            return "test-idempotency-key"

    class FakeExecutor:
        def __init__(self, ledger):
            pass

        async def execute(self, **kwargs):
            called["execute"] += 1
            assert kwargs["workspace_id"] == "workspace-test"
            assert kwargs["metadata"]["decision_id"] == "decision-test"
            assert kwargs["metadata"]["correlation_id"] == "correlation-test"
            return {"ok": True, "success": True, "status": "succeeded", "provider_object_id": "test-object-1"}

    import services.content_engine as content_module
    monkeypatch.setattr(content_module, "content_engine", FakeContentEngine(), raising=False)
    monkeypatch.setattr(integration, "deliberate_action", approved_decision)
    monkeypatch.setattr(integration, "ExecutionLedger", FakeLedger)
    monkeypatch.setattr(integration, "ExternalActionExecutor", FakeExecutor)

    engine = SimpleNamespace(supabase=None, set_action_executor=lambda fn: setattr(engine, "action_executor", fn))
    engine = SimpleNamespace(supabase=object(), set_action_executor=lambda fn: setattr(engine, "action_executor", fn))
    integration.bind_condition_engine_with_council(engine)
    result = await engine.action_executor(FakePlan())

    assert result["ok"] is True
    assert result["council"]["decision"] == "approved"
    assert called["execute"] == 1


@pytest.mark.asyncio
async def test_approved_action_fails_closed_without_durable_ledger(monkeypatch):
    async def approved_decision(**kwargs):
        return FakeDecision(approved=True, state="approved")

    class NeverExecutor:
        def __init__(self, *args, **kwargs):
            raise AssertionError("executor must not be constructed without durable ledger")

    monkeypatch.setattr(integration, "deliberate_action", approved_decision)
    monkeypatch.setattr(integration, "ExternalActionExecutor", NeverExecutor)

    engine = SimpleNamespace(supabase=None, set_action_executor=lambda fn: setattr(engine, "action_executor", fn))
    integration.bind_condition_engine_with_council(engine)
    result = await engine.action_executor(FakePlan())

    assert result["ok"] is False
    assert result["execution_unavailable"] is True
    assert result["error"] == "durable_execution_ledger_unavailable"


@pytest.mark.asyncio
async def test_executor_success_without_provider_object_id_is_not_success(monkeypatch):
    async def approved_decision(**kwargs):
        return FakeDecision(approved=True, state="approved")

    class FakeContentEngine:
        async def boost_content(self, **kwargs):
            return {"ok": True, "post_id": "test-object-2"}

    class FakeLedger:
        def __init__(self, *args, **kwargs):
            pass
        @staticmethod
        def make_idempotency_key(*parts):
            return "test-idempotency-key"

    class FakeExecutor:
        def __init__(self, ledger):
            pass
        async def execute(self, **kwargs):
            return {"status": "succeeded", "provider_object_id": None, "success": True}

    import services.content_engine as content_module
    monkeypatch.setattr(content_module, "content_engine", FakeContentEngine(), raising=False)
    monkeypatch.setattr(integration, "deliberate_action", approved_decision)
    monkeypatch.setattr(integration, "ExecutionLedger", FakeLedger)
    monkeypatch.setattr(integration, "ExternalActionExecutor", FakeExecutor)

    engine = SimpleNamespace(supabase=object(), set_action_executor=lambda fn: setattr(engine, "action_executor", fn))
    integration.bind_condition_engine_with_council(engine)
    result = await engine.action_executor(FakePlan())

    assert result["ok"] is False
    assert result["success"] is False
    assert result["status"] == "uncertain"
    assert result["reconciliation_required"] is True
