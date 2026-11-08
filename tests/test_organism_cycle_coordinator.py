import pytest

from core.organism import OrganismRegistry
from organism.coordinator import cycle_started_organisms
from organism.perception import Observation, ObservationKind


@pytest.mark.asyncio
async def test_coordinator_synthesizes_observations_for_started_workspace_only():
    registry = OrganismRegistry()
    started = await registry.ensure_started("workspace-a")
    dormant = registry.get_or_create("workspace-b")
    started.runtime.ingest(
        Observation(
            workspace_id="workspace-a",
            kind=ObservationKind.HEALTH,
            source="test",
            value={"health": 0.25},
        )
    )

    result = await cycle_started_organisms(registry)

    assert result.attempted == 1
    assert result.completed == 1
    assert result.failed == 0
    assert result.workspace_ids == ("workspace-a",)
    assert started.runtime.state is not None
    assert started.runtime.state.health == 0.25
    assert dormant.runtime.state is None


@pytest.mark.asyncio
async def test_coordinator_does_not_create_or_start_unknown_workspaces():
    registry = OrganismRegistry()
    result = await cycle_started_organisms(registry)

    assert result.attempted == 0
    assert result.completed == 0
    assert result.failed == 0
    assert registry.list_ids() == []


@pytest.mark.asyncio
async def test_coordinator_bounds_workspace_batch():
    registry = OrganismRegistry()
    await registry.ensure_started("workspace-a")
    await registry.ensure_started("workspace-b")

    result = await cycle_started_organisms(registry, max_workspaces=1)

    assert result.attempted == 1
    assert result.completed == 1
    assert len(result.workspace_ids) == 1


@pytest.mark.asyncio
async def test_coordinator_persists_fresh_trusted_health_goal_as_suggest_only():
    from datetime import datetime, timezone
    from types import SimpleNamespace

    registry = OrganismRegistry()
    organism = await registry.ensure_started("workspace-a")
    runtime = organism.runtime

    class ProposalStore:
        def __init__(self):
            self.items = []

        async def persist(self, proposal):
            self.items.append(proposal)
            return 1

    store = ProposalStore()
    runtime.persistence_store = SimpleNamespace(client=object())
    runtime.goal_proposal_store = store
    runtime.ingest(Observation(
        workspace_id="workspace-a",
        kind=ObservationKind.HEALTH,
        source="unified_event:service_heartbeat",
        value={"service": "api-worker", "status": "degraded"},
        confidence=0.8,
        source_trust=0.8,
        observed_at=datetime.now(timezone.utc),
        observation_id="event:evt-health-1",
        metadata={"event_id": "evt-health-1"},
    ))

    result = await cycle_started_organisms(registry)

    assert result.completed == 1
    assert result.failed == 0
    assert len(store.items) == 1
    assert store.items[0].workspace_id == "workspace-a"
    assert store.items[0].source_event_ids == ("evt-health-1",)
    assert store.items[0].authority == "suggest"
    assert store.items[0].status == "proposed"


@pytest.mark.asyncio
async def test_coordinator_replay_does_not_persist_same_health_event_twice():
    from datetime import datetime, timezone
    from types import SimpleNamespace

    registry = OrganismRegistry()
    organism = await registry.ensure_started("workspace-a")
    runtime = organism.runtime

    class ProposalStore:
        def __init__(self):
            self.items = []

        async def persist(self, proposal):
            self.items.append(proposal)
            return 1

    store = ProposalStore()
    runtime.persistence_store = SimpleNamespace(client=object())
    runtime.goal_proposal_store = store
    runtime.ingest(Observation(
        workspace_id="workspace-a",
        kind=ObservationKind.HEALTH,
        source="unified_event:engine_alert",
        value={"component": "job-worker", "status": "down"},
        confidence=0.9,
        source_trust=0.9,
        observed_at=datetime.now(timezone.utc),
        observation_id="event:evt-health-replay",
        metadata={"event_id": "evt-health-replay"},
    ))

    first = await cycle_started_organisms(registry)
    second = await cycle_started_organisms(registry)

    assert first.completed == second.completed == 1
    assert len(store.items) == 1


@pytest.mark.asyncio
async def test_coordinator_does_not_create_proposals_from_stale_health_events():
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    registry = OrganismRegistry()
    organism = await registry.ensure_started("workspace-a")
    runtime = organism.runtime

    class ProposalStore:
        def __init__(self):
            self.items = []

        async def persist(self, proposal):
            self.items.append(proposal)
            return 1

    store = ProposalStore()
    runtime.persistence_store = SimpleNamespace(client=object())
    runtime.goal_proposal_store = store
    runtime.ingest(Observation(
        workspace_id="workspace-a",
        kind=ObservationKind.HEALTH,
        source="unified_event:service_heartbeat",
        value={"service": "api-worker", "status": "degraded"},
        confidence=0.9,
        source_trust=0.9,
        observed_at=datetime.now(timezone.utc) - timedelta(hours=2),
        observation_id="event:evt-old",
        metadata={"event_id": "evt-old"},
    ))

    result = await cycle_started_organisms(registry)

    assert result.completed == 1
    assert result.failed == 0
    assert store.items == []


@pytest.mark.asyncio
async def test_coordinator_fails_closed_when_fresh_goal_cannot_be_persisted():
    from datetime import datetime, timezone
    from types import SimpleNamespace

    registry = OrganismRegistry()
    organism = await registry.ensure_started("workspace-a")
    runtime = organism.runtime

    class FailingStore:
        async def persist(self, _proposal):
            raise RuntimeError("database unavailable")

    runtime.persistence_store = SimpleNamespace(client=object())
    runtime.goal_proposal_store = FailingStore()
    runtime.ingest(Observation(
        workspace_id="workspace-a",
        kind=ObservationKind.HEALTH,
        source="unified_event:service_heartbeat",
        value={"service": "api-worker", "status": "degraded"},
        confidence=0.9,
        source_trust=0.9,
        observed_at=datetime.now(timezone.utc),
        observation_id="event:evt-fail",
        metadata={"event_id": "evt-fail"},
    ))

    result = await cycle_started_organisms(registry)

    assert result.completed == 0
    assert result.failed == 1
    assert "event:evt-fail" not in getattr(runtime, "_goal_proposal_observation_ids", set())
