import asyncio

from services.business_automation import BusinessAutomationEngine
from services.business_automation_event_bridge import BusinessAutomationEventBridge


def test_event_trigger_is_explicit_and_workspace_scoped():
    async def run():
        e = BusinessAutomationEngine()
        await e.create_rule("ws1", {
            "name": "lead logger",
            "condition": {"field": "kind", "op": "eq", "value": "lead"},
            "action": "log",
            "parameters": {"trigger_events": ["crm.contact.created"]},
        })
        assert await e.run_event(event_name="crm.deal.created", event={"kind": "lead", "event_id": "evt_deal"}, workspace_id="ws1") == []
        out = await e.run_event(event_name="crm.contact.created", event={"kind": "lead", "event_id": "evt_contact"}, workspace_id="ws1")
        assert len(out) == 1 and out[0].status == "completed"
        assert await e.run_event(event_name="crm.contact.created", event={"kind": "lead", "event_id": "evt_ws2"}, workspace_id="ws2") == []
    asyncio.run(run())


def test_event_bridge_passes_trusted_event_metadata():
    class FakeBus:
        def __init__(self): self.handler = None
        async def subscribe_all(self, handler): self.handler = handler; return lambda: None

    async def run():
        e = BusinessAutomationEngine()
        await e.create_rule("ws1", {
            "name": "event log",
            "condition": {"field": "kind", "op": "eq", "value": "lead"},
            "action": "log",
            "parameters": {"trigger_events": ["crm.contact.created"]},
        })
        bus = FakeBus()
        bridge = BusinessAutomationEventBridge(e, bus)
        await bridge.wire()
        await bus.handler({
            "event_name": "crm.contact.created", "event_id": "evt_123", "source": "crm",
            "workspace_id": "ws1", "payload": {"kind": "lead", "workspace_id": "attacker-value"},
        })
        history = await e.history("ws1")
        assert len(history) == 1
        assert ":crm.contact.created:evt_123:1" in history[0].idempotency_key
        await bridge.unwire()
    asyncio.run(run())


def test_durable_inbox_claim_and_ack_are_rpc_backed():
    class Result:
        def __init__(self, data): self.data = data
    class RPC:
        def __init__(self, db, name, args): self.db, self.name, self.args = db, name, args
        def execute(self): self.db.calls.append((self.name, self.args)); return Result(self.db.responses.get(self.name, []))
    class DB:
        def __init__(self):
            self.calls = []
            self.responses = {
                "claim_business_automation_event_deliveries": [{
                    "delivery_id": "d1", "event_id": "e1", "event_type": "crm.contact.created",
                    "payload": {"kind": "lead"}, "workspace_id": "ws1", "claim_token": "worker-a"
                }],
                "complete_business_automation_event_delivery": True,
            }
        def rpc(self, name, args): return RPC(self, name, args)
    async def run():
        db = DB(); engine = BusinessAutomationEngine(db)
        bridge = BusinessAutomationEventBridge(engine)
        rows = await bridge._claim_durable_events()
        assert rows[0]["event_id"] == "e1"
        await bridge._ack_durable(rows[0], success=True)
        assert db.calls == [
            ("claim_business_automation_event_deliveries", {
                "p_consumer_group": "business_automation",
                "p_limit": 25,
                "p_lease_seconds": 120,
            }),
            ("complete_business_automation_event_delivery", {
                "p_delivery_id": "d1",
                "p_workspace_id": "ws1",
                "p_consumer_group": "business_automation",
                "p_claim_token": "worker-a",
                "p_success": True,
                "p_error": "",
            }),
        ]
    asyncio.run(run())


def test_business_automation_public_ingress_is_separate_from_authenticated_router():
    from api.content_intelligence_automation import public_router, router
    protected_paths = {getattr(r, "path", "") for r in router.routes}
    public_paths = {getattr(r, "path", "") for r in public_router.routes}
    assert "/api/v1/business-automation/event-sources/{source_id}/ingest" in public_paths
    assert "/api/v1/business-automation/lead-capture/{source_id}" in public_paths
    assert "/api/v1/business-automation/event-sources/{source_id}/ingest" not in protected_paths
    assert "/api/v1/business-automation/lead-capture/{source_id}" not in protected_paths


def test_evaluate_supports_bounded_dotted_event_fields():
    from services.business_automation import BusinessAutomationEngine
    event = {"business_decision_input": {"decision_eligible": True, "intent": "purchase_interest"}}
    assert BusinessAutomationEngine.evaluate({"field": "business_decision_input.decision_eligible", "op": "eq", "value": True}, event)
    assert BusinessAutomationEngine.evaluate({"field": "business_decision_input.intent", "op": "eq", "value": "purchase_interest"}, event)
    assert not BusinessAutomationEngine.evaluate({"field": "business_decision_input.missing", "op": "exists", "value": True}, event)


def test_event_driven_automation_rejects_missing_event_identity():
    async def run():
        e = BusinessAutomationEngine()
        await e.create_rule("ws1", {
            "name": "event identity",
            "condition": {"field": "kind", "op": "eq", "value": "lead"},
            "action": "log",
            "parameters": {"trigger_events": ["crm.contact.created"]},
        })
        try:
            await e.run_event(event_name="crm.contact.created", event={"kind": "lead"}, workspace_id="ws1")
        except ValueError as exc:
            assert "event_id" in str(exc)
        else:
            raise AssertionError("missing event identity must fail closed")
    asyncio.run(run())

def test_dotted_exists_operator_resolves_nested_event_paths():
    from services.business_automation import BusinessAutomationEngine
    event = {"business_decision_input": {"decision_eligible": True}}
    assert BusinessAutomationEngine.evaluate({"field": "business_decision_input.decision_eligible", "op": "exists", "value": True}, event)
    assert BusinessAutomationEngine.evaluate({"field": "business_decision_input.missing", "op": "exists", "value": False}, event)

def test_event_bridge_rejects_missing_event_identity():
    class FakeBus:
        async def subscribe_all(self, handler):
            self.handler = handler
            return lambda: None
    async def run():
        bridge = BusinessAutomationEventBridge(BusinessAutomationEngine(), FakeBus())
        await bridge.wire()
        await bridge.event_bus.handler({
            "event_name": "crm.contact.created", "source": "crm",
            "workspace_id": "ws1", "payload": {"kind": "lead"},
        })
        assert bridge.stats()["errors"] == 1
        await bridge.unwire()
    asyncio.run(run())
