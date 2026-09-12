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
    assert result["status"] == "succeeded"
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
