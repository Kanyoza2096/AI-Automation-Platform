""""
Brand-aware scheduling engine — UNIFIED v4.0
Replaces PostScheduler with a multi-tenant BrandSchedulerManager.

v4.6 (2026-10-24) — SLOT TERMINAL SHORT-CIRCUIT IN DLQ RETRY:
  When a DLQ entry refers to a slot whose automation_job has already
  reached a terminal state (PUBLISHED or FAILED), the retry would run
  the workflow anyway and either re-publish a successful slot (duplicate
  post) or retry a permanently-failed slot (wasted work). The retry path
  now checks the slot's terminal status via _get_or_create_automation_job
  and acks the DLQ entry without executing the workflow. This replaces
  the previous "slot already claimed" branch that returned (None, None)
  and left the DLQ entry in place to retry forever.

v4.5 (2026-10-23) — DATE COERCION FIX IN DLQ RETRY:
  _retry_dead_letter_queue passed the DLQ's serialized `today` string
  directly to _trigger_workflow, which then called `.isoformat()` on it.
  Strings have no .isoformat(), so every DLQ retry failed at the same
  line, was re-pushed with the same stringified date, and re-failed on
  the next cycle — an infinite 60-second retry storm that flooded the
  scheduler log per brand. The fix coerces `today` back to a date and
  `hour` back to an int before the call. Coercion is idempotent: new
  entries (which still round-trip as strings through JSON) are handled
  by the same path.

v4.4 (2026-10-22) — REUSE QUEUED JOBS:
  _get_or_create_automation_job previously returned (None, None) for
  jobs whose status was QUEUED, so the pg_cron retry cascade (which
  moves STALE -> RETRY_PENDING -> QUEUED) silently abandoned the slot
  forever. This release reuses QUEUED jobs by returning them to the
  caller, which then transitions QUEUED -> RUNNING and retries the
  workflow.

v4.3 (2026-10-04) — ENUM STATUS COMPARISON FIX:
  _sync_brands read brand.status with str(...).lower() and compared
  against "active". BrandStatus is a str-subclass enum: str(BrandStatus
  .ACTIVE) returns 'BrandStatus.ACTIVE', not 'active'. The comparison
  therefore always failed and every brand was skipped on every boot.
  Fix: read .value for the primitive, matching _check_schedule's
  existing pattern.

v4.2 (2026-10-03) — ASYNC CONTEXT CORRECTNESS (preserved):
  - _recover_all_stale_jobs now awaits JobService methods directly.
  - _retry_dead_letter_queue extracts only accepted kwargs and reads
    retry_count from payload.
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
    """
    raw = getattr(brand, "status", "")
    if hasattr(raw, "value"):
        return str(raw.value).lower()
    return str(raw or "").lower()


def _brand_publish_platform(brand: Any) -> str:
    """Resolve the configured primary platform, preserving legacy Facebook defaults."""
    explicit = getattr(brand, "platform", None)
    metadata = getattr(brand, "metadata", None)
    configured = None
    if isinstance(metadata, dict):
        configured = metadata.get("primary_platform") or metadata.get("platform")
    value = explicit or configured or "facebook"
    value = getattr(value, "value", value)
    return str(value).strip().lower() or "facebook"

def _coerce_date(value):
    """Return a `date` from a date/datetime/ISO string; None otherwise."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _coerce_int(value):
    """Return an int from an int/str; None for None; raise otherwise."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"Cannot coerce {value!r} to int")


def _job_call(service, method: str, *args, **kwargs):
    """Run JobService async API from legacy synchronous scheduler threads."""
    coro = getattr(service, method)(*args, **kwargs)
    if not asyncio.iscoroutine(coro):
        return coro
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
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
    """Run an awaitable to completion from a synchronous context."""
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

    v4.1 (2026-10-02) — SCHEMA ALIGNMENT:
      Live dead_letter_queue table columns are:
        id, kind, payload, error, workspace_id, created_at
      brand_id, retry_count, and next_retry_at live inside payload.
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

    @staticmethod
    def _extract_meta(entry: dict) -> dict:
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
        meta = DeadLetterQueue._extract_meta(entry)
        raw = meta.get("next_retry_at")
        if not raw:
            return True
        try:
            return datetime.fromisoformat(str(raw)) <= now
        except Exception:
            return True

    def push(
        self,
        brand_id: str,
        error: str,
        payload: dict,
        retry_count: int = 0,
        *,
        workspace_id: Optional[str] = None,
    ) -> None:
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

        db_entry = {
            "kind": "publish_failure",
            "workspace_id": resolved_workspace,
            "payload": payload_with_meta,
            "error": error,
        }

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
            if self.supabase:
                try:
                    result = (
                        self.supabase.table("dead_letter_queue")
                        .select("*")
                        .eq("kind", "publish_failure")
                        .limit(limit * 3)
                        .execute()
                    )
                    for row in (result.data or []):
                        _try_add(dict(row))
                        if len(retryable) >= limit:
                            break
                except Exception as exc:
                    logger.warning("DLQ supabase read failed: %s", exc)

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

            if len(retryable) < limit:
                for entry in list(self._memory_queue):
                    _try_add(entry)
                    if len(retryable) >= limit:
                        break

        return retryable

    def ack(self, entry_id) -> bool:
        removed = False
        entry_id_str = str(entry_id)

        with self._lock:
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

            before = len(self._memory_queue)
            self._memory_queue = deque(
                (e for e in self._memory_queue if str(e.get("id")) != entry_id_str),
                maxlen=self._memory_queue.maxlen,
            )
            if len(self._memory_queue) != before:
                removed = True

        return removed

    def get_count(self) -> int:
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


# Deterministic content/validation failures must not be retried unchanged.
_DETERMINISTIC_WORKFLOW_FAILURE_CODES = {
    "AI_VALIDATION_ERROR",
    "content_validation_failed",
    "editorial_quality_gate_rejected",
}


def _is_ai_provider_outage(failure_evidence: dict) -> bool:
    """Recognize provider availability/quota failures without masking other failures."""
    failed_steps = (failure_evidence or {}).get("failed_steps") or []
    provider_names = ("gemini", "google ai", "cloudflare ai", "openai", "anthropic")
    availability_terms = (
        "http 429", "status 429", "http 503", "status 503",
        "rate limit", "rate-limit", "quota", "resource exhausted",
        "provider unavailable", "service unavailable", "overloaded",
    )
    for step in failed_steps:
        if not isinstance(step, dict):
            continue
        evidence = " ".join(
            str(step.get(key) or "")
            for key in ("step", "step_name", "error_code", "error", "message", "details")
        ).lower()
        has_provider = any(name in evidence for name in provider_names)
        has_availability = any(term in evidence for term in availability_terms)
        is_ai_step = any(term in evidence for term in ("generate", "generation", "llm", "ai_provider"))
        if has_availability and (has_provider or is_ai_step):
            return True
    return False


def _workflow_failure_is_retryable(failure_evidence: dict) -> bool:
    """Keep deterministic validation and provider outages out of retry storms."""
    failed_steps = (failure_evidence or {}).get("failed_steps") or []
    if not failed_steps or _is_ai_provider_outage(failure_evidence):
        return False
    return not any(
        str(step.get("error_code") or "").strip() in _DETERMINISTIC_WORKFLOW_FAILURE_CODES
        for step in failed_steps
        if isinstance(step, dict)
    )


# ═══════════════════════════════════════════════════════════════════════════
# DASHBOARD EVENT EMITTER
# ═══════════════════════════════════════════════════════════════════════════


def _emit_dashboard_event(
    event_name: str,
    payload: dict,
    loop: Optional[asyncio.AbstractEventLoop] = None,
) -> None:
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
# BRAND SCHEDULER v4.0
# ═══════════════════════════════════════════════════════════════════════════


class BrandScheduler:
    """Handles automated + manual posting for a single brand."""

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

        self._supabase_circuit = SupabaseCircuitBreaker()

        # One Redis key shared by every brand scheduler. This is the
        # cross-brand publication mutex: only one publish pipeline may run
        # at a time across all brands and service instances.
        self._distributed_lock = DistributedLock(
            name="global:publish",
            redis_client=redis_client,
            timeout_seconds=1800,
            allow_local_fallback=False,
        )

        self._publish_semaphore = threading.Semaphore(
            self.MAX_CONCURRENT_PUBLISHES
        )

        self._dead_letter_queue = DeadLetterQueue(
            supabase_client=supabase_client,
            file_path=f"/tmp/dead_letter_{brand.id}.json",
        )

        self._metrics = SchedulerMetrics()

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

    def _emit(self, event_name: str, payload: dict) -> None:
        """Publish scheduler events without abandoning async bus coroutines.

        BrandScheduler runs on a worker thread, while UnifiedEventBus is owned
        by the application lifecycle loop. Awaitables must be submitted to that
        loop; asyncio.run() here would create a second loop and violate bus
        ownership.
        """
        if not self._event_bus:
            return

        # Scheduler events are tenant-scoped. Carry trusted identity from the
        # scheduler's hydrated Brand, never a caller-supplied/default workspace.
        workspace_id = str(getattr(self.brand, "workspace_id", "") or "").strip()
        brand_id = str(getattr(self.brand, "id", "") or "").strip()
        if not workspace_id or workspace_id.lower() in {"default", "unbound", "platform"} or not brand_id:
            logger.error(
                "[%s] Refusing scheduler event %s: concrete brand/workspace identity unavailable",
                self.brand.name, event_name,
            )
            return
        scoped_payload = dict(payload or {})
        for key, trusted_value in (("workspace_id", workspace_id), ("brand_id", brand_id)):
            supplied_value = scoped_payload.get(key)
            if supplied_value not in (None, "", trusted_value):
                logger.error(
                    "[%s] Refusing scheduler event %s: %s scope mismatch",
                    self.brand.name, event_name, key,
                )
                return
            scoped_payload[key] = trusted_value

        try:
            if hasattr(self._event_bus, "publish_event"):
                result = self._event_bus.publish_event(event_name, scoped_payload)
            elif hasattr(self._event_bus, "publish"):
                result = self._event_bus.publish(event_name, scoped_payload)
            elif hasattr(self._event_bus, "emit"):
                result = self._event_bus.emit(event_name, scoped_payload)
            else:
                logger.warning(
                    "[%s] Event bus has no supported publish method for %s",
                    self.brand.name, event_name,
                )
                return

            import inspect
            if not inspect.isawaitable(result):
                return

            loop = self._event_loop
            if loop is None or loop.is_closed() or not loop.is_running():
                close = getattr(result, "close", None)
                if close is not None:
                    close()
                logger.error(
                    "[%s] Cannot dispatch scheduler event %s: owner event loop unavailable",
                    self.brand.name, event_name,
                )
                return

            try:
                running_loop = asyncio.get_running_loop()
            except RuntimeError:
                running_loop = None

            if running_loop is loop:
                task = loop.create_task(result)
                def _report_task_error(done_task):
                    if done_task.cancelled():
                        return
                    try:
                        done_task.result()
                    except Exception:
                        logger.exception(
                            "[%s] Scheduler event publication failed: %s",
                            self.brand.name, event_name,
                        )
                task.add_done_callback(_report_task_error)
            else:
                future = asyncio.run_coroutine_threadsafe(result, loop)
                try:
                    future.result(timeout=5.0)
                except Exception:
                    logger.exception(
                        "[%s] Scheduler event publication failed: %s",
                        self.brand.name, event_name,
                    )
        except Exception:
            logger.exception(
                "[%s] Scheduler event dispatch failed: %s",
                self.brand.name, event_name,
            )

    def _emit_dashboard(self, event_name: str, payload: dict) -> None:
        _emit_dashboard_event(event_name, payload, loop=self._event_loop)

    def _notify_webhooks(self, event: str, payload: dict) -> None:
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
                    self._process_queued_automation_jobs()
                    self._process_queued_manual_jobs()
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

    def _retry_dead_letter_queue(self) -> None:
        """Retry failed posts from the dead letter queue.

        v4.6 — SLOT TERMINAL SHORT-CIRCUIT:

        Before calling _trigger_workflow on a DLQ entry, check whether
        the entry's slot has already resolved to a terminal state
        (PUBLISHED or FAILED). If so, the retry would either duplicate
        a successful publish (if PUBLISHED) or waste work on a slot that
        has exhausted its retries (if FAILED). In both cases, ack the
        DLQ entry without running the workflow.

        v4.5 — DATE COERCION FIX:
        The DLQ stores entries via JSON (file or Supabase jsonb), so
        `today` round-trips as a string. Coerce it back to a date (and
        `hour` to an int) before passing it to _trigger_workflow, which
        calls `today.isoformat()`.
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
                today_date = _coerce_date(meta.get("today"))
                hour_int = _coerce_int(meta.get("hour"))

                # v4.6 — Slot terminal short-circuit.
                # If the slot has already resolved, ack the DLQ entry
                # without retrying. This prevents re-publishing a
                # PUBLISHED slot and re-failing a terminal-FAILED slot.
                manual_job_id = meta.get("automation_job_id")
                if manual_job_id and self._job_service:
                    manual_job = _job_call(self._job_service, "find_by_id", str(manual_job_id))
                    if not manual_job or str(getattr(manual_job, "brand_id", "")) != str(self.brand.id) or str(getattr(manual_job, "workspace_id", "")) != str(getattr(self.brand, "workspace_id", "")):
                        self._dead_letter_queue.ack(entry.get("id"))
                        continue
                    manual_status = getattr(getattr(manual_job, "status", None), "value", getattr(manual_job, "status", None))
                    if manual_status == JobStatus.RETRY_PENDING.value:
                        manual_job = _job_call(self._job_service, "reset_to_pending", manual_job.job_id)
                    elif manual_status != JobStatus.QUEUED.value:
                        self._dead_letter_queue.ack(entry.get("id"))
                        continue
                    accepted = bool(manual_job) and self._trigger_workflow(
                        source="manual", existing_job=manual_job,
                    )
                    if accepted:
                        self._dead_letter_queue.ack(entry.get("id"))
                    continue

                if today_date is not None and hour_int is not None and self._job_service:
                    # Do not let legacy DLQ entries create a fresh slot before
                    # the normal workflow gate gets a chance to inspect cooldown.
                    try:
                        cooldown_active = _job_call(
                            self._job_service,
                            "has_recent_provider_outage",
                            brand_id=str(self.brand.id),
                            workspace_id=str(getattr(self.brand, "workspace_id", "") or ""),
                            cooldown_seconds=1800,
                        )
                    except Exception as gate_error:
                        logger.error(
                            "[%s] Provider cooldown lookup failed during DLQ recovery; retaining entry without retry: %s",
                            self.brand.name, gate_error,
                        )
                        continue
                    if cooldown_active:
                        self._dead_letter_queue.ack(entry.get("id"))
                        logger.info(
                            "[%s] Acknowledged stale DLQ retry during active provider cooldown; failed-job evidence remains durable",
                            self.brand.name,
                        )
                        continue

                    _, slot_signal = self._get_or_create_automation_job(
                        brand_id=self.brand.id,
                        workspace_id=getattr(self.brand, "workspace_id", ""),
                        slot_date=today_date.isoformat(),
                        slot_hour=hour_int,
                        job_id=f"dlq_check_{entry.get('id')}",
                        source="dead_letter_retry",
                    )
                    if slot_signal == "SLOT_TERMINAL":
                        self._dead_letter_queue.ack(entry.get("id"))
                        logger.info(
                            "[%s] Dead letter entry %s resolved by terminal slot; acked",
                            self.brand.name, entry.get("id"),
                        )
                        continue

                accepted = self._trigger_workflow(
                    source="dead_letter_retry",
                    today=today_date,
                    hour=hour_int,
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

    def _process_queued_automation_jobs(self, limit: int = 50) -> int:
        """Drain due, brand-scoped scheduled jobs returned to QUEUED.

        The database retry cascade only changes lifecycle state; it does not
        execute jobs. This consumer re-enters the existing idempotent workflow
        path so a queued slot is resumed rather than abandoned after its hour.
        """
        if not self._job_service:
            return 0

        try:
            queued_jobs = _job_call(
                self._job_service,
                "find_by_brand",
                self.brand.id,
                JobStatus.QUEUED,
                limit=limit,
                oldest_first=True,
            )
        except Exception as exc:
            logger.error(
                "[%s] Could not load queued automation jobs: %s",
                self.brand.name, exc,
            )
            return 0

        now_utc = datetime.now(timezone.utc)
        workspace_id = str(getattr(self.brand, "workspace_id", "") or "")

        def _queue_order(job):
            value = (
                getattr(job, "scheduled_for", None)
                or getattr(job, "created_at", None)
                or now_utc
            )
            if not isinstance(value, datetime):
                return now_utc
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)

        candidates = sorted(queued_jobs or [], key=_queue_order)
        accepted = 0

        for job in candidates:
            try:
                raw_status = getattr(job, "status", None)
                status = getattr(raw_status, "value", raw_status)
                raw_workflow_type = getattr(job, "workflow_type", None)
                workflow_type = getattr(raw_workflow_type, "value", raw_workflow_type)
                if status != JobStatus.QUEUED.value:
                    continue
                if getattr(job, "brand_id", None) != self.brand.id:
                    continue
                if str(getattr(job, "workspace_id", "") or "") != workspace_id:
                    logger.warning(
                        "[%s] Skipping queued job %s: workspace scope mismatch",
                        self.brand.name, getattr(job, "job_id", "?"),
                    )
                    continue
                if workflow_type != WorkflowType.CONTENT_PUBLISH.value:
                    continue
                if str(getattr(job, "source", "")) != "scheduled":
                    continue

                scheduled_for = getattr(job, "scheduled_for", None)
                if not isinstance(scheduled_for, datetime):
                    continue
                if scheduled_for.tzinfo is None:
                    scheduled_for = scheduled_for.replace(tzinfo=timezone.utc)
                if scheduled_for > now_utc:
                    continue

                key = str(getattr(job, "idempotency_key", "") or "")
                prefix = f"{self.brand.id}-"
                if not key.startswith(prefix):
                    continue
                try:
                    slot_date_raw, slot_hour_raw, slot_kind = key[len(prefix):].rsplit("-", 2)
                    if slot_kind != "content":
                        continue
                    slot_date = date.fromisoformat(slot_date_raw)
                    slot_hour = int(slot_hour_raw)
                    if slot_hour < 0 or slot_hour > 23:
                        continue
                except (TypeError, ValueError):
                    logger.warning(
                        "[%s] Skipping queued job %s: invalid scheduled-slot key",
                        self.brand.name, getattr(job, "job_id", "?"),
                    )
                    continue

                logger.info(
                    "[%s] Resuming overdue queued job %s for slot %s %02d:00 UTC",
                    self.brand.name,
                    getattr(job, "job_id", "?"),
                    slot_date.isoformat(),
                    slot_hour,
                )
                if self._trigger_workflow(
                    source="queued_retry",
                    today=slot_date,
                    hour=slot_hour,
                ):
                    accepted += 1
            except Exception as exc:
                logger.error(
                    "[%s] Queued job processing failed for %s: %s",
                    self.brand.name, getattr(job, "job_id", "?"), exc,
                )

        return accepted

    def _process_queued_manual_jobs(self, limit: int = 25) -> int:
        """Execute persisted manual jobs outside the HTTP request lifecycle."""
        if not self._job_service or not self.ai_profile:
            return 0
        workspace_id = str(getattr(self.brand, "workspace_id", "") or "")
        try:
            jobs = _job_call(
                self._job_service, "find_by_brand", self.brand.id,
                JobStatus.QUEUED, limit=limit, oldest_first=True,
                workflow_type=WorkflowType.MANUAL_PUBLISH, source="manual",
            )
        except Exception as exc:
            logger.error("[%s] Could not load queued manual jobs: %s", self.brand.name, exc)
            return 0

        processed = 0
        for job in jobs or []:
            status = getattr(getattr(job, "status", None), "value", getattr(job, "status", None))
            workflow_type = getattr(getattr(job, "workflow_type", None), "value", getattr(job, "workflow_type", None))
            if status != JobStatus.QUEUED.value:
                continue
            if workflow_type != WorkflowType.MANUAL_PUBLISH.value or getattr(job, "source", None) != "manual":
                continue
            if getattr(job, "brand_id", None) != self.brand.id:
                continue
            if str(getattr(job, "workspace_id", "") or "") != workspace_id:
                logger.warning("[%s] Skipping manual job %s: workspace scope mismatch", self.brand.name, getattr(job, "job_id", "?"))
                continue
            scheduled_for = getattr(job, "scheduled_for", None)
            if isinstance(scheduled_for, datetime):
                if scheduled_for.tzinfo is None:
                    scheduled_for = scheduled_for.replace(tzinfo=timezone.utc)
                if scheduled_for > datetime.now(timezone.utc):
                    continue
            if self._trigger_workflow(source="manual", existing_job=job):
                processed += 1
        return processed

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

    def _get_or_create_automation_job(
        self, brand_id: str, workspace_id: str, slot_date: str,
        slot_hour: int, job_id: str, source: str = "scheduled",
    ) -> tuple[Optional[AutomationJob], Optional[str]]:
        """Find or create the automation job for a slot.

        Return value:
          (job, idempotency_key)   — proceed with the job
          (None, None)             — skip this slot silently
          (None, "SLOT_TERMINAL")  — slot has already resolved to a
                                     terminal state; caller should ack
                                     any DLQ entry that pointed here

        v4.6 — TERMINAL SLOT SIGNAL:
          PUBLISHED and FAILED are terminal. Reusing a PUBLISHED job
          would duplicate a successful publish; reusing a FAILED job
          would retry a slot that has exhausted its attempts. Both
          return (None, "SLOT_TERMINAL") so the DLQ retry path can ack
          the entry without executing the workflow.
        """
        if not self._job_service:
            return None, f"{brand_id}-{slot_date}-{slot_hour:02d}-content"

        idempotency_key = f"{brand_id}-{slot_date}-{slot_hour:02d}-content"

        existing = _job_call(
            self._job_service, "find_by_idempotency_key", idempotency_key,
        )
        if existing:
            if existing.status == JobStatus.RETRY_PENDING:
                logger.info(
                    "[%s] job=%s — reusing RETRY_PENDING job %s",
                    self.brand.name, job_id, existing.id,
                )
                job = _job_call(
                    self._job_service, "reset_to_pending", existing.job_id,
                )
                if job:
                    return job, idempotency_key
                logger.warning(
                    "[%s] job=%s — reset_to_pending returned nothing for job_id=%s "
                    "(status=%s); skipping slot",
                    self.brand.name, job_id, existing.job_id,
                    existing.status.value if hasattr(existing.status, "value") else existing.status,
                )
                return None, None

            if existing.status == JobStatus.QUEUED:
                logger.info(
                    "[%s] job=%s — reusing QUEUED job %s",
                    self.brand.name, job_id, existing.id,
                )
                return existing, idempotency_key

            if existing.status == JobStatus.PUBLISHED:
                logger.info(
                    "[%s] job=%s — slot already completed by %s",
                    self.brand.name, job_id, existing.id,
                )
                return None, "SLOT_TERMINAL"

            if existing.status == JobStatus.FAILED:
                logger.info(
                    "[%s] job=%s — slot already failed by %s",
                    self.brand.name, job_id, existing.id,
                )
                return None, "SLOT_TERMINAL"

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

    def _build_workflow_context(self, **kwargs) -> dict:
        """Bind every post workflow to its owning brand and concrete workspace."""
        workspace_id = str(getattr(self.brand, "workspace_id", "") or "").strip()
        brand_id = str(getattr(self.brand, "id", "") or "").strip()
        if (
            not workspace_id
            or workspace_id.lower() in {"default", "unbound", "platform"}
            or not brand_id
        ):
            raise ValueError(
                "Post workflow requires a concrete brand and tenant workspace"
            )
        return {
            **kwargs,
            "brand": self.brand,
            "brand_id": brand_id,
            "workspace_id": workspace_id,
        }

    def _execute_workflow(self, workflow, wf_kwargs: dict):
        """Run the workflow with an optional async history store.

        v4.6 — RE-INDENTED: this method body was previously de-dented
        to module scope, which raised IndentationError and prevented
        the module from importing. The `workflow_history` lookup is
        best-effort: if the container lacks `AsyncWorkflowHistory`
        the workflow still executes without it.
        """
        # 1. Resolve the history store from the container
        workflow_history = None
        if self._container is not None:
            try:
                from api.v1_stores import AsyncWorkflowHistory
                workflow_history = self._container.try_resolve(AsyncWorkflowHistory)
            except Exception:
                pass

        # 2. Pass it into the workflow execution
        maybe = workflow.execute_async(
            initial_context=wf_kwargs,
            workflow_history=workflow_history,
        )
        return _run_awaitable_sync(maybe)

    def _extract_workflow_outcome(
        self, result, *, context_platform: Optional[str] = None
    ) -> tuple[str, bool, List[str], bool]:
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
                    platforms = [
                        k for k, v in output.items()
                        if isinstance(v, dict) and v.get("ok")
                    ]

        return topic, publish_ok, platforms, queued_publish

    @staticmethod
    def _extract_confirmed_post_id(result) -> Optional[str]:
        """Return a provider post ID only when the publish step confirms success."""
        for step in getattr(result, "steps", []) or []:
            if getattr(step, "step_name", None) != "publish" or not getattr(step, "ok", False):
                continue
            output = step.output if isinstance(step.output, dict) else {}
            if output.get("queued"):
                return None
            status = str(output.get("status") or "").strip().lower()
            flat_id = (
                output.get("platform_post_id")
                or output.get("provider_object_id")
                or output.get("post_id")
            )
            flat_ok = bool(output.get("ok")) or status in {
                "ok", "succeeded", "success", "published",
            }
            if flat_ok and flat_id:
                return str(flat_id)
            for value in output.values():
                if not isinstance(value, dict):
                    continue
                nested_status = str(value.get("status") or "").strip().lower()
                nested_ok = bool(value.get("ok")) or nested_status in {
                    "ok", "succeeded", "success", "published",
                }
                nested_id = (
                    value.get("platform_post_id")
                    or value.get("provider_object_id")
                    or value.get("post_id")
                )
                if nested_ok and nested_id:
                    return str(nested_id)
        return None

    def _trigger_workflow(
        self, source: str = "scheduled", today=None, hour=None,
        scheduled_post_id: Optional[str] = None,
        scheduled_caption: Optional[str] = None,
        existing_job: Optional[AutomationJob] = None,
    ) -> bool:
        job_id = str(getattr(existing_job, "job_id", "") or f"pub_{datetime.now().strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}")
        agent_name = self.ai_profile.name if self.ai_profile else "Gemini"

        # Publishing is an external side effect: local-only locks are never sufficient.
        require_dist = True
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

        if not self._publish_semaphore.acquire(blocking=False):
            logger.warning(
                "[%s] Backpressure limit reached (%d concurrent) — deferring",
                self.brand.name, self.MAX_CONCURRENT_PUBLISHES,
            )
            self._distributed_lock.release()
            return False

        publish_started = time.monotonic()
        automation_job: Optional[AutomationJob] = existing_job
        idempotency_key: Optional[str] = getattr(existing_job, "idempotency_key", None)
        publish_resources_released = False

        def _release_publish_resources() -> None:
            """Release this pipeline's local slot and owner-token lock once."""
            nonlocal publish_resources_released
            if publish_resources_released:
                return
            publish_resources_released = True
            self._publish_semaphore.release()
            self._distributed_lock.release()

        try:
            if not self._job_service:
                raise RuntimeError(
                    "Publishing requires durable JobService persistence; refusing to run"
                )
            if today is not None and hour is not None:
                workspace_id = getattr(self.brand, "workspace_id", "")
                slot_date = today.isoformat()

                # A prior, explicitly classified provider outage opens a durable
                # brand/workspace cooldown. Check before claiming/creating a slot.
                if source in {"scheduled", "queued_retry", "dead_letter_retry"}:
                    try:
                        provider_cooldown = _job_call(
                            self._job_service,
                            "has_recent_provider_outage",
                            brand_id=str(self.brand.id),
                            workspace_id=str(workspace_id),
                            cooldown_seconds=1800,
                        )
                    except Exception as gate_error:
                        logger.error(
                            "[%s] Provider cooldown lookup failed; failing closed before job creation: %s",
                            self.brand.name, gate_error,
                        )
                        return False
                    if provider_cooldown:
                        logger.warning(
                            "[%s] AI provider cooldown active; scheduled attempt suppressed before job creation",
                            self.brand.name,
                        )
                        return False

                if self._job_service:
                    automation_job, idempotency_key = self._get_or_create_automation_job(
                        brand_id=self.brand.id, workspace_id=workspace_id,
                        slot_date=slot_date, slot_hour=hour, job_id=job_id, source=source,
                    )
                    if automation_job is None and idempotency_key is None:
                        return False
                    if automation_job is None and idempotency_key == "SLOT_TERMINAL":
                        return False
                    if automation_job:
                        try:
                            automation_job = _job_call(
                                self._job_service, "transition",
                                automation_job, JobStatus.RUNNING,
                            )
                        except Exception as e:
                            logger.error(
                                "[%s] job=%s — transition to RUNNING failed; refusing workflow execution: %s",
                                self.brand.name, job_id, e,
                            )
                            return False
            elif automation_job is not None:
                if str(getattr(automation_job, "brand_id", "")) != str(self.brand.id) or str(getattr(automation_job, "workspace_id", "")) != str(getattr(self.brand, "workspace_id", "")):
                    logger.error("[%s] Refusing manual job with mismatched brand/workspace scope", self.brand.name)
                    return False
                if getattr(automation_job, "status", None) != JobStatus.QUEUED:
                    return False
                try:
                    automation_job = _job_call(
                        self._job_service, "transition", automation_job, JobStatus.RUNNING,
                    )
                except Exception as e:
                    logger.error("[%s] job=%s — manual job claim failed: %s", self.brand.name, job_id, e)
                    return False

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

            content_platform = _brand_publish_platform(self.brand)
            factory_name = (
                "ArticlePublishWorkflowFactory"
                if content_platform == "website"
                else "PostPublishWorkflowFactory"
            )
            workflow_factory = self._container.resolve(factory_name)
            workflow = workflow_factory(
                brand=self.brand, ai_profile=self.ai_profile
            )

            wf_kwargs = self._build_workflow_context(
                job_id=job_id,
                platform=content_platform,
                source=source,
                risk_level="low",
                scheduled_post_id=scheduled_post_id,
                scheduled_caption=scheduled_caption,
            )
            result = self._execute_workflow(workflow, wf_kwargs)

            # The provider-facing workflow has returned: no external publish
            # operation remains inside this scheduler critical section. Release
            # the global lock before durable state transitions and event delivery,
            # which may be slow or unavailable. A confirmed provider receipt is
            # still persisted below, and uncertain outcomes remain reconciliation-
            # protected rather than being blindly retried.
            _release_publish_resources()

            context_platform = (
                wf_kwargs.get("platform")
                or getattr(self.brand, "platform", "facebook")
            )
            topic, publish_ok, platforms, queued_publish = self._extract_workflow_outcome(
                result, context_platform=context_platform,
            )
            confirmed_post_id = self._extract_confirmed_post_id(result)

            duration_ms = (time.monotonic() - publish_started) * 1000

            if publish_ok and not queued_publish and not confirmed_post_id:
                logger.error(
                    "[%s] job=%s — publish step returned without a confirmed provider post ID; "
                    "automatic retry suppressed pending reconciliation",
                    self.brand.name, job_id,
                )
                if automation_job and self._job_service:
                    try:
                        _job_call(
                            self._job_service, "mark_failed",
                            automation_job,
                            error="Provider publish outcome unconfirmed; reconcile before retry",
                            error_code="PUBLISH_UNCONFIRMED",
                            retryable=False,
                        )
                    except Exception as persist_error:
                        logger.error(
                            "[%s] job=%s — could not persist unconfirmed publish state: %s",
                            self.brand.name, job_id, persist_error,
                        )
                self._pipeline_status = {
                    "status": "unknown", "current_step": "reconciliation",
                    "progress": 90, "topic": topic, "platforms": [],
                    "job_id": job_id,
                }
                return False

            if publish_ok and not queued_publish and confirmed_post_id:
                if today is not None and hour is not None:
                    self._fired_slots.add((today, hour, "content"))

                if automation_job and self._job_service:
                    try:
                        _job_call(
                            self._job_service, "mark_published",
                            automation_job, post_id=confirmed_post_id,
                            result={"topic": topic, "platforms": platforms},
                        )
                    except Exception as e:
                        logger.error(
                            "[%s] job=%s — verified Facebook receipt could not be persisted: %s",
                            self.brand.name, job_id, e,
                        )
                        self._pipeline_status = {
                            "status": "unknown", "current_step": "reconciliation",
                            "progress": 90, "topic": topic, "platforms": [],
                            "job_id": job_id,
                        }
                        return False

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

                # The provider receipt and durable job state are now recorded.
                # Do not let dashboard/webhook delivery hold every other brand
                # behind the global publishing mutex.
                _release_publish_resources()

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
                    pass
                self._pipeline_status = {
                    "status": "queued", "current_step": "outbox", "progress": 90,
                    "topic": topic, "platforms": [], "job_id": job_id,
                }
                # Durable outbox handoff completes this scheduler's work; the
                # outbox worker owns subsequent provider delivery/reconciliation.
                _release_publish_resources()
                self._emit("scheduler.workflow_queued", {
                    "brand_id": self.brand.id, "job_id": job_id, "topic": topic,
                })
                return True

            else:
                failed = [s.step_name for s in result.steps if not s.ok]
                failure_evidence = result.failure_evidence()
                provider_outage = _is_ai_provider_outage(failure_evidence)
                retryable = _workflow_failure_is_retryable(failure_evidence)
                failure_code = "AI_PROVIDER_UNAVAILABLE" if provider_outage else "WORKFLOW_FAILED"
                # Provider outages are durable terminal evidence, not DLQ work.
                if automation_job and self._job_service:
                    try:
                        automation_job.result = failure_evidence
                        _job_call(
                            self._job_service, "mark_failed",
                            automation_job, error=f"Steps failed: {failed}",
                            error_code=failure_code, retryable=retryable,
                        )
                    except Exception as e:
                        logger.error(
                            "[%s] job=%s — mark failed failed: %s",
                            self.brand.name, job_id, e,
                        )
                self._posts_failed += 1
                self._metrics.record_publish_failure(duration_ms)

                if retryable:
                    self._dead_letter_queue.push(
                        brand_id=self.brand.id,
                        error=f"Steps failed: {failed}",
                        payload={
                            "source": source, "today": today, "hour": hour,
                            "automation_job_id": getattr(automation_job, "job_id", None),
                            "failure_evidence": failure_evidence,
                        },
                        workspace_id=getattr(self.brand, "workspace_id", None),
                    )

                # Failure state and any retry handoff have been persisted.
                # Release before nonessential logging/return-path work.
                _release_publish_resources()
                retry_note = ("provider cooldown opened; retry suppressed" if provider_outage else ("queued for retry" if retryable else "terminal validation failure; retry suppressed"))
                logger.warning(
                    "[%s] job=%s FAILED — steps=%s evidence=%s (%s)",
                    self.brand.name,
                    job_id,
                    failed,
                    [
                        {
                            "step": item["step"],
                            "code": item["error_code"],
                            "reason": item["error"][:300],
                        }
                        for item in failure_evidence["failed_steps"]
                    ],
                    retry_note,
                )
                return False

        except Exception as e:
            duration_ms = (time.monotonic() - publish_started) * 1000
            provider_outage = _is_ai_provider_outage({
                "failed_steps": [{"step": "ai_generation", "error": str(e)}],
            })
            if automation_job and self._job_service:
                try:
                    from core.errors import is_retryable
                    _job_call(
                        self._job_service, "mark_failed",
                        automation_job, error=str(e),
                        error_code="AI_PROVIDER_UNAVAILABLE" if provider_outage else "WORKFLOW_EXCEPTION",
                        retryable=False if provider_outage else is_retryable(e),
                    )
                except Exception:
                    pass
            self._posts_failed += 1
            self._metrics.record_publish_failure(duration_ms)

            if not provider_outage:
                self._dead_letter_queue.push(
                    brand_id=self.brand.id,
                    error=str(e),
                    payload={
                        "source": source, "today": today, "hour": hour,
                        "automation_job_id": getattr(automation_job, "job_id", None),
                    },
                    workspace_id=getattr(self.brand, "workspace_id", None),
                )

            logger.error(
                "[%s] job=%s EXCEPTION: %s (%s)",
                self.brand.name, job_id, e,
                "provider cooldown opened; retry suppressed" if provider_outage else "queued for retry",
            )
            return False
        finally:
            # Covers early returns and exceptions; safe after an earlier
            # terminal-boundary release because the helper is idempotent.
            _release_publish_resources()

    def _trigger_news(self) -> None:
        logger.warning(
            "BrandScheduler news publication fenced: canonical V70/V71 path required"
        )
        return

    def trigger_manual(self, idempotency_key: Optional[str] = None) -> dict:
        """Persist a manual publishing job and return without running the workflow."""
        if not self._job_service:
            return {"ok": False, "error": "Manual publishing requires durable JobService persistence"}
        if not self.ai_profile:
            return {"ok": False, "error": "Brand has no AI profile"}

        job_id = f"pub_{datetime.now().strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"
        workspace_id = str(getattr(self.brand, "workspace_id", "") or "")
        try:
            automation_job = _job_call(
                self._job_service, "create_manual_job",
                brand_id=self.brand.id, workspace_id=workspace_id, job_id=job_id,
                idempotency_key=idempotency_key,
            )
            if not automation_job:
                raise RuntimeError("Manual publishing job was not durably created")
            status = getattr(getattr(automation_job, "status", None), "value", getattr(automation_job, "status", None))
            return {
                "ok": True,
                "queued": status in {JobStatus.CREATED.value, JobStatus.QUEUED.value, JobStatus.RETRY_PENDING.value},
                "published": status == JobStatus.PUBLISHED.value,
                "status": status,
                "brand": self.brand.name,
                "job_id": automation_job.job_id,
            }
        except Exception as exc:
            logger.exception("[%s] Failed to enqueue manual publish", self.brand.name)
            return {"ok": False, "error": str(exc), "brand": self.brand.name}

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

        # Refresh dependencies on schedulers created before this container bind.
        # Manual API triggers must share the same durable JobService and Redis lock client.
        for scheduler in self._schedulers.values():
            # A transient/missing container registration must not erase a
            # previously valid dependency from an already-running scheduler.
            # Missing dependencies still fail closed for newly created schedulers
            # and manual triggers.
            if self._job_service is not None:
                scheduler._job_service = self._job_service
            if self._redis_client is not None:
                scheduler._distributed_lock.redis = self._redis_client

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
                await scheduler.start()

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
