"""Regression coverage for JobService event publication across scheduler loops."""

import asyncio
import threading
import unittest

from services.job_service import JobService


class _LoopBoundBus:
    def __init__(self, owner_loop):
        self._loop = owner_loop
        self.called = False

    async def publish(self, event_name, payload):
        if asyncio.get_running_loop() is not self._loop:
            raise AssertionError("publish must run on the event bus owner loop")
        self.called = True
        return "event-1"


class JobServiceEventLoopBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_emit_from_scheduler_thread_uses_bus_owner_loop(self):
        owner_loop = asyncio.get_running_loop()
        bus = _LoopBoundBus(owner_loop)
        service = JobService.__new__(JobService)
        service._event_bus = bus

        worker = threading.Thread(
            target=lambda: asyncio.run(
                service._emit("job.status_changed", {"job_id": "job-test"})
            ),
            daemon=True,
        )
        worker.start()
        await asyncio.wait_for(asyncio.to_thread(worker.join), timeout=3)

        self.assertFalse(worker.is_alive())
        self.assertTrue(bus.called)


if __name__ == "__main__":
    unittest.main()
