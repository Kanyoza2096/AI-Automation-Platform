import pytest
import asyncio


def _engine():
    from services.business_automation import BusinessAutomationEngine
    return BusinessAutomationEngine()


def test_notification_action_uses_governed_executor(monkeypatch):
    engine = _engine()
    calls = []

    class FakeNotifier:
        def alert(self, message, level="info", channels=None):
            calls.append((message, level, channels))
            return {c: True for c in channels}

    monkeypatch.setattr("services.notifier.notifier", FakeNotifier())
    result = asyncio.run(engine._action_notify_operator(
        {}, {"message": "hello", "channels": ["slack"], "level": "info"},
        workspace_id="ws-1", idempotency_key="automation:key"
    ))
    assert result["status"] == "uncertain"
    assert result["reconciliation_required"] is True
    assert calls == [("hello", "info", ["slack"])]


def test_notification_partial_delivery_is_uncertain(monkeypatch):
    engine = _engine()

    class FakeNotifier:
        def alert(self, message, level="info", channels=None):
            return {"slack": True, "discord": False}

    monkeypatch.setattr("services.notifier.notifier", FakeNotifier())
    result = asyncio.run(engine._action_notify_operator(
        {}, {"message": "hello", "channels": ["slack", "discord"]},
        workspace_id="ws-1", idempotency_key="automation:partial"
    ))
    assert result["status"] == "uncertain"
    assert result["reconciliation_required"] is True

@pytest.mark.asyncio
async def test_business_automation_rule_update_pause_and_resume():
    from services.business_automation import BusinessAutomationEngine

    engine = BusinessAutomationEngine()
    rule = await engine.create_rule("ws-update", {
        "name": "Lifecycle test",
        "action": "log",
        "parameters": {"message": "hello"},
        "risk": "low",
    })
    paused = await engine.update_rule("ws-update", rule.id, {"enabled": False})
    assert paused.enabled is False
    assert paused.version == rule.version + 1
    resumed = await engine.update_rule("ws-update", rule.id, {"enabled": True, "name": "Lifecycle updated"})
    assert resumed.enabled is True
    assert resumed.name == "Lifecycle updated"
    assert resumed.version == paused.version + 1


@pytest.mark.asyncio
async def test_business_automation_rule_update_rejects_unknown_action():
    from services.business_automation import BusinessAutomationEngine

    engine = BusinessAutomationEngine()
    rule = await engine.create_rule("ws-update", {"name": "Safe", "action": "log"})
    with pytest.raises(ValueError, match="unsupported action"):
        await engine.update_rule("ws-update", rule.id, {"action": "arbitrary_shell"})


def test_business_automation_catalog_is_declarative_and_bounded():
    from services.business_automation_catalog import list_templates, get_template
    templates = list_templates()
    assert len(templates) >= 6
    assert all(t["action"] in {"crm_add_contact", "crm_add_deal", "crm_schedule_followup", "notify_operator", "generate_workspace_report", "crm_update_contact_status", "crm_update_deal_stage", "whatsapp_message", "sales_orchestrate", "customer_communication"} for t in templates)
    assert get_template("sales.new-lead-capture")["trigger"] == "lead.created"
    assert all("shell" not in str(t).lower() and "import" not in str(t).lower() for t in templates)
