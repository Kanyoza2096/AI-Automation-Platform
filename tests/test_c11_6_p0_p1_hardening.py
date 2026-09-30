import asyncio
import types
import pytest

from core.synapses import SynapseMatrix
from council.integration import deliberate_action
from council.models import Proposal
from services.business_automation import BusinessAutomationEngine


def test_council_rejects_unknown_action():
    with pytest.raises(ValueError, match="concrete action_name"):
        asyncio.run(deliberate_action(
            action_name="unknown",
            source_engine="condition_engine",
            description="bad proposal",
        ))


def test_synapse_rejects_missing_director_action():
    class Bus:
        def __init__(self): self.events = []
        async def publish(self, *args, **kwargs): self.events.append((args, kwargs))
    s = SynapseMatrix(event_bus=Bus())
    asyncio.run(s._on_director_proposal({"workspace_id": "ws1"}))
    assert not s._bus.events
    assert s.get_stats()["events_rejected"] == 1


def test_synapse_council_failure_fails_closed(monkeypatch):
    class Gate:
        def issue(self, **kwargs): return types.SimpleNamespace(**kwargs, to_dict=lambda: kwargs, decision_id="d1")
    monkeypatch.setattr("core.synapses.decision_gate", Gate(), raising=False)
    s = SynapseMatrix()
    async def fail(*args, **kwargs): raise RuntimeError("council down")
    monkeypatch.setattr("council.engine.DemocracyEngine.deliberate", fail)
    result = asyncio.run(s._deliberate_council({}, "ws1", "c1", "content_adjust_mix", "medium"))
    assert result["approved"] is False
    assert result["confidence"] == 0.0
    assert "fail_closed" in result["reason"]


def test_synapse_council_decided_never_propagates_none_approval():
    class Bus:
        def __init__(self): self.events = []
        async def publish(self, *args, **kwargs): self.events.append((args, kwargs))
    bus = Bus()
    s = SynapseMatrix(event_bus=bus)
    asyncio.run(s._on_council_decided({"decision_id": "d1", "workspace_id": "ws1", "approved": None}))
    payload = bus.events[-1][0][1]
    assert payload["record"]["approved"] is False
    assert payload["record"]["approval_state"] == "invalid_missing_or_non_boolean"


def test_business_automation_engine_list_rules_is_tenant_scoped():
    class Query:
        def __init__(self): self.filters = []
        def select(self, *_): return self
        def eq(self, k, v): self.filters.append((k, v)); return self
        def order(self, *_args, **_kwargs): return self
        def execute(self): return types.SimpleNamespace(data=[])
    class DB:
        def table(self, name):
            assert name == "business_automation_rules"
            self.q = Query(); return self.q
    engine = BusinessAutomationEngine(supabase_client=DB())
    asyncio.run(engine.list_rules("ws-authoritative"))
    assert ("workspace_id", "ws-authoritative") in engine._db.q.filters

def test_business_automation_engine_is_registered_in_composition_root():
    from dependency_registry import _register_business_services
    from service_container import ServiceContainer
    c = ServiceContainer()
    _register_business_services(c, [])
    from services.business_automation import BusinessAutomationEngine
    assert c.try_resolve(BusinessAutomationEngine) is not None
