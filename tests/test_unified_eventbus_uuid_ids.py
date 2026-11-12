import uuid

import pytest

from core.unified_bus import EventBusConfig, EventMetadata, UnifiedEventBus


def test_event_metadata_default_id_is_uuid_compatible():
    event_id = EventMetadata().event_id
    assert str(uuid.UUID(event_id)) == event_id


@pytest.mark.asyncio
async def test_unified_bus_publishes_uuid_compatible_id_before_persistence():
    bus = UnifiedEventBus(
        supabase_client=object(),
        config=EventBusConfig(persistence_flush_interval=0.01),
    )

    async def persist_batch(_entries):
        return True

    bus._persist_batch = persist_batch
    try:
        event_id = await bus.publish(
            "heartbeat.beat",
            {"component": "test"},
            source="uuid-regression-test",
            workspace_id="platform",
        )
        assert event_id is not None
        assert str(uuid.UUID(event_id)) == event_id
        assert bus._event_store[-1].event_id == event_id
    finally:
        await bus.stop()
