"""
Observability metrics collector — M1 Architecture Improvement.

Thread-safe, in-process metrics store.  Records timing and counts for:

  - External HTTP calls  (latency, client name, status code)
  - AI provider calls   (latency, provider, operation, success)
  - Render operations   (latency, type, cache hit/miss)
  - Webhook processing  (latency)
  - Database queries    (latency, operation)

Exposes ``get_snapshot()`` for the ``GET /api/metrics`` endpoint.
No external dependencies — pure Python, zero I/O.

Usage::

    from utils.metrics import metrics

    # Record directly
    metrics.record_ai("gemini", "chat", latency_ms=320.5, success=True)

    # Or use the context-manager (automatically records timing + success)
    with metrics.timed("render.card"):
        render_the_card()
"""

import contextlib
import threading
import time
from collections import defaultdict, deque
from typing import Any, Dict, Generator, Optional

# How many recent timing samples to keep per category (FIFO ring)
_HISTORY_LEN = 200


def _percentile(sorted_data: list, p: float) -> float:
    if not sorted_data:
        return 0.0
    idx = max(0, int(len(sorted_data) * p) - 1)
    return round(sorted_data[idx], 2)


class MetricsCollector:
    """
    Collects and aggregates operational metrics.
    All public methods are thread-safe.
    """

    def __init__(self):
        self._lock     = threading.Lock()
        self._counters: Dict[str, int]   = defaultdict(int)
        self._timings:  Dict[str, deque] = defaultdict(lambda: deque(maxlen=_HISTORY_LEN))

    # ------------------------------------------------------------------
    # Recording — HTTP
    # ------------------------------------------------------------------

    def record_http(self, client_name: str, status: int, latency_ms: float) -> None:
        cat = f"http.{client_name}"
        with self._lock:
            self._timings[cat].append(latency_ms)
            self._counters[f"{cat}.requests"] += 1
            if status >= 400:
                self._counters[f"{cat}.errors"] += 1

    # ------------------------------------------------------------------
    # Recording — AI
    # ------------------------------------------------------------------

    def record_ai(self, provider: str, operation: str, latency_ms: float, success: bool) -> None:
        cat = f"ai.{provider}.{operation}"
        with self._lock:
            self._timings[cat].append(latency_ms)
            self._counters[f"{cat}.calls"] += 1
            if not success:
                self._counters[f"{cat}.errors"] += 1

    # ------------------------------------------------------------------
    # Recording — Render
    # ------------------------------------------------------------------

    def record_render(self, render_type: str, latency_ms: float, cached: bool = False) -> None:
        cat = f"render.{render_type}"
        with self._lock:
            self._timings[cat].append(latency_ms)
            self._counters[f"{cat}.renders"] += 1
            if cached:
                self._counters[f"{cat}.cache_hits"] += 1

    # ------------------------------------------------------------------
    # Recording — Webhook
    # ------------------------------------------------------------------

    def record_webhook(self, latency_ms: float, success: bool = True) -> None:
        with self._lock:
            self._timings["webhook"].append(latency_ms)
            self._counters["webhook.requests"] += 1
            if not success:
                self._counters["webhook.errors"] += 1

    # ------------------------------------------------------------------
    # Recording — Database
    # ------------------------------------------------------------------

    def record_db(self, operation: str, latency_ms: float, success: bool = True) -> None:
        cat = f"db.{operation}"
        with self._lock:
            self._timings[cat].append(latency_ms)
            self._counters[f"{cat}.calls"] += 1
            if not success:
                self._counters[f"{cat}.errors"] += 1

    # ------------------------------------------------------------------
    # Generic counter
    # ------------------------------------------------------------------

    def increment(self, counter: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[counter] += amount

    # ------------------------------------------------------------------
    # Context manager — timed block
    # ------------------------------------------------------------------

    @contextlib.contextmanager
    def timed(
        self,
        category: str,
        record_fn: Optional[str] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        """
        Context manager that records timing for any block of code.

        ``category`` is a dot-separated name stored in timings
        (e.g. ``"render.card"``, ``"content.generate_post"``).

        A mutable ``ctx`` dict is yielded so callers can signal failure::

            with metrics.timed("render.card") as ctx:
                try:
                    result = render()
                except Exception:
                    ctx["success"] = False
                    raise

        By default ``ctx["success"]`` is ``True``; set it to ``False`` to
        count the call as an error.
        """
        ctx = {"success": True}
        t0  = time.monotonic()
        try:
            yield ctx
        finally:
            ms = (time.monotonic() - t0) * 1000
            with self._lock:
                self._timings[category].append(ms)
                self._counters[f"{category}.calls"] += 1
                if not ctx["success"]:
                    self._counters[f"{category}.errors"] += 1

    # ------------------------------------------------------------------
    # Snapshot for /api/metrics
    # ------------------------------------------------------------------

    def get_snapshot(self) -> Dict[str, Any]:
        """Return a JSON-serialisable snapshot of all current metrics."""
        with self._lock:
            counters = dict(self._counters)
            timings: Dict[str, Any] = {}
            for cat, dq in self._timings.items():
                if not dq:
                    continue
                lst = sorted(dq)
                n   = len(lst)
                timings[cat] = {
                    "count":   n,
                    "mean_ms": round(sum(lst) / n, 2),
                    "min_ms":  round(lst[0], 2),
                    "max_ms":  round(lst[-1], 2),
                    "p50_ms":  _percentile(lst, 0.50),
                    "p95_ms":  _percentile(lst, 0.95),
                    "p99_ms":  _percentile(lst, 0.99),
                }

        return {"counters": counters, "timings": timings}

    def reset(self) -> None:
        """Reset all metrics.  Intended for testing only."""
        with self._lock:
            self._counters.clear()
            self._timings.clear()


# Module-level singleton used across the platform
metrics = MetricsCollector()
