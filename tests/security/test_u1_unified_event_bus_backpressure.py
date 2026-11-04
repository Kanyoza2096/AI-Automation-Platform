import asyncio
from datetime import datetime, timezone

from core.unified_bus import EventBusConfig, EventStoreEntry, UnifiedEventBus


class _Result:
    data = []


class _Query:
    def __init__(self, client):
        self.client = client

    def upsert(self, records, on_conflict=None):
        self.client.calls.append((records, on_conflict))
        return self

    def execute(self):
        if self.client.failures:
            self.client.failures -= 1
            raise RuntimeError("temporary database outage")
        return _Result()


class _Supabase:
    def __init__(self, failures=0):
        self.failures = failures
        self.calls = []

    def table(self, name):
        assert name == "events"
        return _Query(self)


def _entry(event_id="evt-test-1"):
    return EventStoreEntry(
        event_id=event_id,
        event_name="test.event",
        payload={"value": 1},
        correlation_id=None,
        sequence_number=1,
        published_at=datetime.now(timezone.utc),
        source_engine="unit-test",
    )


def test_persistence_batch_failure_is_reported_and_retry_is_idempotent():
    async def run():
        client = _Supabase(failures=1)
        bus = UnifiedEventBus(supabase_client=client)

        assert await bus._persist_batch([_entry()]) is False
        assert await bus._persist_batch([_entry()]) is True
        assert len(client.calls) == 2
        assert client.calls[0][1] == "id"
        assert client.calls[1][1] == "id"

    asyncio.run(run())


def test_flush_preserves_failed_persistence_batch_in_queue():
    async def run():
        client = _Supabase(failures=1)
        bus = UnifiedEventBus(
            supabase_client=client,
            config=EventBusConfig(max_queue_size=2),
        )
        entry = _entry()
        future = asyncio.get_running_loop().create_future()
        await bus._persistence_queue.put((entry, future))

        await bus._flush_persistence()

        assert bus._persistence_queue.qsize() == 1
        retained, retained_future = bus._persistence_queue.get_nowait()
        assert retained.event_id == entry.event_id
        assert retained_future is future
        assert future.done()
        assert isinstance(future.exception(), RuntimeError)
        bus._persistence_queue.task_done()

    asyncio.run(run())


def test_persistence_batch_success_returns_true():
    async def run():
        bus = UnifiedEventBus(supabase_client=_Supabase())
        assert await bus._persist_batch([_entry()]) is True

    asyncio.run(run())
