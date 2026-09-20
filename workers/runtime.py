"""Shared runtime entrypoint for production worker processes."""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import json
import time
from pathlib import Path
from types import SimpleNamespace

from dependency_registry import build_container
from lifecycle import boot_infrastructure, graceful_shutdown

logger = logging.getLogger("kanyoza.workers.runtime")

HEARTBEAT_FILE = Path(os.getenv("KDO_HEARTBEAT_FILE", "/tmp/kdo-runtime-heartbeat.json"))


def _write_heartbeat(mode: str) -> None:
    """Publish local process liveness for non-HTTP container health checks."""
    payload = {"pid": os.getpid(), "mode": mode, "timestamp": time.time()}
    tmp = HEARTBEAT_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(HEARTBEAT_FILE)


def _clear_heartbeat() -> None:
    try:
        HEARTBEAT_FILE.unlink(missing_ok=True)
    except OSError:
        logger.warning("Unable to remove worker heartbeat file %s", HEARTBEAT_FILE)


async def _heartbeat_loop(mode: str, stopping: asyncio.Event) -> None:
    while not stopping.is_set():
        try:
            _write_heartbeat(mode)
        except OSError:
            logger.exception("Failed to write worker heartbeat")
        try:
            await asyncio.wait_for(stopping.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pass


async def _wait_for_tasks(tasks: list[asyncio.Task], timeout: float) -> None:
    pending = {task for task in tasks if task is not None and not task.done()}
    if not pending:
        return
    done, pending = await asyncio.wait(pending, timeout=timeout)
    if pending:
        logger.warning("Graceful worker drain timed out; cancelling %d task(s)", len(pending))
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)


async def run_service(mode: str) -> None:
    os.environ["SERVICE_MODE"] = mode
    container = build_container()
    app = SimpleNamespace(state=SimpleNamespace(boot_completed=False, container=container, boot_warnings=[]))
    stopping = asyncio.Event()
    try:
        drain_timeout = max(1.0, float(os.getenv("KDO_WORKER_DRAIN_TIMEOUT_SECONDS", "30")))
    except ValueError:
        drain_timeout = 30.0

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stopping.set)
        except (NotImplementedError, RuntimeError):
            pass

    await boot_infrastructure(app, container)
    app.state.boot_completed = True

    heartbeat_task = asyncio.create_task(_heartbeat_loop(mode, stopping), name="runtime-heartbeat")
    automation_task = None
    action_outbox_task = None
    if mode in {"workers", "full"}:
        from services.business_automation_worker import run as run_business_automation
        automation_task = asyncio.create_task(run_business_automation(stopping), name="business-automation-worker")
        from services.action_outbox_worker import run as run_action_outbox
        action_outbox_task = asyncio.create_task(run_action_outbox(stopping), name="action-outbox-worker")
    logger.info("Kanyoza %s service is ready.", mode)
    try:
        await stopping.wait()
    finally:
        # Signal-driven shutdown is cooperative first: workers finish their
        # current durable cycle and only then receive cancellation. This keeps
        # the Action Outbox/automation lease model authoritative during deploys.
        stopping.set()
        await _wait_for_tasks([t for t in (action_outbox_task, automation_task, heartbeat_task) if t is not None], drain_timeout)
        _clear_heartbeat()
        app.state.boot_completed = False
        await graceful_shutdown(container)
