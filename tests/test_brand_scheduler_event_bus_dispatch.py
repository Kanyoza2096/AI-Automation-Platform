import asyncio
import threading

import pytest

from scheduler.brand_scheduler import BrandScheduler


class FakeBrand:
    id = "brand-test"
    name = "test-brand"
    workspace_id = "workspace-test"


class AsyncEventBus:
    def __init__(self):
        self.calls = []

    async def publish_event(self, event_name, payload):
        await asyncio.sleep(0)
        self.calls.append((event_name, payload))


def make_scheduler(bus, loop):
    scheduler = BrandScheduler.__new__(BrandScheduler)
    scheduler.brand = FakeBrand()
    scheduler._event_bus = bus
    scheduler._event_loop = loop
    return scheduler


def test_emit_dispatches_coroutine_to_owner_loop_from_worker_thread():
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    def run_owner_loop():
        asyncio.set_event_loop(loop)
        loop.call_soon(ready.set)
        loop.run_forever()

    thread = threading.Thread(target=run_owner_loop, daemon=True)
    thread.start()
    assert ready.wait(timeout=2)
    bus = AsyncEventBus()
    scheduler = make_scheduler(bus, loop)

    try:
        worker = threading.Thread(
            target=scheduler._emit,
            args=("scheduler.test", {"ok": True}),
        )
        worker.start()
        worker.join(timeout=2)
        assert not worker.is_alive()
        assert bus.calls == [("scheduler.test", {"ok": True, "workspace_id": "workspace-test", "brand_id": "brand-test"})]
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2)
        loop.close()


def test_emit_schedules_without_blocking_when_called_on_owner_loop():
    loop = asyncio.new_event_loop()
    bus = AsyncEventBus()
    scheduler = make_scheduler(bus, loop)

    async def exercise():
        scheduler._emit("scheduler.test", {"ok": True})
        await asyncio.sleep(0)

    try:
        loop.run_until_complete(exercise())
        assert bus.calls == [("scheduler.test", {"ok": True})]
    finally:
        loop.close()


def test_emit_closes_awaitable_when_owner_loop_is_unavailable(caplog):
    loop = asyncio.new_event_loop()
    bus = AsyncEventBus()
    scheduler = make_scheduler(bus, loop)

    try:
        with caplog.at_level("ERROR"):
            scheduler._emit("scheduler.test", {"ok": True})
        assert "owner event loop unavailable" in caplog.text
        assert bus.calls == []
    finally:
        loop.close()


def test_emit_refuses_missing_or_synthetic_workspace(caplog):
    loop = asyncio.new_event_loop()
    bus = AsyncEventBus()
    scheduler = make_scheduler(bus, loop)
    scheduler.brand.workspace_id = "default"

    try:
        with caplog.at_level("ERROR"):
            scheduler._emit("scheduler.test", {"ok": True})
        assert "concrete brand/workspace identity unavailable" in caplog.text
        assert bus.calls == []
    finally:
        loop.close()


def test_workflow_context_binds_brand_and_workspace():
    loop = asyncio.new_event_loop()
    scheduler = make_scheduler(AsyncEventBus(), loop)
    try:
        context = scheduler._build_workflow_context(job_id="job-1", source="scheduled")
        assert context["brand"] is scheduler.brand
        assert context["brand_id"] == "brand-test"
        assert context["workspace_id"] == "workspace-test"
        assert context["job_id"] == "job-1"
    finally:
        loop.close()


def test_workflow_context_rejects_synthetic_workspace():
    loop = asyncio.new_event_loop()
    scheduler = make_scheduler(AsyncEventBus(), loop)
    scheduler.brand.workspace_id = "unbound"
    try:
        with pytest.raises(ValueError, match="concrete brand and tenant workspace"):
            scheduler._build_workflow_context(job_id="job-1")
    finally:
        loop.close()
