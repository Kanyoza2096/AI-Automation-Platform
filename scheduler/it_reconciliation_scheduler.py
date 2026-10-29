"""Durable scheduling entrypoint for IT deployment reconciliation.

This scheduler owns timing and distributed coordination only. It does not
execute deployments, create retries, or decide provider outcomes. Each cycle
routes durable uncertain deployments through ReconciliationOrchestrator.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Optional

from core.distributed_lock import DistributedLock
from services.reconciliation_orchestrator import ReconciliationOrchestrator

logger = logging.getLogger("kanyoza.scheduler.it_reconciliation")


class ITDeploymentReconciliationScheduler:
    """Periodic invocation wrapper around the canonical reconciliation path."""

    def __init__(
        self,
        repository: Any,
        render_adapter: Any,
        *,
        redis_client: Any = None,
        orchestrator: Optional[ReconciliationOrchestrator] = None,
        interval_seconds: int = 300,
        batch_limit: int = 100,
        lock_timeout_seconds: int = 240,
        worker_id: str = "it-reconciliation",
    ) -> None:
        self.repository = repository
        self.render_adapter = render_adapter
        self.redis_client = redis_client
        self.orchestrator = orchestrator or ReconciliationOrchestrator()
        self.interval_seconds = max(30, int(interval_seconds))
        self.batch_limit = max(1, min(int(batch_limit), 500))
        self.lock_timeout_seconds = max(30, int(lock_timeout_seconds))
        self.worker_id = str(worker_id).strip() or "it-reconciliation"
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_result: Optional[dict[str, int]] = None
        self._run_count = 0

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="it-reconciliation-scheduler",
        )
        self._thread.start()
        logger.info(
            "IT deployment reconciliation scheduler started (interval=%ss, batch=%s)",
            self.interval_seconds,
            self.batch_limit,
        )

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=min(10.0, float(self.interval_seconds)))
        self._thread = None
        logger.info("IT deployment reconciliation scheduler stopped")

    def status(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "interval_seconds": self.interval_seconds,
            "batch_limit": self.batch_limit,
            "last_result": self._last_result,
            "run_count": self._run_count,
        }

    def run_once(self) -> Optional[dict[str, int]]:
        """Run one reconciler cycle if distributed coordination is available."""
        lock = DistributedLock(
            "it-deployment-reconciliation-cycle",
            self.redis_client,
            timeout_seconds=self.lock_timeout_seconds,
            allow_local_fallback=False,
        )
        if not lock.acquire(require_distributed=True):
            logger.debug("IT reconciliation cycle skipped: distributed lock unavailable or held")
            return None
        try:
            result = self.orchestrator.reconcile_it_deployments(
                repository=self.repository,
                render_adapter=self.render_adapter,
                limit=self.batch_limit,
            )
            self._last_result = result
            self._run_count += 1
            logger.info("IT reconciliation cycle complete: %s", result)
            return result
        except Exception:
            logger.exception("IT reconciliation cycle failed")
            return None
        finally:
            lock.release()

    def _run_loop(self) -> None:
        # Reconcile immediately after worker boot, then continue on the durable
        # scheduler cadence. A failed/locked cycle never becomes a deployment retry.
        self.run_once()
        while self._running:
            for _ in range(self.interval_seconds):
                if not self._running:
                    break
                time.sleep(1)
            if self._running:
                self.run_once()


def build_it_reconciliation_scheduler(*, repository: Any, redis_client: Any = None) -> Optional[ITDeploymentReconciliationScheduler]:
    """Build the scheduler with an explicit read-only Render adapter.

    Reconciliation requires provider read access. It never enables Render
    mutations and does not discover provider credentials through the database.
    """
    token = os.getenv("RENDER_API_KEY", "").strip()
    if not token:
        logger.warning("IT reconciliation scheduler not started: RENDER_API_KEY is not configured")
        return None

    from services.connectors.render_adapter import RenderAdapter

    adapter = RenderAdapter(token=token, allow_mutations=False, allow_high_risk=False)
    return ITDeploymentReconciliationScheduler(
        repository,
        adapter,
        redis_client=redis_client,
        interval_seconds=int(os.getenv("IT_RECONCILIATION_INTERVAL_SECONDS", "300")),
        batch_limit=int(os.getenv("IT_RECONCILIATION_BATCH_LIMIT", "100")),
        worker_id=os.getenv("WORKER_ID", "it-reconciliation"),
    )
