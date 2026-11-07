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



def test_defer_policy_waits_for_dispatch_queue_capacity():
    async def run():
        bus = UnifiedEventBus(
            config=EventBusConfig(max_queue_size=1),
        )
        bus._running = True
        await bus._dispatch_queue.put(("occupied", {}, "test", "existing", None, None, None, None, None))

        publish_task = asyncio.create_task(
            bus.publish("test.event", {"value": 2}, source="test", workspace_id="ws-test")
        )
        await asyncio.sleep(0)
        assert not publish_task.done()

        bus._dispatch_queue.get_nowait()
        bus._dispatch_queue.task_done()
        event_id = await asyncio.wait_for(publish_task, timeout=1)

        assert event_id
        assert bus._dispatch_queue.qsize() == 1

    asyncio.run(run())



def test_dispatch_waits_for_all_registered_handlers():
    async def run():
        bus = UnifiedEventBus()
        seen = []

        async def handler(event):
            await asyncio.sleep(0)
            seen.append(event["event_id"])

        await bus.subscribe("task.completed", handler)
        await bus._dispatch_event(
            "task.completed", {}, "test", "event-dispatch-1"
        )

        assert seen == ["event-dispatch-1"]

    asyncio.run(run())


def test_in_process_dead_letter_can_be_replayed_and_cleared():
    async def run():
        bus = UnifiedEventBus()
        attempts = {"count": 0}

        async def flaky_handler(event):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise RuntimeError("temporary handler failure")

        await bus.subscribe("task.failed", flaky_handler)
        await bus._invoke_handler(
            flaky_handler,
            "task.failed",
            {"task_id": "task-1"},
            "test",
            "event-dlq-1",
        )

        assert len(bus.get_dead_letters()) == 1
        assert await bus.replay_dead_letter("event-dlq-1") == 1
        assert bus.get_dead_letters() == []
        assert attempts["count"] == 2

    asyncio.run(run())


def test_publish_waits_for_durable_commit_before_dispatch():
    import threading

    class _BlockingQuery(_Query):
        def execute(self):
            self.client.started.set()
            if not self.client.release.wait(timeout=5):
                raise TimeoutError("test persistence release timed out")
            self.client.persisted = True
            return _Result()

    class _BlockingSupabase(_Supabase):
        def __init__(self):
            super().__init__()
            self.started = threading.Event()
            self.release = threading.Event()
            self.persisted = False

        def table(self, name):
            assert name == "events"
            return _BlockingQuery(self)

    async def run():
        client = _BlockingSupabase()
        bus = UnifiedEventBus(supabase_client=client)
        handler_called = asyncio.Event()

        async def handler(_event):
            handler_called.set()

        await bus.subscribe("durable.test", handler)
        publish_task = asyncio.create_task(
            bus.publish("durable.test", {"value": "durable"}, source="test", workspace_id="ws-test")
        )
        assert await asyncio.to_thread(client.started.wait, 2)
        assert not publish_task.done()
        assert not handler_called.is_set()

        client.release.set()
        event_id = await asyncio.wait_for(publish_task, timeout=2)
        assert event_id
        assert client.persisted is True
        await asyncio.wait_for(handler_called.wait(), timeout=2)
        await bus.stop()

    asyncio.run(run())
