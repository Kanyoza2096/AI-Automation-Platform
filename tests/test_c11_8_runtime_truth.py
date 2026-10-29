import asyncio
import importlib
import logging


def test_intelligent_decision_rejects_missing_action(caplog):
    from core.autonomous_loop import AutonomousLoop

    loop = object.__new__(AutonomousLoop)
    class Metrics:
        decisions_made = 0
    loop._metrics = Metrics()

    async def run():
        await loop._handle_intelligent_decision({"decision_id": "d-malformed"})

    with caplog.at_level(logging.ERROR):
        asyncio.run(run())
    assert loop._metrics.decisions_made == 0
    assert "Rejected malformed intelligent_decision" in caplog.text
    assert "unknown" not in caplog.text


def test_intelligent_decision_accepts_explicit_action():
    from core.autonomous_loop import AutonomousLoop

    loop = object.__new__(AutonomousLoop)
    class Metrics:
        decisions_made = 0
    loop._metrics = Metrics()

    asyncio.run(loop._handle_intelligent_decision({"action": "review_content_strategy"}))
    assert loop._metrics.decisions_made == 1


def test_business_automation_rpc_retries_transport_and_refreshes_client():
    from services.business_automation import BusinessAutomationEngine
    from services.business_automation_event_bridge import BusinessAutomationEventBridge

    class Result:
        data = [{"ok": True}]

    class DB:
        def __init__(self, fail_once=False): self.calls = 0; self.fail_once = fail_once
        def rpc(self, name, args):
            self.calls += 1
            db = self
            class RPC:
                def execute(self_inner):
                    if db.fail_once:
                        db.fail_once = False
                        raise BrokenPipeError("broken pipe")
                    return Result()
            return RPC()

    async def run():
        db = DB(fail_once=True); fresh = DB(); engine = BusinessAutomationEngine(db)
        bridge = BusinessAutomationEventBridge(engine)
        refreshed = {"done": False}
        async def fake_refresh():
            engine.set_supabase_client(fresh)
            refreshed["done"] = True
            return True
        bridge._refresh_db_client = fake_refresh
        result = await bridge._execute_db_rpc("claim_business_automation_event_deliveries", {})
        assert result.data == [{"ok": True}]
        assert refreshed["done"] is True
        assert fresh.calls == 1

    asyncio.run(run())
