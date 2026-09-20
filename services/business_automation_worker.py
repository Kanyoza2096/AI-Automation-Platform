"""Durable worker for scheduled internal business automations."""
from __future__ import annotations
import asyncio
import logging
from services.business_automation import business_automation_engine

logger = logging.getLogger("kanyoza.business_automation_worker")

async def run(stop_event: asyncio.Event, interval_seconds: int = 15) -> None:
    while not stop_event.is_set():
        try:
            await business_automation_engine.run_due(limit=20)
        except Exception:
            logger.exception("business automation dispatch cycle failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            pass
