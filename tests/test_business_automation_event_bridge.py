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
        assert await e.run_event(event_name="crm.deal.created", event={"kind": "lead"}, workspace_id="ws1") == []
        out = await e.run_event(event_name="crm.contact.created", event={"kind": "lead"}, workspace_id="ws1")
        assert len(out) == 1 and out[0].status == "completed"
        assert await e.run_event(event_name="crm.contact.created", event={"kind": "lead"}, workspace_id="ws2") == []
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
                    "payload": {"kind": "lead"}, "workspace_id": "ws1"
                }],
                "complete_business_automation_event_delivery": [True],
            }
        def rpc(self, name, args): return RPC(self, name, args)
    async def run():
        db = DB(); engine = BusinessAutomationEngine(db)
        bridge = BusinessAutomationEventBridge(engine)
        rows = await bridge._claim_durable_events()
        assert rows[0]["event_id"] == "e1"
        await bridge._ack_durable(rows[0], success=True)
        assert [c[0] for c in db.calls] == [
            "claim_business_automation_event_deliveries",
            "complete_business_automation_event_delivery",
        ]
    asyncio.run(run())
