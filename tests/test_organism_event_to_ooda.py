"""End-to-end contract: workspace event -> perception -> OODA state synthesis."""
import pytest

from core.organism import OrganismRegistry
from organism.coordinator import cycle_started_organisms
from organism.event_bridge import OrganismEventBridge


class _RecordingStore:
    def __init__(self):
        self.persisted = []

    async def persist(self, runtime):
        self.persisted.append({
            "workspace_id": runtime.workspace_id,
            "state": runtime.state,
            "observations": runtime.perception.recent(runtime.workspace_id),
        })
        return 1


class _Bus:
    def __init__(self):
        self.handlers = []

    async def subscribe_all(self, handler):
        self.handlers.append(handler)

        def unsubscribe():
            self.handlers[:] = [item for item in self.handlers if item is not handler]

        return unsubscribe

    async def emit(self, event):
        for handler in tuple(self.handlers):
            await handler(event)


@pytest.mark.asyncio
async def test_workspace_event_flows_through_perception_into_ooda_state_without_cross_tenant_effects():
    registry = OrganismRegistry()
    workspace_a = await registry.ensure_started("workspace-a")
    workspace_b = registry.get_or_create("workspace-b")
    store = _RecordingStore()
    workspace_a.runtime.persistence_store = store
    bridge = OrganismEventBridge(registry)
    bus = _Bus()
    await bridge.start(bus)

    try:
        await bus.emit({
            "event_name": "service_heartbeat",
            "event_id": "u2-e2e-workspace-a-001",
            "workspace_id": "workspace-a",
            "source": "u2-acceptance",
            "correlation_id": "u2-acceptance-correlation",
            "payload": {
                "service": "synthetic-api",
                "status": "degraded",
                "health": 0.25,
                "api_key": "must-never-enter-perception",
                "error": "must-never-enter-perception",
            },
        })

        observations_a = workspace_a.runtime.perception.recent("workspace-a")
        assert len(observations_a) == 1
        assert observations_a[0].value == {
            "service": "synthetic-api",
            "status": "degraded",
            "health": 0.25,
        }
        assert workspace_b.runtime.perception.recent("workspace-b") == ()

        result = await cycle_started_organisms(registry)

        assert result.attempted == 1
        assert result.completed == 1
        assert result.failed == 0
        assert result.workspace_ids == ("workspace-a",)
        assert workspace_a.runtime.state is not None
        assert workspace_a.runtime.state.health == 0.25
        assert workspace_b.runtime.state is None
        assert registry.get("workspace-b") is workspace_b

        # The OODA coordinator must persist the state it just synthesized,
        # not merely update an in-memory runtime. Persistence remains scoped
        # to the started workspace and carries no execution authority.
        assert len(store.persisted) == 1
        saved = store.persisted[0]
        assert saved["workspace_id"] == "workspace-a"
        assert saved["state"].health == 0.25
        assert saved["state"].authority == "suggest"
        assert saved["observations"] == observations_a
    finally:
        await bridge.stop()
