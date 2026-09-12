"""Shared runtime entrypoint for production worker processes."""
from __future__ import annotations

import asyncio
import logging
import os
import signal
from types import SimpleNamespace

from dependency_registry import build_container
from lifecycle import boot_infrastructure, graceful_shutdown

logger = logging.getLogger("kanyoza.workers.runtime")


async def run_service(mode: str) -> None:
    os.environ["SERVICE_MODE"] = mode
    container = build_container()
    app = SimpleNamespace(state=SimpleNamespace(boot_completed=False, container=container, boot_warnings=[]))
    stopping = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stopping.set)
        except (NotImplementedError, RuntimeError):
            pass

    await boot_infrastructure(app, container)
    app.state.boot_completed = True

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
        if action_outbox_task is not None:
            action_outbox_task.cancel()
            try:
                await action_outbox_task
            except asyncio.CancelledError:
                pass
        if automation_task is not None:
            automation_task.cancel()
            try:
                await automation_task
            except asyncio.CancelledError:
                pass
        app.state.boot_completed = False
        await graceful_shutdown(container)
