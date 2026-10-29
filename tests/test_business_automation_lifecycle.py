import asyncio

from services.business_automation import BusinessAutomationEngine


def test_event_parameter_interpolation_preserves_types_and_nested_values():
    e = BusinessAutomationEngine()
    rendered = e._render_parameters({
        "name": "{{event.name}}",
        "amount": "{{event.amount}}",
        "message": "Lead {{event.name}} from {{event.company}}",
        "nested": {"phone": "{{event.contact.phone}}"},
    }, {"name": "Jane", "amount": 250000, "company": "Acme", "contact": {"phone": "+265"}})
    assert rendered["name"] == "Jane"
    assert rendered["amount"] == 250000
    assert rendered["message"] == "Lead Jane from Acme"
    assert rendered["nested"]["phone"] == "+265"


def test_website_lead_rule_resolves_event_fields_before_action():
    async def run():
        e = BusinessAutomationEngine()
        seen = {}

        async def capture(event, parameters, **kwargs):
            seen.update(parameters)
            return {"ok": True}

        e.register_action("test_capture", capture)
        await e.create_rule("ws1", {
            "name": "capture",
            "action": "test_capture",
            "parameters": {"trigger_events": ["lead.created"], "name": "{{event.name}}", "email": "{{event.email}}"},
        })
        out = await e.run_event(event_name="lead.created", event={"name": "Jane", "email": "jane@example.com", "event_id": "l1"}, workspace_id="ws1")
        assert out[0].status == "completed"
        assert seen == {"trigger_events": ["lead.created"], "name": "Jane", "email": "jane@example.com"}

    asyncio.run(run())


def test_crm_contact_lifecycle_publishes_tenant_scoped_event(monkeypatch):
    class FakeBus:
        def __init__(self): self.calls = []
        async def publish(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return "evt_lifecycle_1"

    async def run():
        e = BusinessAutomationEngine()
        async def governed(*args, **kwargs):
            return {"status": "succeeded", "output": {"data": {"contact_id": "ctc_1"}}}
        monkeypatch.setattr(e, "_execute_governed_crm", governed)
        bus = FakeBus()
        import core.unified_bus as ub
        monkeypatch.setattr(ub, "get_unified_bus", lambda: asyncio.sleep(0, result=bus))
        result = await e._action_crm_add_contact({"event_id": "lead_1", "name": "Jane"}, {}, workspace_id="ws1")
        assert result["lifecycle_event"]["event_id"] == "evt_lifecycle_1"
        assert bus.calls[0][0][0] == "crm.contact.created"
        assert bus.calls[0][1]["workspace_id"] == "ws1"
        assert bus.calls[0][1]["parent_event_id"] == "lead_1"

    asyncio.run(run())
