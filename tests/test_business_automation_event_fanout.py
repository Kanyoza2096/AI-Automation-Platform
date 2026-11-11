from __future__ import annotations

import asyncio
from pathlib import Path

from services.business_automation import BusinessAutomationEngine
from services.business_automation_event_bridge import BusinessAutomationEventBridge


class _Result:
    def __init__(self, data):
        self.data = data


class _Rpc:
    def __init__(self, db, name):
        self.db = db
        self.name = name

    def execute(self):
        self.db.calls.append(self.name)
        if self.name == "business_automation_event_fanout_ready":
            if self.db.raise_readiness_error:
                raise RuntimeError("readiness RPC unavailable")
            return _Result(self.db.fanout_ready)
        if self.name == "claim_business_automation_event_deliveries":
            return _Result([])
        raise AssertionError(f"unexpected RPC: {self.name}")


class _Db:
    def __init__(self, *, fanout_ready, raise_readiness_error=False):
        self.fanout_ready = fanout_ready
        self.raise_readiness_error = raise_readiness_error
        self.calls = []

    def rpc(self, name, _args):
        return _Rpc(self, name)


class _EventBus:
    async def subscribe_all(self, handler):
        self.handler = handler
        return lambda: None


def test_bridge_starts_durable_inbox_only_after_fanout_is_confirmed():
    async def run():
        db = _Db(fanout_ready=True)
        bridge = BusinessAutomationEventBridge(BusinessAutomationEngine(db), _EventBus())
        await bridge.wire()

        assert bridge.stats()["durable_inbox"]["enabled"] is True
        assert bridge.stats()["durable_inbox"]["fanout_ready"] is True
        assert "business_automation_event_fanout_ready" in db.calls
        assert bridge._durable_task is not None

        await bridge.unwire()
        assert bridge.stats()["durable_inbox"]["enabled"] is False

    asyncio.run(run())


def test_bridge_fails_closed_when_fanout_trigger_is_missing_or_disabled():
    async def run():
        db = _Db(fanout_ready=False)
        bridge = BusinessAutomationEventBridge(BusinessAutomationEngine(db), _EventBus())
        await bridge.wire()

        inbox = bridge.stats()["durable_inbox"]
        assert inbox["enabled"] is False
        assert inbox["fanout_ready"] is False
        assert bridge._durable_task is None
        assert db.calls == ["business_automation_event_fanout_ready"]

        # The low-latency event subscription remains wired; only the durable
        # inbox is marked unavailable.
        assert bridge.stats()["wired"] is True
        await bridge.unwire()

    asyncio.run(run())


def test_bridge_fails_closed_when_fanout_readiness_rpc_is_unavailable():
    async def run():
        db = _Db(fanout_ready=False, raise_readiness_error=True)
        bridge = BusinessAutomationEventBridge(BusinessAutomationEngine(db), _EventBus())
        await bridge.wire()

        assert bridge.stats()["durable_inbox"]["enabled"] is False
        assert bridge.stats()["durable_inbox"]["fanout_ready"] is False
        assert bridge._durable_task is None
        await bridge.unwire()

    asyncio.run(run())


def test_event_fanout_migration_is_workspace_scoped_and_idempotent():
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "164_u2_business_automation_event_fanout.sql"
    ).read_text(encoding="utf-8")

    assert "AFTER INSERT ON public.events" in migration
    assert "rule.workspace_id = NEW.workspace_id" in migration
    assert "rule.enabled IS TRUE" in migration
    assert "jsonb_typeof(rule.parameters -> 'trigger_events') = 'array'" in migration
    assert "(rule.parameters -> 'trigger_events') ? NEW.event_type" in migration
    assert "ON CONFLICT (event_id, consumer_group) DO NOTHING" in migration
    assert "'business_automation'" in migration
    assert "business_automation_event_fanout_ready" in migration
    assert "does not backfill historical events" in migration
