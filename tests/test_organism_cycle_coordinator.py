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
