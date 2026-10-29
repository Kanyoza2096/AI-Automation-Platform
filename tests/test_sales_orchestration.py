import pytest
from services.sales_orchestration import SalesOrchestrator
from services.business_automation import BusinessAutomationEngine


def test_policy_is_bounded_and_deterministic():
    d = SalesOrchestrator.decide({"customer_intent": "purchase_interest"})
    assert d.action == "crm_schedule_followup"
    assert d.confidence > 0.9
    assert SalesOrchestrator.decide({"customer_intent": "unknown"}) is None


def test_opt_out_never_selects_outbound_communication():
    d = SalesOrchestrator.decide({"customer_intent": "opt_out"})
    assert d.action == "notify_operator"
    assert d.action != "whatsapp_message"


def test_engine_registers_orchestrator():
    engine = BusinessAutomationEngine()
    assert "sales_orchestrate" in engine._actions

@pytest.mark.asyncio
async def test_orchestrator_returns_no_action_without_signal():
    result = await SalesOrchestrator.execute(
        event={}, workspace_id="ws1", idempotency_key="k1", approved=False,
        db=None, production=False,
    )
    assert result["status"] == "no_action"

@pytest.mark.asyncio
async def test_orchestrator_hands_consequential_action_to_kernel(monkeypatch):
    calls = {}
    class Kernel:
        async def execute(self, **kwargs):
            calls.update(kwargs)
            return {"status": "succeeded", "intent_id": "intent1"}
    import services.action_kernel
    monkeypatch.setattr(services.action_kernel, "build_default_action_kernel", lambda *a, **k: Kernel())
    result = await SalesOrchestrator.execute(
        event={"customer_intent": "price_request", "phone": "+2651"},
        workspace_id="ws1", idempotency_key="k2", approved=False,
        db=None, production=False,
    )
    assert result["status"] == "succeeded"
    assert calls["action_type"] == "notify_operator"
    assert calls["workspace_id"] == "ws1"
    assert result["orchestration"]["action"] == "notify_operator"
