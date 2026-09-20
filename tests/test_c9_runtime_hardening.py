from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path


def test_worker_health_probe_accepts_fresh_heartbeat(tmp_path, monkeypatch):
    heartbeat = tmp_path / "heartbeat.json"
    heartbeat.write_text(json.dumps({"pid": os.getpid(), "mode": "workers", "timestamp": time.time()}))
    monkeypatch.setenv("KDO_HEARTBEAT_FILE", str(heartbeat))
    from scripts.worker_health import main
    assert main() == 0


def test_worker_health_probe_rejects_stale_heartbeat(tmp_path, monkeypatch):
    heartbeat = tmp_path / "heartbeat.json"
    heartbeat.write_text(json.dumps({"pid": os.getpid(), "mode": "workers", "timestamp": time.time() - 120}))
    monkeypatch.setenv("KDO_HEARTBEAT_FILE", str(heartbeat))
    from scripts.worker_health import main
    assert main() == 1


def test_worker_drain_waits_before_cancelling():
    from workers.runtime import _wait_for_tasks

    async def scenario():
        finished = False
        async def job():
            nonlocal finished
            await asyncio.sleep(0.03)
            finished = True
        task = asyncio.create_task(job())
        await _wait_for_tasks([task], 0.2)
        assert finished is True
        assert task.done()

    asyncio.run(scenario())
