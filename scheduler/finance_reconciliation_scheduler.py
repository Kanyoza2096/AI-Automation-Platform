"""Durable Finance payment reconciliation scheduler."""
from __future__ import annotations
import logging, os, threading, time
from typing import Any, Optional
from core.distributed_lock import DistributedLock
from services.reconciliation_orchestrator import ReconciliationOrchestrator

logger = logging.getLogger("kanyoza.scheduler.finance_reconciliation")

class FinancePaymentReconciliationScheduler:
    def __init__(self, repository: Any, stripe_adapter: Any, *, redis_client=None, orchestrator=None, interval_seconds=300, batch_limit=100, lock_timeout_seconds=240):
        self.repository = repository
        self.stripe_adapter = stripe_adapter
        self.redis_client = redis_client
        self.orchestrator = orchestrator or ReconciliationOrchestrator()
        self.interval_seconds = max(30, int(interval_seconds))
        self.batch_limit = max(1, min(int(batch_limit), 500))
        self.lock_timeout_seconds = max(30, int(lock_timeout_seconds))
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_result = None
        self._run_count = 0

    def start(self):
        if self._running: return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="finance-reconciliation-scheduler")
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread and self._thread.is_alive(): self._thread.join(timeout=min(10.0, float(self.interval_seconds)))
        self._thread = None

    def status(self):
        return {"running": self._running, "interval_seconds": self.interval_seconds, "batch_limit": self.batch_limit, "last_result": self._last_result, "run_count": self._run_count}

    def run_once(self):
        lock = DistributedLock("finance-payment-reconciliation-cycle", self.redis_client, timeout_seconds=self.lock_timeout_seconds, allow_local_fallback=False)
        if not lock.acquire(require_distributed=True): return None
        try:
            result = self.orchestrator.reconcile_finance_payments(repository=self.repository, stripe_adapter=self.stripe_adapter, limit=self.batch_limit)
            self._last_result = result; self._run_count += 1
            return result
        except Exception:
            logger.exception("Finance reconciliation cycle failed")
            return None
        finally: lock.release()

    def _run_loop(self):
        self.run_once()
        while self._running:
            for _ in range(self.interval_seconds):
                if not self._running: break
                time.sleep(1)
            if self._running: self.run_once()


def build_finance_reconciliation_scheduler(*, repository, redis_client=None):
    token = os.getenv("STRIPE_SECRET_KEY", "").strip()
    if not token:
        logger.warning("Finance reconciliation scheduler not started: STRIPE_SECRET_KEY is not configured")
        return None
    from services.connectors.modern_adapters import StripeAdapter
    adapter = StripeAdapter(token=token, allow_mutations=False, allow_high_risk=False)
    return FinancePaymentReconciliationScheduler(
        repository, adapter, redis_client=redis_client,
        interval_seconds=int(os.getenv("FINANCE_RECONCILIATION_INTERVAL_SECONDS", "300")),
        batch_limit=int(os.getenv("FINANCE_RECONCILIATION_BATCH_LIMIT", "100")),
    )
