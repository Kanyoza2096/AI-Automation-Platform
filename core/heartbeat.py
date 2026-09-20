"""
Kanyoza Heartbeat System v1.0
==============================

Publishes periodic "I'm alive" signals from every engine.
Like a digital pulse confirming the organism is alive.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger("kanyoza.heartbeat")


class HeartbeatPublisher:
    """
    Publishes heartbeat signals to the event bus.
    
    Every engine sends a heartbeat. If heartbeats stop,
    the system knows something is wrong.
    """
    
    VERSION = "1.0.0"
    
    def __init__(self, event_bus=None, container=None):
        self._event_bus = event_bus
        self._container = container
        self._running = False
        self._beat_task: Optional[asyncio.Task] = None
        self._beat_count = 0
        self._last_beat_time: Optional[datetime] = None
        self._subscribers: Dict[str, datetime] = {}
        self._last_log_count = 0
        self._log_interval_seconds = max(30, int(os.getenv("KANYOZA_HEARTBEAT_LOG_INTERVAL", "60")))
        self._last_log_monotonic = 0.0
    
    @property
    def service_name(self) -> str:
        return "heartbeat"
    
    async def start(self, interval_seconds: int = 10) -> None:
        """Start the heartbeat."""
        if self._running:
            return
        
        self._running = True
        self._beat_task = asyncio.create_task(
            self._beat_loop(interval_seconds),
            name="heartbeat-loop",
        )
        logger.info("💓 Heartbeat system started (every %ds)", interval_seconds)
    
    async def stop(self) -> None:
        """Stop the heartbeat."""
        self._running = False
        if self._beat_task and not self._beat_task.done():
            self._beat_task.cancel()
            try:
                await self._beat_task
            except asyncio.CancelledError:
                pass
    
    async def _beat_loop(self, interval_seconds: int) -> None:
        """Main heartbeat loop."""
        while self._running:
            try:
                await self._publish_beat()
            except Exception as exc:
                logger.debug("Heartbeat publish failed: %s", exc)
            
            await asyncio.sleep(interval_seconds)
    
    async def _publish_beat(self) -> None:
        """Publish a single heartbeat."""
        self._beat_count += 1
        self._last_beat_time = datetime.now(timezone.utc)
        
        heartbeat_data = {
            "id": f"beat_{uuid.uuid4().hex[:8]}",
            "count": self._beat_count,
            "timestamp": self._last_beat_time.isoformat(),
            "version": self.VERSION,
        }
        
        # Publish to event bus
        bus = self._event_bus
        if bus is None and self._container:
            from core.unified_bus import UnifiedEventBus
            bus = self._container.try_resolve(UnifiedEventBus)
        
        if bus:
            try:
                # Platform-scoped pulse — not a tenant event
                await bus.publish(
                    "heartbeat.system",
                    heartbeat_data,
                    source="heartbeat",
                    workspace_id="platform",
                )
            except Exception as exc:
                logger.warning("💓 Heartbeat publish degraded: %s", str(exc)[:200])

        # Production liveness signal: one compact INFO record per minute by
        # default. Individual 10s beats remain internal/event-bus signals.
        now_mono = time.monotonic()
        if (now_mono - self._last_log_monotonic) >= self._log_interval_seconds:
            self._last_log_monotonic = now_mono
            logger.info(
                "💓 Organism heartbeat: count=%d running=%s last_beat=%s",
                self._beat_count,
                self._running,
                self._last_beat_time.isoformat() if self._last_beat_time else None,
            )
    
    def get_status(self) -> Dict[str, Any]:
        """Get heartbeat status."""
        return {
            "version": self.VERSION,
            "running": self._running,
            "beat_count": self._beat_count,
            "last_beat_time": self._last_beat_time.isoformat() if self._last_beat_time else None,
            "alive": self._running and self._last_beat_time is not None,
        }


# Global heartbeat instance
heartbeat = HeartbeatPublisher()


async def init_heartbeat(container=None, event_bus=None) -> HeartbeatPublisher:
    """Initialize heartbeat."""
    global heartbeat
    heartbeat._container = container
    heartbeat._event_bus = event_bus
    await heartbeat.start(interval_seconds=10)
    return heartbeat


async def shutdown_heartbeat() -> None:
    """Stop heartbeat."""
    global heartbeat
    await heartbeat.stop()


__all__ = [
    "HeartbeatPublisher",
    "heartbeat",
    "init_heartbeat",
    "shutdown_heartbeat",
]