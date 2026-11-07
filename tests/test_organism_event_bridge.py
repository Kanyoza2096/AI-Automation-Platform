import pytest

from core.organism import OrganismRegistry
from organism.event_bridge import OrganismEventBridge
from organism.perception import ObservationKind


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
async def test_bridge_ingests_only_allowlisted_fields_into_matching_workspace():
    registry = OrganismRegistry()
    organism = registry.get_or_create("workspace-a")
    bridge = OrganismEventBridge(registry)
    bus = _Bus()
    await bridge.start(bus)

    await bus.emit({
        "event_name": "service_heartbeat",
        "event_id": "evt-1",
        "workspace_id": "workspace-a",
        "source": "monitor",
        "correlation_id": "corr-1",
        "payload": {
            "service": "api",
            "status": "healthy",
            "cpu_percent": 22.5,
            "api_key": "must-not-enter-runtime",
            "error": "must-not-enter-runtime",
        },
    })

    observations = organism.runtime.perception.recent("workspace-a")
    assert len(observations) == 1
    observation = observations[0]
    assert observation.kind is ObservationKind.HEALTH
    assert observation.value == {
        "service": "api",
        "status": "healthy",
        "cpu_percent": 22.5,
    }
    assert observation.metadata["event_id"] == "evt-1"
    assert "api_key" not in observation.value
    assert "error" not in observation.value


@pytest.mark.asyncio
async def test_bridge_never_falls_back_to_default_or_creates_unknown_workspaces():
    registry = OrganismRegistry()
    existing = registry.get_or_create("workspace-a")
    bridge = OrganismEventBridge(registry)
    bus = _Bus()
    await bridge.start(bus)

    await bus.emit({
        "event_name": "task_failed",
        "event_id": "evt-unscoped",
        "payload": {"action": "publish", "status": "failed"},
    })
    await bus.emit({
        "event_name": "task_failed",
        "event_id": "evt-unknown-workspace",
        "workspace_id": "workspace-b",
        "payload": {"action": "publish", "status": "failed"},
    })

    assert registry.get("default") is None
    assert registry.get("workspace-b") is None
    assert existing.runtime.perception.recent("workspace-a") == ()


@pytest.mark.asyncio
async def test_bridge_deduplicates_events_and_ignores_unrecognized_types():
    registry = OrganismRegistry()
    organism = registry.get_or_create("workspace-a")
    bridge = OrganismEventBridge(registry)
    bus = _Bus()
    await bridge.start(bus)

    event = {
        "event_name": "task_failed",
        "event_id": "evt-duplicate",
        "workspace_id": "workspace-a",
        "payload": {"action": "publish", "status": "failed"},
    }
    await bus.emit(event)
    await bus.emit(event)
    await bus.emit({
        "event_name": "arbitrary.command",
        "event_id": "evt-not-allowlisted",
        "workspace_id": "workspace-a",
        "payload": {"action": "delete"},
    })

    observations = organism.runtime.perception.recent("workspace-a")
    assert len(observations) == 1
    assert observations[0].kind is ObservationKind.EXECUTION


@pytest.mark.asyncio
async def test_bridge_start_is_idempotent_and_stop_unsubscribes():
    registry = OrganismRegistry()
    organism = registry.get_or_create("workspace-a")
    bridge = OrganismEventBridge(registry)
    bus = _Bus()

    await bridge.start(bus)
    await bridge.start(bus)
    assert len(bus.handlers) == 1

    await bridge.stop()
    assert bus.handlers == []
    await bus.emit({
        "event_name": "service_heartbeat",
        "event_id": "evt-after-stop",
        "workspace_id": "workspace-a",
        "payload": {"status": "healthy"},
    })
    assert organism.runtime.perception.recent("workspace-a") == ()
