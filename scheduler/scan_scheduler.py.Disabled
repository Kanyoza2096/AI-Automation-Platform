"""
Scan scheduler — runs the Code Guardian security scan on a recurring interval.
"""

import logging
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Callable, Optional

logger = logging.getLogger("kanyoza.scheduler.scan")


class ScanScheduler:
    """
    Runs a callback every N hours in a daemon thread.
    Runs once immediately on start, then repeats on the configured interval.
    Uses 1-second sleep ticks for responsive shutdown.
    """

    def __init__(self, callback: Callable, hours: int = 24):
        self._callback = callback
        self._interval = timedelta(hours=max(1, hours))
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_run: Optional[datetime] = None
        self._run_count: int = 0

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="scan-scheduler",
        )
        self._thread.start()
        logger.info(
            "ScanScheduler started — interval: %dh",
            int(self._interval.total_seconds() / 3600),
        )

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("ScanScheduler stopped")

    def status(self) -> dict:
        return {
            "running": self._running,
            "interval_hours": int(self._interval.total_seconds() / 3600),
            "last_run": self._last_run.isoformat() if self._last_run else None,
            "run_count": self._run_count,
        }

    def _run_loop(self) -> None:
        # Run immediately on startup
        self._execute()

        while self._running:
            elapsed = 0.0
            interval_s = self._interval.total_seconds()
            while self._running and elapsed < interval_s:
                time.sleep(1)
                elapsed += 1

            if self._running:
                self._execute()

    def _execute(self) -> None:
        try:
            logger.info("ScanScheduler: running guardian scan")
            self._callback()
            self._last_run = datetime.now(timezone.utc)
            self._run_count += 1
            logger.info("ScanScheduler: scan complete (run #%d)", self._run_count)
        except Exception as exc:
            logger.error("ScanScheduler: scan failed: %s", exc)
