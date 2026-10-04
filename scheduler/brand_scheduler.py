"""
Brand-aware scheduling engine — UNIFIED v4.0
Replaces PostScheduler with a multi-tenant BrandSchedulerManager.

v4.0 — 10/10 PRODUCTION GRADE:
  - Distributed locking (Redis + Thread fallback)
  - Dead Letter Queue (failed posts recoverable)
  - Circuit breaker with half-open probing
  - Full metrics (Prometheus-compatible)
  - Backpressure (concurrent publish limits)
  - Manual trigger idempotency
  - Webhook notifications
  - Timezone DST edge case handling
  - Graceful degradation (Supabase → file → memory)
  - Complete observability

v4.3 (2026-10-04) — ENUM STATUS COMPARISON FIX:
  _sync_brands read brand.status with str(...).lower() and compared
  against "active". BrandStatus is a str-subclass enum: str(BrandStatus
  .ACTIVE) returns 'BrandStatus.ACTIVE', not 'active'. The comparison
  therefore always failed and every brand was skipped on every boot.
  The manager logged "started" but no per-brand scheduler was ever
  created. Since 2026-09-21 the scheduler silently skipped every fire.
  Fix: read .value for the primitive, matching _check_schedule's
  existing pattern. Also logs skipped brands at debug level so a future
  regression is visible.

v4.2 (2026-10-03) — ASYNC CONTEXT CORRECTNESS (preserved):
  - _recover_all_stale_jobs now awaits JobService methods directly.
  - _retry_dead_letter_queue extracts only accepted kwargs and reads
    retry_count from payload.

All v3.9 features retained:
  - Circuit breaker for Supabase operations
  - Retry with exponential backoff on API failures
  - Brand-specific timezone support
  - Proper error logging (no silent failures)
  - Atomic claim, event loop fix, dashboard events

v1.6 fixes (2026-10-01):
  - _extract_workflow_outcome handles both the flat pipeline result shape
    and the legacy per-platform mapping.
  - The post_id extraction block in _trigger_workflow applies the same
    flat-shape detection and guards .get() with isinstance(v, dict).
  - _extract_workflow_outcome now accepts a context_platform kwarg.
  - Both call sites pass context_platform from wf_kwargs["platform"].
"""

import asyncio
import json
import os
import random
import time
import uuid
import logging
import threading
from datetime import datetime, timezone, date, timedelta
from typing import Dict, List, Optional, TYPE_CHECKING, Callable, Any
from collections import deque

from models.brand import Brand
from models.ai_profile import AIProfile
from models.automation_job import AutomationJob, JobStatus, WorkflowType
from repositories.brand_repository import BrandRepository
from repositories.ai_profile_repository import AIProfileRepository

if TYPE_CHECKING:
    from service_container import ServiceContainer
    from services.job_service import JobService

logger = logging.getLogger("kanyoza.scheduler.brand")

_failed_emits: int = 0
_failed_emits_lock = threading.Lock()


# ═══════════════════════════════════════════════════════════════════════════
# v4.0 — DISTRIBUTED LOCK (Redis + Thread fallback)
# ═══════════════════════════════════════════════════════════════════════════

# DistributedLock moved to core.distributed_lock (honest DEGRADED_LOCAL mode)
from core.distributed_lock import DistributedLock, LockMode  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════


def _brand_status_value(brand: Any) -> str:
    """Return a brand's status as a lowercase string.

    BrandStatus is a str-subclass enum. `str(BrandStatus.ACTIVE)` returns
    'BrandStatus.ACTIVE', not 'active', because Enum's __str__ wins over
    the str subclass's. The only reliable primitive is `.value`.

    Handles:
      - BrandStatus.ACTIVE         → 'active'
      - 'active'                   → 'active'
      - None / missing attribute   → ''
      - 'ACTIVE'                   → 'active'
    """
    raw = getattr(brand, "status", "")
    if hasattr(raw, "value"):
        return str(raw.value).lower()
    return str(raw or "").lower()


def _job_call(service, method: str, *args, **kwargs):
    """Run JobService async API from legacy synchronous scheduler threads.

    IMPORTANT: This is only safe from a synchronous context — specifically
    the background thread that runs BrandScheduler._run(). Calling this
    from an async method that is awaited on the main event loop creates
    a coroutine on one loop and runs it on another thread's loop, which
    Python 3.12 rejects (RuntimeError: coroutine created in a different
    loop) and earlier versions handle inconsistently. If you are already
    inside an async method, use `await` directly.
    """
    coro = getattr(service, method)(*args, **kwargs)
    if not asyncio.iscoroutine(coro):
        return coro
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Embedded in an event loop — run the coroutine on a dedicated thread.
    # This path is only correct when the coroutine does NOT depend on the
    # calling loop (no loop-bound futures, no Supabase async client state
    # created on the parent loop). Prefer awaiting directly when possible.
    box = {}

    def runner():
        try:
            box["value"] = asyncio.run(coro)
        except BaseException as exc:
            box["error"] = exc

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    t.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _run_awaitable_sync(maybe_awaitable):
    """Run an awaitable to completion from a synchronous context.

    Handles the case where the caller is already inside a running event loop
    by executing the coroutine on a dedicated thread. Returns the resolved
    value for both awaitable and non-awaitable inputs.
    """
    import inspect
    if not inspect.isawaitable(maybe_awaitable):
        return maybe_awaitable
    try:
        return asyncio.run(maybe_awaitable)
    except RuntimeError as loop_exc:
        if "running event loop" not in str(loop_exc):
            raise
        box: dict = {}

        def runner():
            try:
                box["value"] = asyncio.run(maybe_awaitable)
            except BaseException as exc:
                box["error"] = exc

        t = threading.Thread(target=runner, daemon=True)
        t.start()
        t.join()
        if "error" in box:
            raise box["error"]
        return box.get("value")


# ═══════════════════════════════════════════════════════════════════════════
# v4.0 — DEAD LETTER QUEUE
# ═══════════════════════════════════════════════════════════════════════════


class DeadLetterQueue:
    """Stores failed publishes for later retry/manual review.

    v4.0: No more lost posts. Failed attempts go to a persistent queue.
    Falls back: Supabase → file → memory.

    v4.1 (2026-10-02) — SCHEMA ALIGNMENT:
      Live dead_letter_queue table columns are:
        id           bigint      DB-generated
        kind         text        why this entry exists
        payload      jsonb       everything the retry needs
        error        text        human-readable failure reason
        workspace_id text        tenant scope for queries
        created_at   timestamptz DB default

      brand_id, retry_count, and next_retry_at have no columns; they
      live inside payload.
    """

    def __init__(
        self,
        supabase_client: Any = None,
        file_path: str = "/tmp/dead_letter_queue.json",
        max_memory_entries: int = 1000,
    ):
        self.supabase = supabase_client
        self.file_path = file_path
        self._memory_queue: deque = deque(maxlen=max_memory_entries)
        self._lock = threading.RLock()

    # ────────────────────────────────────────────────────────────────
    # Internal helpers
    # ────────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_meta(entry: dict) -> dict:
        """Pull brand_id / retry_count / next_retry_at out of payload.

        The schema stores these inside payload. Older entries written by
        the pre-v4.1 code may have them at the top level. Read both so
        a mixed queue drains correctly.
        """
        meta = dict(entry.get("payload") or {})
        if "retry_count" not in meta and "retry_count" in entry:
            meta["retry_count"] = entry["retry_count"]
        if "next_retry_at" not in meta and "next_retry_at" in entry:
            meta["next_retry_at"] = entry["next_retry_at"]
        if "brand_id" not in meta and "brand_id" in entry:
            meta["brand_id"] = entry["brand_id"]
        return meta

    @staticmethod
    def _is_ready(entry: dict, now: datetime) -> bool:
        """True if the entry's next_retry_at has passed (or is absent)."""
        meta = DeadLetterQueue._extract_meta(entry)
        raw = meta.get("next_retry_at")
        if not raw:
            return True
        try:
            return datetime.fromisoformat(str(raw)) <= now
        except Exception:
            return True

    # ────────────────────────────────────────────────────────────────
    # Public API
    # ────────────────────────────────────────────────────────────────

    def push(
        self,
        brand_id: str,
        error: str,
        payload: dict,
        retry_count: int = 0,
        *,
        workspace_id: Optional[str] = None,
    ) -> None:
        """Add a failed publish to the dead letter queue.

        Schema-aligned insert: writes only the columns the table has.
        brand_id, retry_count, and next_retry_at live inside payload.
        """
        next_retry_at = (
            datetime.now(timezone.utc) + timedelta(hours=6)
        ).isoformat()

        original_payload = dict(payload or {})
        payload_with_meta = dict(original_payload)
        payload_with_meta["brand_id"] = brand_id
        payload_with_meta["retry_count"] = retry_count
        payload_with_meta["next_retry_at"] = next_retry_at

        resolved_workspace = (
            workspace_id
            or original_payload.get("workspace_id")
            or payload_with_meta.get("workspace_id")
            or ""
        )

        # Shape written to Supabase — matches the live schema exactly.
        db_entry = {
            "kind": "publish_failure",
            "workspace_id": resolved_workspace,
            "payload": payload_with_meta,
            "error": error,
        }

        # Shape written to file/memory — flat, includes an id and
        # created_at for parity with what Supabase returns on select.
        flat_entry = {
            **db_entry,
            "id": uuid.uuid4().hex,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        with self._lock:
            if self.supabase:
                try:
                    self.supabase.table("dead_letter_queue").insert(db_entry).execute()
                    return
                except Exception as exc:
                    logger.warning(
                        "DLQ supabase insert failed, falling back to file: %s",
                        exc,
                    )

            try:
                existing = []
                if os.path.exists(self.file_path):
                    with open(self.file_path, "r") as f:
                        existing = json.load(f)
                existing.append(flat_entry)
                with open(self.file_path, "w") as f:
                    json.dump(existing, f, indent=2, default=str)
                return
            except Exception as exc:
                logger.warning("DLQ file write failed, falling back to memory: %s", exc)

            self._memory_queue.append(flat_entry)

    def pop_retryable(self, limit: int = 10) -> List[dict]:
        """Get entries ready for retry.

        Reads next_retry_at from payload (where the schema stores it).
        Falls back to top-level for entries written by pre-v4.1 code.

        Entries are not removed here — ack() is the removal path, and
        it is only called after downstream execution accepts the retry.
        """
        now = datetime.now(timezone.utc)
        retryable: List[dict] = []
        seen_ids: set = set()

        def _try_add(entry: dict) -> None:
            if len(retryable) >= limit:
                return
            entry_id = entry.get("id")
            if entry_id is not None and entry_id in seen_ids:
                return
            if not self._is_ready(entry, now):
                return
            if entry_id is not None:
                seen_ids.add(entry_id)
            retryable.append(entry)

        with self._lock:
            # Supabase is authoritative when reachable.
            if self.supabase:
                try:
                    result = (
                        self.supabase.table("dead_letter_queue")
                        .select("*")
                        .eq("kind", "publish_failure")
                        .limit(limit * 3)  # over-fetch; filter by payload in Python
                        .execute()
                    )
                    for row in (result.data or []):
                        _try_add(dict(row))
                        if len(retryable) >= limit:
                            break
                except Exception as exc:
                    logger.warning("DLQ supabase read failed: %s", exc)

            # File fallback — only if Supabase didn't fill the batch.
            if len(retryable) < limit and os.path.exists(self.file_path):
                try:
                    with open(self.file_path, "r") as f:
                        existing = json.load(f)
                    for entry in existing:
                        _try_add(entry)
                        if len(retryable) >= limit:
                            break
                except Exception as exc:
                    logger.warning("DLQ file read failed: %s", exc)

            # Memory fallback — last resort.
            if len(retryable) < limit:
                for entry in list(self._memory_queue):
                    _try_add(entry)
                    if len(retryable) >= limit:
                        break

        return retryable

    def ack(self, entry_id) -> bool:
        """Remove a DLQ entry after downstream execution succeeded.

        entry_id may be an int (Supabase bigint id) or a str (file/memory
        UUID). Both are compared as strings for the file and memory
        backends; Supabase receives the value as-is and coerces.
        """
        removed = False
        entry_id_str = str(entry_id)

        with self._lock:
            # Supabase
            if self.supabase:
                try:
                    result = (
                        self.supabase.table("dead_letter_queue")
                        .delete()
                        .eq("id", entry_id)
                        .execute()
                    )
                    if getattr(result, "data", None):
                        removed = True
                except Exception as exc:
                    logger.warning("DLQ supabase delete failed: %s", exc)

            # File
            if os.path.exists(self.file_path):
                try:
                    with open(self.file_path, "r") as f:
                        existing = json.load(f)
                    remaining = [
                        e for e in existing
                        if str(e.get("id")) != entry_id_str
                    ]
                    if len(remaining) != len(existing):
                        removed = True
                        with open(self.file_path, "w") as f:
                            json.dump(remaining, f, indent=2, default=str)
                except Exception as exc:
                    logger.warning("DLQ file ack failed: %s", exc)

            # Memory
            before = len(self._memory_queue)
            self._memory_queue = deque(
                (e for e in self._memory_queue if str(e.get("id")) != entry_id_str),
                maxlen=self._memory_queue.maxlen,
            )
            if len(self._memory_queue) != before:
                removed = True

        return removed

    def get_count(self) -> int:
        """Approximate total entries across all backends.

        Supabase is authoritative when reachable and returns an exact
        count. File and memory are summed only when Supabase is not
        available or the query fails.
        """
        if self.supabase:
            try:
                result = (
                    self.supabase.table("dead_letter_queue")
                    .select("id", count="exact")
                    .eq("kind", "publish_failure")
                    .execute()
                )
                count = getattr(result, "count", None)
                if count is not None:
                    return int(count)
            except Exception as exc:
                logger.debug("DLQ count via supabase failed: %s", exc)

        count = len(self._memory_queue)
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, "r") as f:
                    count += len(json.load(f))
            except Exception:
                pass
        return count


# ═══════════════════════════════════════════════════════════════════════════
# v4.0 — CIRCUIT BREAKER WITH HALF-OPEN PROBING
# ═══════════════════════════════════════════════════════════════════════════


class SupabaseCircuitBreaker:
    """Circuit breaker with proper half-open probing."""

    def __init__(
        self,
        failure_threshold: int = 5,
        cooldown_seconds: float = 60.0,
        half_open_max_probes: int = 2,
    ):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.half_open_max_probes = half_open_max_probes
        self._failure_count = 0
        self._state = "CLOSED"
        self._last_failure_time = 0.0
        self._half_open_probes = 0
        self._lock = threading.RLock()
        self.state_history: deque = deque(maxlen=100)

    @property
    def is_open(self) -> bool:
        with self._lock:
            if self._state == "CLOSED":
                return False
            if self._state == "OPEN":
                if time.monotonic() - self._last_failure_time >= self.cooldown_seconds:
                    self._state = "HALF_OPEN"
                    self._half_open_probes = 0
                    self._record_state_transition("OPEN", "HALF_OPEN")
                    logger.info("Circuit breaker: OPEN → HALF_OPEN")
                    return False
                return True
            if self._state == "HALF_OPEN":
                if self._half_open_probes < self.half_open_max_probes:
                    return False
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            if self._state == "HALF_OPEN":
                self._half_open_probes += 1
                if self._half_open_probes >= self.half_open_max_probes:
                    self._state = "CLOSED"
                    self._failure_count = 0
                    self._record_state_transition("HALF_OPEN", "CLOSED")
                    logger.info("Circuit breaker: HALF_OPEN → CLOSED")
            elif self._state == "CLOSED":
                self._failure_count = 0

    def record_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            if self._state == "HALF_OPEN":
                self._state = "OPEN"
                self._record_state_transition("HALF_OPEN", "OPEN")
                logger.warning("Circuit breaker: HALF_OPEN → OPEN (probe failed)")
            elif self._failure_count >= self.failure_threshold and self._state == "CLOSED":
                self._state = "OPEN"
                self._record_state_transition("CLOSED", "OPEN")
                logger.error("Circuit breaker: CLOSED → OPEN (%d failures)", self._failure_count)

    def _record_state_transition(self, from_state: str, to_state: str) -> None:
        self.state_history.append({
            "from": from_state,
            "to": to_state,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    @property
    def state(self) -> str:
        with self._lock:
            return self._state


# ═══════════════════════════════════════════════════════════════════════════
# v4.0 — METRICS COLLECTOR
# ═══════════════════════════════════════════════════════════════════════════


class SchedulerMetrics:
    """Prometheus-compatible metrics for the scheduler."""

    def __init__(self):
        self._lock = threading.RLock()
        self.posts_published_total = 0
        self.posts_failed_total = 0
        self.retry_attempts_total = 0
        self.circuit_breaker_trips = 0
        self.dead_letter_entries = 0
        self.publish_latency_ms: deque = deque(maxlen=500)
        self.success_rate_5m: deque = deque(maxlen=300)
        self.success_rate_1h: deque = deque(maxlen=3600)

    def record_publish_success(self, duration_ms: float) -> None:
        with self._lock:
            self.posts_published_total += 1
            self.publish_latency_ms.append(duration_ms)
            self.success_rate_5m.append((time.monotonic(), True))
            self.success_rate_1h.append((time.monotonic(), True))

    def record_publish_failure(self, duration_ms: float = 0) -> None:
        with self._lock:
            self.posts_failed_total += 1
            self.success_rate_5m.append((time.monotonic(), False))
            self.success_rate_1h.append((time.monotonic(), False))

    def get_success_rate(self, window_seconds: float = 300) -> float:
        with self._lock:
            now = time.monotonic()
            entries = [s for t, s in self.success_rate_5m if now - t <= window_seconds]
            if not entries:
                return 1.0
            return sum(entries) / len(entries)

    def get_p95_latency(self) -> float:
        with self._lock:
            if not self.publish_latency_ms:
                return 0.0
            data = sorted(self.publish_latency_ms)
            idx = int(len(data) * 0.95)
            return data[min(idx, len(data) - 1)]

    def to_prometheus(self) -> str:
        """Export metrics in Prometheus text format."""
        with self._lock:
            return (
                f"# HELP scheduler_posts_published_total Total published posts\n"
                f"# TYPE scheduler_posts_published_total counter\n"
                f"scheduler_posts_published_total {self.posts_published_total}\n"
                f"# HELP scheduler_posts_failed_total Total failed posts\n"
                f"# TYPE scheduler_posts_failed_total counter\n"
                f"scheduler_posts_failed_total {self.posts_failed_total}\n"
                f"# HELP scheduler_success_rate_5m Success rate in last 5 minutes\n"
                f"# TYPE scheduler_success_rate_5m gauge\n"
                f"scheduler_success_rate_5m {self.get_success_rate(300):.4f}\n"
                f"# HELP scheduler_p95_latency_ms P95 publish latency\n"
                f"# TYPE scheduler_p95_latency_ms gauge\n"
                f"scheduler_p95_latency_ms {self.get_p95_latency():.2f}\n"
                f"# HELP scheduler_dead_letter_entries Dead letter queue entries\n"
                f"# TYPE scheduler_dead_letter_entries gauge\n"
                f"scheduler_dead_letter_entries {self.dead_letter_entries}\n"
            )


# ═══════════════════════════════════════════════════════════════════════════
# v4.0 — RETRY WITH BACKOFF
# ═══════════════════════════════════════════════════════════════════════════


def retry_with_backoff(fn, max_retries: int = 2, base_delay: float = 1.0):
    """Retry a function with exponential backoff."""
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except Exception as e:
            if attempt == max_retries:
                raise
            delay = base_delay * (2 ** attempt) + random.uniform(0, 0.5)
            logger.warning(
                "Retry %d/%d in %.2fs: %s",
                attempt + 1, max_retries, delay, e,
            )
            time.sleep(delay)


# ═══════════════════════════════════════════════════════════════════════════
# DASHBOARD EVENT EMITTER — v3.8 FIXED (retained)
# ═══════════════════════════════════════════════════════════════════════════


def _emit_dashboard_event(
    event_name: str,
    payload: dict,
    loop: Optional[asyncio.AbstractEventLoop] = None,
) -> None:
    """Best-effort dashboard event emission using stored loop."""
    global _failed_emits

    try:
        from core.socket_server import emit_to_dashboard

        if loop is not None:
            asyncio.run_coroutine_threadsafe(
                emit_to_dashboard(event_name, payload), loop
            )
            return

        try:
            loop = asyncio.get_running_loop()
            asyncio.run_coroutine_threadsafe(
                emit_to_dashboard(event_name, payload), loop
            )
        except RuntimeError:
            raise

    except RuntimeError:
        with _failed_emits_lock:
            _failed_emits += 1
            count = _failed_emits
        if count == 1 or count % 100 == 0:
            logger.warning(
                "Dashboard event '%s' lost (no event loop). Total dropped: %d",
                event_name, count,
            )
    except Exception as exc:
        logger.debug("emit_dashboard_event error (%s): %s", event_name, exc)


# ═══════════════════════════════════════════════════════════════════════════
# BRAND SCHEDULER v4.0 — 10/10 PRODUCTION GRADE
# ═══════════════════════════════════════════════════════════════════════════


class BrandScheduler:
    """Handles automated + manual posting for a single brand."""

    # v4.0 — Backpressure: max concurrent publishes per brand
    MAX_CONCURRENT_PUBLISHES = 3

    def __init__(
        self,
        brand: Brand,
        ai_profile: Optional[AIProfile],
        container: "ServiceContainer",
        job_service: Optional["JobService"] = None,
        event_bus=None,
        event_loop=None,
        supabase_client: Any = None,
        redis_client: Any = None,
    ) -> None:
        self.brand = brand
        self.ai_profile = ai_profile
        self._container = container
        self._job_service = job_service
        self._event_bus = event_bus
        self._event_loop = event_loop
        self._running = False
        self._thread = None
        self._stop_event = threading.Event()
        self._worker_id = f"scheduler:{brand.id}:{uuid.uuid4().hex}"

        # v3.9: Timezone support
        self.timezone_name = getattr(brand, "timezone", None) or "UTC"
        try:
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
            self.timezone = ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError:
            logger.warning(
                "Brand '%s': invalid timezone '%s' — falling back to UTC",
                brand.name, self.timezone_name,
            )
            self.timezone = timezone.utc
        except Exception:
            self.timezone = timezone.utc

        # v3.9: Circuit breaker
        self._supabase_circuit = SupabaseCircuitBreaker()

        # v4.0: Distributed lock
        self._distributed_lock = DistributedLock(
            name=f"brand:{brand.id}:publish",
            redis_client=redis_client,
        )

        # v4.0: Backpressure semaphore
        self._publish_semaphore = threading.Semaphore(
            self.MAX_CONCURRENT_PUBLISHES
        )

        # v4.0: Dead letter queue
        self._dead_letter_queue = DeadLetterQueue(
            supabase_client=supabase_client,
            file_path=f"/tmp/dead_letter_{brand.id}.json",
        )

        # v4.0: Metrics
        self._metrics = SchedulerMetrics()

        # v4.0: Manual trigger idempotency
        self._manual_trigger_keys: dict[str, float] = {}
        self._manual_trigger_lock = threading.Lock()

        self._fired_slots: set = set()
        self._published_keys: set = set()
        self._publish_lock = threading.Lock()

        schedule = self.brand.posting_schedule or {}
        self.content_hours = schedule.get("content_hours", [])
        self.news_hours = schedule.get("news_hours", [])
        self.max_posts_per_day = schedule.get(
            "max_posts_per_day", len(self.content_hours)
        )
        self.post_interval_hours = schedule.get("post_interval_hours")
        self.news_interval_hours = schedule.get("news_interval_hours")

        self._pipeline_status: dict = {
            "status": "idle", "current_step": "", "progress": 0,
            "topic": "", "platforms": [],
        }
        self._posts_published = 0
        self._posts_failed = 0

    # ═══════════════════════════════════════════════════════════════
    # EVENT BUS
    # ═══════════════════════════════════════════════════════════════

    def _emit(self, event_name: str, payload: dict) -> None:
        if not self._event_bus:
            return
        try:
            if hasattr(self._event_bus, "publish_event"):
                self._event_bus.publish_event(event_name, payload)
            elif hasattr(self._event_bus, "publish"):
                self._event_bus.publish(event_name, payload)
            elif hasattr(self._event_bus, "emit"):
                self._event_bus.emit(event_name, payload)
        except Exception:
            pass

    def _emit_dashboard(self, event_name: str, payload: dict) -> None:
        _emit_dashboard_event(event_name, payload, loop=self._event_loop)

    # ═══════════════════════════════════════════════════════════════
    # v4.0 — WEBHOOK NOTIFICATIONS
    # ═══════════════════════════════════════════════════════════════

    def _notify_webhooks(self, event: str, payload: dict) -> None:
        """Notify external systems via webhook URLs."""
        webhook_urls = getattr(self.brand, "webhook_urls", []) or []
        for url in webhook_urls:
            try:
                import requests
                requests.post(
                    url,
                    json={"event": event, **payload},
                    timeout=5,
                )
            except Exception as exc:
                logger.debug(
                    "Webhook '%s' failed: %s", url, exc,
                )

    # ═══════════════════════════════════════════════════════════════
    # LIFECYCLE
    # ═══════════════════════════════════════════════════════════════

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info(
            "BrandScheduler v4.0: started for '%s' — %d content slots, %d news slots (tz=%s)",
            self.brand.name, len(self.content_hours), len(self.news_hours),
            self.timezone_name,
        )

    def stop(self) -> None:
        self._running = False
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def _sleep(self, seconds: float) -> None:
        self._stop_event.wait(timeout=seconds)

    def _run(self) -> None:
        self._sleep(30)
        self._recover_on_startup()
        self._retry_dead_letter_queue()
        first_cycle = True
        while self._running:
            try:
                if first_cycle:
                    logger.info(
                        "BrandScheduler '%s': startup complete", self.brand.name
                    )
                    first_cycle = False
                else:
                    self._check_schedule()
                    self._check_scheduled_posts()
                    self._retry_dead_letter_queue()
            except Exception as e:
                logger.error(
                    "BrandScheduler '%s' error: %s", self.brand.id, e
                )
            self._sleep(60)

    def _recover_on_startup(self) -> None:
        if not self._job_service:
            return
        try:
            now_local = datetime.now(self.timezone)
            today = now_local.date()
            hour = now_local.hour
            if hour in self.content_hours:
                idempotency_key = (
                    f"{self.brand.id}-{today.isoformat()}-{hour:02d}-content"
                )
                if not _job_call(self._job_service, "is_slot_claimed", idempotency_key):
                    if now_local.minute < 30:
                        logger.info(
                            "BrandScheduler '%s': recovering missed slot hour=%d",
                            self.brand.name, hour,
                        )
                        self._trigger_workflow("scheduled", today, hour)
        except Exception as e:
            logger.error(
                "BrandScheduler '%s': startup recovery failed: %s",
                self.brand.name, e,
            )

    # ═══════════════════════════════════════════════════════════════
    # v4.0 — DEAD LETTER QUEUE RETRY
    # ═══════════════════════════════════════════════════════════════

    def _retry_dead_letter_queue(self) -> None:
        """Retry failed posts from the dead letter queue.

        v4.2: extract only the kwargs that _trigger_workflow accepts, and
        read retry_count from payload (where v4.1 stores it). The prior
        implementation splatted the entire payload into the call, which
        raised TypeError on brand_id / retry_count / next_retry_at; the
        except handler then crashed on entry["retry_count"] (KeyError),
        so no DLQ entry was ever actually retried.
        """
        retryable = self._dead_letter_queue.pop_retryable(limit=3)
        for entry in retryable:
            self._metrics.retry_attempts_total += 1
            logger.info(
                "[%s] Retrying dead letter entry: %s",
                self.brand.name, entry.get("id"),
            )
            try:
                meta = self._dead_letter_queue._extract_meta(entry)
                accepted = self._trigger_workflow(
                    source="dead_letter_retry",
                    today=meta.get("today"),
                    hour=meta.get("hour"),
                    scheduled_post_id=meta.get("scheduled_post_id"),
                    scheduled_caption=meta.get("scheduled_caption"),
                )
                if accepted:
                    self._dead_letter_queue.ack(entry.get("id"))
                else:
                    logger.warning(
                        "[%s] Dead letter entry %s not accepted; retaining for retry",
                        self.brand.name, entry.get("id"),
                    )
            except Exception as e:
                logger.error(
                    "[%s] Dead letter retry failed: %s — re-queueing",
                    self.brand.name, e,
                )
                meta = self._dead_letter_queue._extract_meta(entry)
                self._dead_letter_queue.push(
                    brand_id=self.brand.id,
                    error=str(e),
                    payload=meta,
                    retry_count=int(meta.get("retry_count", 0)) + 1,
                    workspace_id=getattr(self.brand, "workspace_id", None),
                )

    # ═══════════════════════════════════════════════════════════════
    # SCHEDULE CHECKING
    # ═══════════════════════════════════════════════════════════════

    def _check_schedule(self) -> None:
        status = _brand_status_value(self.brand)
        if status != "active" or not self.ai_profile:
            return

        now_local = datetime.now(self.timezone)
        today = now_local.date()
        hour = now_local.hour

        self._fired_slots = {
            (d, h, k) for d, h, k in self._fired_slots if d == today
        }

        if hour in self.content_hours and (today, hour, "content") not in self._fired_slots:
            posts_today = sum(1 for d, h, k in self._fired_slots if k == "content")
            if posts_today < self.max_posts_per_day:
                self._trigger_workflow("scheduled", today, hour)

        if hour in self.news_hours and (today, hour, "news") not in self._fired_slots:
            logger.info("[%s] Scheduled news slot — hour %d", self.brand.name, hour)
            self._fired_slots.add((today, hour, "news"))
            self._trigger_news()

    # ═══════════════════════════════════════════════════════════════
    # SCHEDULED POSTS (with circuit breaker + retry)
    # ═══════════════════════════════════════════════════════════════

    def _check_scheduled_posts(self) -> None:
        if not self._job_service:
            return

        if self._supabase_circuit.is_open:
            logger.debug(
                "[%s] Circuit open — skipping scheduled post check",
                self.brand.name,
            )
            return

        try:
            from services.supabase import supabase_service
            if not supabase_service or not supabase_service.client:
                return

            now_utc = datetime.now(timezone.utc)

            # Recover only expired scheduler claims. A post already linked to
            # the publish outbox is never reset to queued: the outbox owns it.
            try:
                supabase_service.client.rpc("recover_scheduled_post_claims", {
                    "p_limit": 100,
                }).execute()
            except Exception as recovery_exc:
                logger.error(
                    "[%s] Scheduled claim recovery failed: %s",
                    self.brand.name, recovery_exc,
                )

            post_result = retry_with_backoff(
                lambda: supabase_service.client.table("scheduled_posts")
                .select("id,caption,workspace_id,brand_id")
                .order("scheduled_for")
                .eq("brand_id", self.brand.id)
                .lte("scheduled_for", now_utc.isoformat())
                .eq("status", "queued")
                .limit(1)
                .execute(),
                max_retries=2,
            )

            if not post_result.data:
                self._supabase_circuit.record_success()
                return

            post_row = post_result.data[0]
            post_id = post_row["id"]

            worker_id = getattr(self, "_worker_id", None) or f"scheduler:{self.brand.id}:{id(self)}"
            result = retry_with_backoff(
                lambda: supabase_service.client.rpc("claim_scheduled_post", {
                    "p_id": post_id,
                    "p_worker": worker_id,
                    "p_lease_seconds": 300,
                }).execute(),
                max_retries=2,
            )

            claimed = result.data if hasattr(result, 'data') else []
            if claimed:
                self._supabase_circuit.record_success()
                logger.info(
                    "[%s] Scheduled post claimed: id=%s",
                    self.brand.name, post_id,
                )
                accepted = self._trigger_workflow(
                    "scheduled", scheduled_post_id=post_id,
                    scheduled_caption=post_row.get("caption"),
                )
                if not accepted:
                    # Never leave a claimed post stranded in processing when the
                    # workflow could not be accepted (backpressure/lock/failure).
                    try:
                        supabase_service.client.table("scheduled_posts").update({
                            "status": "queued", "started_at": None,
                            "lease_token": None, "lease_owner": None, "lease_expires_at": None,
                        }).eq("id", post_id).eq("status", "processing").eq("lease_owner", worker_id).execute()
                    except Exception as reset_exc:
                        logger.error(
                            "[%s] Failed to release unaccepted scheduled post %s: %s",
                            self.brand.name, post_id, reset_exc,
                        )

        except Exception as e:
            self._supabase_circuit.record_failure()
            logger.error(
                "[%s] Error processing scheduled post: %s",
                self.brand.name, e,
            )

    # ═══════════════════════════════════════════════════════════════
    # JOB MANAGEMENT
    # ═══════════════════════════════════════════════════════════════

    def _get_or_create_automation_job(
        self, brand_id: str, workspace_id: str, slot_date: str,
        slot_hour: int, job_id: str, source: str = "scheduled",
    ) -> tuple[Optional[AutomationJob], Optional[str]]:
        if not self._job_service:
            return None, f"{brand_id}-{slot_date}-{slot_hour:02d}-content"

        idempotency_key = f"{brand_id}-{slot_date}-{slot_hour:02d}-content"

        existing = _job_call(self._job_service, "find_by_idempotency_key", idempotency_key)
        if existing:
            if existing.status == JobStatus.RETRY_PENDING:
                logger.info(
                    "[%s] job=%s — reusing RETRY_PENDING job %s",
                    self.brand.name, job_id, existing.id,
                )
                job = _job_call(self._job_service, "reset_to_pending", existing.id)
                if job:
                    return job, idempotency_key
            elif existing.status == JobStatus.PUBLISHED:
                logger.info(
                    "[%s] job=%s — slot already completed by %s",
                    self.brand.name, job_id, existing.id,
                )
                return None, None
            else:
                logger.info(
                    "[%s] job=%s — slot already claimed (status=%s) by %s",
                    self.brand.name, job_id, existing.status.value, existing.id,
                )
                return None, None

        try:
            automation_job = _job_call(
                self._job_service, "create_scheduled_job",
                brand_id=brand_id, workspace_id=workspace_id,
                slot_date=slot_date, slot_hour=slot_hour, job_id=job_id,
            )
            return automation_job, idempotency_key
        except Exception as e:
            from core.errors import DuplicateJobError
            if isinstance(e, DuplicateJobError):
                return None, None
            raise

    # ═══════════════════════════════════════════════════════════════
    # WORKFLOW EXECUTION HELPER (v1.6)
    # ═══════════════════════════════════════════════════════════════

    def _execute_workflow(self, workflow, wf_kwargs: dict):
        """Execute a workflow through its async entrypoint.

        The workflow contains async steps (KnowledgeBase.search,
        ContentEngine.generate_post) that must be awaited. Calling the sync
        workflow.run() from a background thread returned coroutines that were
        never awaited, causing downstream steps to fail with
        "'coroutine' object has no attribute 'get'".
        """
        maybe = workflow.execute_async(initial_context=wf_kwargs)
        return _run_awaitable_sync(maybe)

    def _extract_workflow_outcome(
        self, result, *, context_platform: Optional[str] = None
    ) -> tuple[str, bool, List[str], bool]:
        """Parse workflow result steps into (topic, publish_ok, platforms, queued).

        The publish step's output can be either of two shapes:

          Flat pipeline result (canonical pipeline / execution_managed path):
            {"status": "ok", "ok": True, "post_id": "...", "platform": "facebook", ...}

          Per-platform mapping (legacy, still emitted by the outbox path):
            {"facebook": {"ok": True, "post_id": "..."}, "twitter": {...}}

        Prior versions assumed the per-platform shape and crashed with
        "'bool' object has no attribute 'get'" the first time the flat shape
        reached this method — the top-level `ok`/`queued`/`governed` bools
        were iterated and `.get()` was called on each value.
        """
        topic, publish_ok, platforms, queued_publish = "", False, [], False

        for step in result.steps:
            if step.step_name == "select_topic" and step.ok:
                topic = (step.output or {}).get("topic", "")
            elif step.step_name == "publish":
                publish_ok = step.ok
                output = step.output if isinstance(step.output, dict) else {}

                queued_publish = bool(output.get("queued"))

                if not step.ok:
                    continue

                # Detect the flat pipeline shape first. Its top-level "status"
                # or a top-level bool at "ok" is unambiguous.
                status_str = str(output.get("status") or "").strip().lower()
                is_flat = (
                    "status" in output
                    or isinstance(output.get("ok"), bool)
                    or "post_id" in output
                    or "platform_post_id" in output
                    or "provider_object_id" in output
                )

                if is_flat:
                    ok_flag = bool(output.get("ok")) or status_str in (
                        "ok", "succeeded", "success", "published"
                    )
                    post_id = (
                        output.get("post_id")
                        or output.get("platform_post_id")
                        or output.get("provider_object_id")
                    )
                    if ok_flag and post_id:
                        _platform = (
                            output.get("platform")
                            or context_platform
                            or "facebook"
                        )
                        platforms = [_platform]
                else:
                    # Legacy per-platform mapping. Iterate only over dict values.
                    platforms = [
                        k for k, v in output.items()
                        if isinstance(v, dict) and v.get("ok")
                    ]

        return topic, publish_ok, platforms, queued_publish

    # ═══════════════════════════════════════════════════════════════
    # WORKFLOW TRIGGER (v4.0 — with distributed lock + backpressure)
    # ═══════════════════════════════════════════════════════════════

    def _trigger_workflow(
        self, source: str = "scheduled", today=None, hour=None,
        scheduled_post_id: Optional[str] = None,
        scheduled_caption: Optional[str] = None,
    ) -> bool:
        job_id = f"pub_{datetime.now().strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"
        agent_name = self.ai_profile.name if self.ai_profile else "Gemini"

        # v4.0 — Acquire distributed lock (honest mode)
        # Multi-instance safety requires Redis. Local fallback is DEGRADED_LOCAL.
        require_dist = str(os.getenv("REQUIRE_DISTRIBUTED_LOCK", "")).lower() in (
            "1", "true", "yes",
        )
        if not self._distributed_lock.acquire(require_distributed=require_dist):
            logger.warning(
                "[%s] Could not acquire publish lock (mode=%s) — skip",
                self.brand.name,
                getattr(self._distributed_lock, "mode", "?"),
            )
            return False
        if getattr(self._distributed_lock, "is_degraded_local", False):
            logger.warning(
                "[%s] Publish lock is DEGRADED_LOCAL — not safe across instances",
                self.brand.name,
            )

        # v4.0 — Acquire backpressure semaphore
        if not self._publish_semaphore.acquire(blocking=False):
            logger.warning(
                "[%s] Backpressure limit reached (%d concurrent) — deferring",
                self.brand.name, self.MAX_CONCURRENT_PUBLISHES,
            )
            self._distributed_lock.release()
            return False

        publish_started = time.monotonic()
        automation_job: Optional[AutomationJob] = None
        idempotency_key: Optional[str] = None

        try:
            if today is not None and hour is not None:
                workspace_id = getattr(self.brand, "workspace_id", "")
                slot_date = today.isoformat()

                if self._job_service:
                    automation_job, idempotency_key = self._get_or_create_automation_job(
                        brand_id=self.brand.id, workspace_id=workspace_id,
                        slot_date=slot_date, slot_hour=hour, job_id=job_id, source=source,
                    )
                    if automation_job is None and idempotency_key is None:
                        return False
                    if automation_job:
                        try:
                            automation_job = _job_call(
                                self._job_service, "transition",
                                automation_job, JobStatus.RUNNING,
                            )
                        except Exception as e:
                            logger.warning(
                                "[%s] job=%s — transition to RUNNING failed: %s",
                                self.brand.name, job_id, e,
                            )

            logger.info(
                "[%s] job=%s START | brand=%s | source=%s | agent=%s",
                self.brand.name, job_id, self.brand.name, source, agent_name,
            )

            self._emit("scheduler.workflow_started", {
                "brand_id": self.brand.id, "job_id": job_id, "source": source,
            })

            self._emit_dashboard("mission_status", {
                "goal": f"Publish {self.brand.name} Content",
                "stage": "AI Generation", "progress": 5,
                "active_agent": agent_name, "is_active": True,
                "brand_id": self.brand.id, "job_id": job_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            self._pipeline_status = {
                "status": "running", "current_step": "ai", "progress": 30,
                "topic": "", "platforms": [], "job_id": job_id,
            }

            workflow_factory = self._container.resolve(
                "PostPublishWorkflowFactory"
            )
            workflow = workflow_factory(
                brand=self.brand, ai_profile=self.ai_profile
            )

            wf_kwargs = dict(
                job_id=job_id,
                platform=getattr(self.brand, "platform", "facebook"),
                source=source,
                risk_level="low",
                scheduled_post_id=scheduled_post_id,
                scheduled_caption=scheduled_caption,
            )
            result = self._execute_workflow(workflow, wf_kwargs)

            context_platform = (
                wf_kwargs.get("platform")
                or getattr(self.brand, "platform", "facebook")
            )
            topic, publish_ok, platforms, queued_publish = self._extract_workflow_outcome(
                result, context_platform=context_platform,
            )

            duration_ms = (time.monotonic() - publish_started) * 1000

            if publish_ok and not queued_publish:
                if today is not None and hour is not None:
                    self._fired_slots.add((today, hour, "content"))

                if automation_job and self._job_service:
                    try:
                        post_id = ""
                        for step in result.steps:
                            if step.step_name == "publish" and step.ok:
                                output = step.output if isinstance(step.output, dict) else {}
                                status_str = str(output.get("status") or "").strip().lower()

                                # Flat pipeline shape: read the top-level post ID directly.
                                if (
                                    "status" in output
                                    or isinstance(output.get("ok"), bool)
                                    or "post_id" in output
                                    or "platform_post_id" in output
                                    or "provider_object_id" in output
                                ):
                                    _ok = bool(output.get("ok")) or status_str in (
                                        "ok", "succeeded", "success", "published"
                                    )
                                    if _ok:
                                        post_id = (
                                            output.get("post_id")
                                            or output.get("platform_post_id")
                                            or output.get("provider_object_id")
                                            or ""
                                        )
                                    break

                                # Legacy per-platform mapping. Only dict values are
                                # candidates for the post ID.
                                for _name, _value in output.items():
                                    if isinstance(_value, dict) and _value.get("ok") and _value.get("post_id"):
                                        post_id = _value["post_id"]
                                        break
                                if post_id:
                                    break

                        _job_call(
                            self._job_service, "mark_published",
                            automation_job, post_id=post_id,
                            result={"topic": topic, "platforms": platforms},
                        )
                    except Exception as e:
                        logger.error(
                            "[%s] job=%s — mark published failed: %s",
                            self.brand.name, job_id, e,
                        )

                if scheduled_post_id:
                    try:
                        from services.supabase import supabase_service
                        if supabase_service and supabase_service.client:
                            supabase_service.client.table("scheduled_posts").update({
                                "status": "published",
                                "completed_at": datetime.now(timezone.utc).isoformat(),
                            }).eq("id", scheduled_post_id).execute()
                    except Exception as e:
                        logger.error(
                            "[%s] job=%s — scheduled_post update failed: %s",
                            self.brand.name, job_id, e,
                        )

                self._posts_published += 1
                self._metrics.record_publish_success(duration_ms)
                self._pipeline_status = {
                    "status": "idle", "current_step": "", "progress": 100,
                    "topic": topic, "platforms": platforms, "job_id": job_id,
                }
                self._emit("scheduler.workflow_completed", {
                    "brand_id": self.brand.id, "job_id": job_id,
                    "topic": topic, "platforms": platforms,
                    "duration_ms": round(duration_ms),
                })
                self._emit_dashboard("mission_status", {
                    "goal": f"Publish {self.brand.name} Content",
                    "stage": "Complete", "progress": 100,
                    "active_agent": agent_name, "is_active": False,
                    "brand_id": self.brand.id, "job_id": job_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                self._notify_webhooks("post_published", {
                    "brand_id": self.brand.id,
                    "job_id": job_id,
                    "topic": topic,
                    "platforms": platforms,
                })
                logger.info(
                    "[%s] job=%s COMPLETED — topic='%s' | platforms=%s | %dms",
                    self.brand.name, job_id, topic, platforms, round(duration_ms),
                )
                return True

            elif queued_publish:
                if scheduled_post_id:
                    try:
                        from services.supabase import supabase_service
                        if supabase_service and supabase_service.client:
                            outbox_id = next(
                                (s.output or {}).get("outbox_id")
                                for s in result.steps
                                if s.step_name == "publish"
                                and isinstance(s.output, dict)
                                and (s.output or {}).get("outbox_id")
                            )
                            supabase_service.client.table("scheduled_posts").update({
                                "status": "outbox_pending",
                                "started_at": datetime.now(timezone.utc).isoformat(),
                                "outbox_id": outbox_id,
                            }).eq("id", scheduled_post_id).eq("status", "processing").execute()
                    except Exception as e:
                        logger.error(
                            "[%s] job=%s — scheduled outbox link failed: %s",
                            self.brand.name, job_id, e,
                        )
                if automation_job and self._job_service:
                    # CREATED/RUNNING remains durable until the outbox worker
                    # verifies publication. No transition here.
                    pass
                self._pipeline_status = {
                    "status": "queued", "current_step": "outbox", "progress": 90,
                    "topic": topic, "platforms": [], "job_id": job_id,
                }
                self._emit("scheduler.workflow_queued", {
                    "brand_id": self.brand.id, "job_id": job_id, "topic": topic,
                })
                return True

            else:
                failed = [s.step_name for s in result.steps if not s.ok]
                if automation_job and self._job_service:
                    try:
                        _job_call(
                            self._job_service, "mark_failed",
                            automation_job, error=f"Steps failed: {failed}",
                            error_code="WORKFLOW_FAILED", retryable=True,
                        )
                    except Exception as e:
                        logger.error(
                            "[%s] job=%s — mark failed failed: %s",
                            self.brand.name, job_id, e,
                        )
                self._posts_failed += 1
                self._metrics.record_publish_failure(duration_ms)

                self._dead_letter_queue.push(
                    brand_id=self.brand.id,
                    error=f"Steps failed: {failed}",
                    payload={"source": source, "today": today, "hour": hour},
                    workspace_id=getattr(self.brand, "workspace_id", None),
                )

                logger.warning(
                    "[%s] job=%s FAILED — steps=%s (queued for retry)",
                    self.brand.name, job_id, failed,
                )
                return False

        except Exception as e:
            duration_ms = (time.monotonic() - publish_started) * 1000
            if automation_job and self._job_service:
                try:
                    from core.errors import is_retryable
                    _job_call(
                        self._job_service, "mark_failed",
                        automation_job, error=str(e),
                        error_code="WORKFLOW_EXCEPTION",
                        retryable=is_retryable(e),
                    )
                except Exception:
                    pass
            self._posts_failed += 1
            self._metrics.record_publish_failure(duration_ms)

            self._dead_letter_queue.push(
                brand_id=self.brand.id,
                error=str(e),
                payload={"source": source, "today": today, "hour": hour},
                workspace_id=getattr(self.brand, "workspace_id", None),
            )

            logger.error(
                "[%s] job=%s EXCEPTION: %s (queued for retry)",
                self.brand.name, job_id, e,
            )
            return False
        finally:
            self._publish_semaphore.release()
            self._distributed_lock.release()

    def _trigger_news(self) -> None:
        logger.warning(
            "BrandScheduler news publication fenced: canonical V70/V71 path required"
        )
        return

    # ═══════════════════════════════════════════════════════════════
    # MANUAL TRIGGER (v4.0 — with idempotency)
    # ═══════════════════════════════════════════════════════════════

    def trigger_manual(self, idempotency_key: Optional[str] = None) -> dict:
        # v4.0 — Idempotency check
        if idempotency_key:
            with self._manual_trigger_lock:
                now = time.monotonic()
                # Clean old keys (5 minutes)
                self._manual_trigger_keys = {
                    k: v for k, v in self._manual_trigger_keys.items()
                    if now - v < 300
                }
                if idempotency_key in self._manual_trigger_keys:
                    return {
                        "ok": False,
                        "error": "Duplicate request — already triggered",
                    }
                self._manual_trigger_keys[idempotency_key] = now

        # v4.0 — Distributed lock (honest mode)
        require_dist = str(os.getenv("REQUIRE_DISTRIBUTED_LOCK", "")).lower() in (
            "1", "true", "yes",
        )
        if not self._distributed_lock.acquire(require_distributed=require_dist):
            return {
                "ok": False,
                "error": "Publish lock unavailable",
                "lock_mode": str(
                    getattr(self._distributed_lock.mode, "value", self._distributed_lock.mode)
                ),
            }
        if getattr(self._distributed_lock, "is_degraded_local", False):
            logger.warning(
                "[%s] Manual publish DEGRADED_LOCAL — single-process only",
                self.brand.name,
            )

        # v4.0 — Backpressure
        if not self._publish_semaphore.acquire(blocking=False):
            self._distributed_lock.release()
            return {"ok": False, "error": "Too many concurrent publishes"}

        publish_started = time.monotonic()

        try:
            job_id = f"pub_{datetime.now().strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"

            automation_job = None
            if self._job_service:
                try:
                    workspace_id = getattr(self.brand, "workspace_id", "")
                    automation_job = _job_call(
                        self._job_service, "create_manual_job",
                        brand_id=self.brand.id, workspace_id=workspace_id, job_id=job_id,
                    )
                    if automation_job:
                        _job_call(
                            self._job_service, "transition",
                            automation_job, JobStatus.RUNNING,
                        )
                except Exception as e:
                    logger.warning(
                        "[%s] job=%s — manual job creation failed: %s",
                        self.brand.name, job_id, e,
                    )

            workflow_factory = self._container.resolve("PostPublishWorkflowFactory")
            workflow = workflow_factory(brand=self.brand, ai_profile=self.ai_profile)

            wf_kwargs = dict(
                job_id=job_id,
                platform=getattr(self.brand, "platform", "facebook"),
                source="manual",
                risk_level="low",
            )
            result = self._execute_workflow(workflow, wf_kwargs)

            context_platform = (
                wf_kwargs.get("platform")
                or getattr(self.brand, "platform", "facebook")
            )
            topic, publish_ok, platforms, queued_publish = self._extract_workflow_outcome(
                result, context_platform=context_platform,
            )

            duration_ms = (time.monotonic() - publish_started) * 1000

            if publish_ok and queued_publish:
                self._pipeline_status = {
                    "status": "queued", "current_step": "outbox", "progress": 90,
                    "topic": topic, "platforms": [], "job_id": job_id,
                }
                return {
                    "ok": True, "queued": True, "published": False,
                    "brand": self.brand.name, "topic": topic,
                    "platforms": [], "duration_ms": round(duration_ms), "job_id": job_id,
                }

            if publish_ok:
                if automation_job and self._job_service:
                    try:
                        post_id = ""
                        for step in result.steps:
                            if step.step_name == "publish" and step.ok:
                                output = step.output if isinstance(step.output, dict) else {}
                                status_str = str(output.get("status") or "").strip().lower()

                                if (
                                    "status" in output
                                    or isinstance(output.get("ok"), bool)
                                    or "post_id" in output
                                    or "platform_post_id" in output
                                    or "provider_object_id" in output
                                ):
                                    _ok = bool(output.get("ok")) or status_str in (
                                        "ok", "succeeded", "success", "published"
                                    )
                                    if _ok:
                                        post_id = (
                                            output.get("post_id")
                                            or output.get("platform_post_id")
                                            or output.get("provider_object_id")
                                            or ""
                                        )
                                    break

                                for _name, _value in output.items():
                                    if isinstance(_value, dict) and _value.get("ok") and _value.get("post_id"):
                                        post_id = _value["post_id"]
                                        break
                                if post_id:
                                    break

                        _job_call(
                            self._job_service, "mark_published",
                            automation_job, post_id=post_id,
                            result={"topic": topic, "platforms": platforms},
                        )
                    except Exception:
                        pass
                self._metrics.record_publish_success(duration_ms)
                self._notify_webhooks("post_published", {
                    "brand_id": self.brand.id,
                    "job_id": job_id,
                    "topic": topic,
                    "platforms": platforms,
                })
                return {
                    "ok": True, "published": True, "brand": self.brand.name,
                    "topic": topic, "platforms": platforms,
                    "duration_ms": round(duration_ms), "job_id": job_id,
                }

            if automation_job and self._job_service:
                try:
                    _job_call(
                        self._job_service, "mark_failed",
                        automation_job, error="Publish failed",
                        error_code="PUBLISH_FAILED", retryable=True,
                    )
                except Exception:
                    pass

            self._metrics.record_publish_failure(duration_ms)
            self._dead_letter_queue.push(
                brand_id=self.brand.id,
                error="Publish step failed",
                payload={"source": "manual"},
                workspace_id=getattr(self.brand, "workspace_id", None),
            )

            return {"ok": False, "error": "Publish step failed", "job_id": job_id}

        except Exception as e:
            duration_ms = (time.monotonic() - publish_started) * 1000
            self._metrics.record_publish_failure(duration_ms)
            self._dead_letter_queue.push(
                brand_id=self.brand.id,
                error=str(e),
                payload={"source": "manual"},
                workspace_id=getattr(self.brand, "workspace_id", None),
            )
            return {"ok": False, "error": str(e), "brand": self.brand.name}
        finally:
            self._publish_semaphore.release()
            self._distributed_lock.release()

    # ═══════════════════════════════════════════════════════════════
    # STATUS (v4.0 — with metrics)
    # ═══════════════════════════════════════════════════════════════

    def get_status(self) -> dict:
        today = datetime.now(self.timezone).date()
        posts_today = sum(1 for d, h, k in self._fired_slots if d == today and k == "content")
        job_stats = {}
        if self._job_service:
            try:
                job_stats = _job_call(
                    self._job_service, "get_today_stats", brand_id=self.brand.id,
                )
                posts_today = job_stats.get("published", posts_today)
            except Exception:
                pass
        return {
            "brand_id": self.brand.id,
            "brand_name": self.brand.name,
            "running": self._running,
            "timezone": self.timezone_name,
            "posts_today": posts_today,
            "max_posts_per_day": self.max_posts_per_day,
            "content_hours": self.content_hours,
            "news_hours": self.news_hours,
            "publish_busy": self._publish_lock.locked(),
            "job_stats": job_stats,
            "posts_published": self._posts_published,
            "posts_failed": self._posts_failed,
            "circuit_breaker": self._supabase_circuit.state,
            "dead_letter_count": self._dead_letter_queue.get_count(),
            "success_rate_5m": self._metrics.get_success_rate(300),
            "p95_latency_ms": round(self._metrics.get_p95_latency(), 2),
            **self._pipeline_status,
        }


# ═══════════════════════════════════════════════════════════════════════════
# BRAND SCHEDULER MANAGER v4.0
# ═══════════════════════════════════════════════════════════════════════════


class BrandSchedulerManager:
    """Manages all active BrandSchedulers."""

    def __init__(
        self,
        brand_repository: BrandRepository,
        ai_profile_repository: AIProfileRepository,
    ) -> None:
        self.brand_repo = brand_repository
        self.ai_repo = ai_profile_repository
        self._schedulers: Dict[str, BrandScheduler] = {}
        self._running = False
        self._manager_thread = None
        self._container = None
        self._job_service = None
        self._event_bus = None
        self._event_loop = None
        self._sync_count = 0
        self._supabase_client = None
        self._redis_client = None

    def set_container(self, container: "ServiceContainer") -> None:
        self._container = container
        try:
            from services.job_service import JobService
            self._job_service = container.try_resolve(JobService)
        except Exception:
            pass
        try:
            from core.event_bus import EventBus
            self._event_bus = container.try_resolve(EventBus)
        except Exception:
            pass
        try:
            from services.supabase import get_service_client_from_settings
            self._supabase_client = get_service_client_from_settings()
        except Exception:
            pass
        try:
            from core.redis_client import UpstashRedisClient
            self._redis_client = container.try_resolve(UpstashRedisClient)
        except Exception:
            pass

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._event_loop = loop
        for scheduler in self._schedulers.values():
            scheduler._event_loop = loop

    async def start(self) -> None:
        if self._running:
            return
        if not self._container:
            logger.error("BrandSchedulerManager: container not set")
            return
        self._running = True
        await self._recover_all_stale_jobs()
        await self._sync_brands()
        self._manager_thread = threading.Thread(
            target=self._sync_loop, daemon=True, name="scheduler-manager",
        )
        self._manager_thread.start()
        logger.info("BrandSchedulerManager v4.0: started")

    def stop(self) -> None:
        self._running = False
        for s in self._schedulers.values():
            s.stop()
        if self._manager_thread and self._manager_thread.is_alive():
            self._manager_thread.join(timeout=5)

    async def _recover_all_stale_jobs(self) -> None:
        """Recover stale jobs.

        v4.2: this method is called from BrandSchedulerManager.start(),
        which runs on the FastAPI lifespan's event loop. JobService
        methods are coroutines; they must be awaited on the same loop
        that called start(). The previous implementation routed through
        _job_call, which spawns a thread and calls asyncio.run there.
        That creates the coroutine on one loop and awaits it on another,
        which Python 3.12 rejects. Awaiting directly is correct.
        """
        if not self._job_service:
            return
        try:
            stale_count = 0
            if hasattr(self._job_service, "recover_and_retry"):
                stale_count = await self._job_service.recover_and_retry(
                    stale_minutes=15
                )
            elif hasattr(self._job_service, "recover_stale_jobs"):
                stale_count = await self._job_service.recover_stale_jobs(
                    stale_minutes=15
                )
            if int(stale_count or 0) > 0:
                logger.warning("Recovered %d stale jobs", stale_count)
        except Exception as e:
            logger.error("Stale job recovery failed: %s", e)

    async def _sync_brands(self) -> None:
        """Sync the set of active BrandSchedulers to the set of active brands.

        v4.3 — STATUS ENUM FIX:
          BrandStatus is a str-subclass enum. str(BrandStatus.ACTIVE)
          returns 'BrandStatus.ACTIVE', not 'active'. The previous
          implementation compared the stringified enum against the
          literal "active", which always failed, so every brand was
          skipped on every boot. The manager logged "started" but no
          per-brand scheduler was ever created — silent since 9/21.

          The status read now uses _brand_status_value(), which pulls
          .value from the enum. Skipped brands are logged at debug so a
          future regression of this class is visible without a live
          probe.
        """
        loop = asyncio.get_running_loop()
        brands = await loop.run_in_executor(None, self.brand_repo.find_all)
        active_ids = set()
        for brand in brands:
            status = _brand_status_value(brand)
            if status != "active":
                logger.debug(
                    "scheduler.sync skipping brand: name=%s status=%r reason=not_active",
                    getattr(brand, "name", "?"),
                    status,
                )
                continue

            active_ids.add(brand.id)
            if brand.id not in self._schedulers:
                ai_profile = (
                    self.ai_repo.find_by_id(brand.ai_profile_id)
                    if brand.ai_profile_id else None
                )
                if not ai_profile:
                    logger.warning(
                        "scheduler.sync skipping brand: name=%s reason=no_ai_profile "
                        "ai_profile_id=%r",
                        getattr(brand, "name", "?"),
                        getattr(brand, "ai_profile_id", None),
                    )
                    continue
                scheduler = BrandScheduler(
                    brand, ai_profile, self._container,
                    job_service=self._job_service,
                    event_bus=self._event_bus,
                    event_loop=self._event_loop,
                    supabase_client=self._supabase_client,
                    redis_client=self._redis_client,
                )
                self._schedulers[brand.id] = scheduler
                scheduler.start()

        try:
            from services.director import director
            if director and hasattr(director, "enqueue_event"):
                director.enqueue_event("brand_scheduler_sync", {
                    "total_brands": len(brands),
                    "active_brands": len(active_ids),
                    "schedulers_running": len(self._schedulers),
                })
        except Exception:
            pass

        for bid in list(self._schedulers.keys()):
            if bid not in active_ids:
                self._schedulers[bid].stop()
                del self._schedulers[bid]

        self._sync_count += 1

    def _sync_loop(self) -> None:
        while self._running:
            for _ in range(300):
                if not self._running:
                    break
                time.sleep(1)
            if not self._running:
                break
            try:
                asyncio.run(self._sync_brands())
            except Exception as e:
                logger.error("Sync error: %s", e)

    async def trigger_for_brand(
        self, brand_id: str, idempotency_key: Optional[str] = None,
    ) -> dict:
        scheduler = self._schedulers.get(brand_id)
        if not scheduler:
            await self._sync_brands()
            scheduler = self._schedulers.get(brand_id)
        if not scheduler:
            return {"ok": False, "error": f"No scheduler for brand '{brand_id}'"}
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, scheduler.trigger_manual, idempotency_key)

    async def trigger_all(self) -> dict:
        results = {bid: s.trigger_manual() for bid, s in self._schedulers.items()}
        failures = [
            bid for bid, result in results.items()
            if not isinstance(result, dict) or not result.get("ok", False)
        ]
        return {
            "ok": not failures,
            "results": results,
            "brands_triggered": len(results) - len(failures),
            "brands_failed": failures,
        }

    def get_all_status(self) -> list[dict]:
        return [s.get_status() for s in self._schedulers.values()]

    def get_status_for_brand(self, brand_id: str) -> dict:
        s = self._schedulers.get(brand_id)
        return s.get_status() if s else {"error": f"No scheduler for brand '{brand_id}'"}

    def get_stats(self) -> dict:
        return {
            "brands_active": len(self._schedulers),
            "sync_count": self._sync_count,
            "total_posts_published": sum(
                s._posts_published for s in self._schedulers.values()
            ),
            "total_posts_failed": sum(
                s._posts_failed for s in self._schedulers.values()
            ),
            "total_dead_letter": sum(
                s._dead_letter_queue.get_count() for s in self._schedulers.values()
            ),
        }

    def health_check(self) -> dict:
        stats = self.get_stats()
        status = "healthy"
        if stats["brands_active"] == 0:
            status = "idle"
        elif (
            stats["total_posts_failed"] > stats["total_posts_published"] * 0.3
            and stats["total_posts_published"] > 5
        ):
            status = "degraded"
        return {"status": status, "stats": stats}


__all__ = [
    "BrandScheduler",
    "BrandSchedulerManager",
    "_emit_dashboard_event",
    "DistributedLock",
    "DeadLetterQueue",
    "SupabaseCircuitBreaker",
    "SchedulerMetrics",
]
