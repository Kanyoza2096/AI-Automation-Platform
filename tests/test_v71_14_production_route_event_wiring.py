import pytest
from types import SimpleNamespace
from services.v71_14_production_route_event_wiring import ProductionRouteEventWiring


class Engine:
    async def run_event(self, **kwargs):
        return [{"ok": True, "event_id": kwargs["event"]["event_id"]}]


class Container:
    def try_resolve(self, cls):
        return Engine()


class Runtime: pass


def test_bindings_include_v69_v70_and_legacy_boundaries():
    w = ProductionRouteEventWiring(container=Container(), runtime=Runtime())
    names = {b.name: b for b in w.bindings}
    assert names["KCPG Action Intent"].canonical
    assert names["Business/Event API"].canonical
    assert names["Provider Webhooks"].canonical
    assert names["Business Operating Layer"].canonical
    assert not names["Legacy AutonomousLoop"].canonical

@pytest.mark.asyncio
async def test_event_admission_requires_durable_identity_and_tenant():
    w = ProductionRouteEventWiring(container=Container(), runtime=Runtime())
    with pytest.raises(ValueError, match="event_id_required"):
        await w.admit_event(event_name="customer.created", event={}, workspace_id="w1")
    result = await w.admit_event(
        event_name="customer.created",
        event={"event_id": "e1", "workspace_id": "w1"},
        workspace_id="w1",
    )
    assert result[0]["ok"] is True
    assert w.status()["events_admitted"] == 1
