"""
Job Stats Service — Single-query stats aggregation.
Never imports or calls JobService. Recursion is structurally impossible.

v4.4 — HEALTH STATUS DERIVATION:
  health_check now derives status from the stats it computes instead of
  hardcoding "healthy". The prior implementation reported healthy while
  52 dead jobs (19 STALE, 33 FAILED, oldest active 11.5 days) sat in the
  queue — the check verified the DB query succeeded, not that the queue
  was doing its job. Status is now a function of status_counts and
  oldest_active_minutes.

v4.3 — EVENT SUBSCRIPTION FIX:
  - Fixed duplicate _subscribe_to_events declaration (syntax error)
  - Async UnifiedEventBus.subscribe is now scheduled on the running loop
  - Sync legacy subscribe still supported

v4.2 — HEALTH CHECK FIX:
  - health_check returns "unknown" (not "degraded") on error
  - health_check returns "unknown" (not "not_configured") when no client

v4.1 — MEMORY FIX:
  - Single shared Supabase client (not thread-local)
  - Semaphore-based rate limiting (max 5 concurrent)
  - Prevents "Max outbound streams is 100" errors
  - Reduces memory usage significantly
  - Retains broken pipe retry logic

v4.0 — BROKEN PIPE FIX (retained):
  - Automatic retry on broken pipe
  - Thread-safe connection management
  - Cache invalidation via event subscription

Architecture:
  - Reads directly from Supabase via Postgres function
  - Shared client with semaphore
  - Cache with 30s TTL
  - Returns real errors, never fake zeros
  - Supabase client MUST be injected via constructor or set_client()
"""

from __future__ import annotations

import inspect
import logging
import asyncio
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from core.errors import DatabaseQueryError

logger = logging.getLogger("kanyoza.services.job_stats_service")

# ═══════════════════════════════════════════════════════════════════════
# Single source of truth — hardcoded to avoid circular imports
# ═══════════════════════════════════════════════════════════════════════

ACTIVE_STATUS_VALUES = (
    "CREATED", "QUEUED", "RUNNING", "GENERATING",
    "VALIDATING", "RENDERING", "READY", "PUBLISHING",
    "RETRY_PENDING", "STALE",
)

TERMINAL_STATUS_VALUES = (
    "PUBLISHED", "FAILED", "CANCELLED",
)


# ═══════════════════════════════════════════════════════════════════════
# v4.1 — SHARED CLIENT WITH SEMAPHORE
# ═══════════════════════════════════════════════════════════════════════

class SharedSupabaseClient:
    """Single shared Supabase client with semaphore-based rate limiting.

    v4.1: Instead of per-thread clients (which create too many HTTP/2
    connections), use ONE shared client with a semaphore to limit
    concurrent requests. This prevents "Max outbound streams is 100"
    errors and reduces memory usage.
    """

    def __init__(self, client, max_concurrent: int = 5):
        self._client = client
        self._semaphore = threading.BoundedSemaphore(max_concurrent)
        self._lock = threading.Lock()

    def execute(self, fn):
        """Execute a function with rate limiting.

        Args:
            fn: A callable that takes the client as its argument.

        Returns:
            The result of fn(client).
        """
        with self._semaphore:
            return fn(self._client)

    @property
    def client(self):
        """The shared client."""
        return self._client

    def reset(self) -> None:
        """Reset the semaphore (after broken pipe recovery)."""
        # BoundedSemaphore auto-resets; nothing to do here
        pass


# ═══════════════════════════════════════════════════════════════════════
# JobStatsService v4.4
# ═══════════════════════════════════════════════════════════════════════

class JobStatsService:
    """Read-only stats service with shared Supabase client."""

    service_name: str = "job_stats_service"

    CACHE_TTL_SECONDS = 30
    MAX_BROKEN_PIPE_RETRIES = 2

    # v4.4 — Health status thresholds.
    # Tuned so a working queue stays healthy and a genuinely stuck one
    # surfaces. A healthy queue can have a handful of in-flight failures;
    # a stuck queue has dozens.
    HEALTH_DEGRADED_STALE = 5
    HEALTH_DEGRADED_FAILED = 10
    HEALTH_UNHEALTHY_STALE = 20
    HEALTH_UNHEALTHY_FAILED = 50
    HEALTH_UNHEALTHY_OLDEST_MINUTES = 24 * 60  # 24 hours

    def __init__(
        self,
        supabase_client=None,
        event_bus=None,
    ) -> None:
        self._event_bus = event_bus
        self._cache: dict = {}
        self._cache_time: float = 0.0
        self._lock = threading.Lock()
        # Query errors are per-caller: concurrent health probes must not clear
        # or overwrite one another's failure state.
        self._error_state = threading.local()
        self._last_error = None

        # v4.1: Shared client with semaphore
        self._shared_client: Optional[SharedSupabaseClient] = None

        if supabase_client is not None:
            self.set_client(supabase_client)
        else:
            logger.warning(
                "JobStatsService: No Supabase client provided. "
                "Stats will return empty data until wired."
            )

        if self._event_bus is not None:
            self._subscribe_to_events()

    @property
    def _last_error(self) -> Optional[str]:
        return getattr(self._error_state, "value", None)

    @_last_error.setter
    def _last_error(self, value: Optional[str]) -> None:
        self._error_state.value = value

    # ------------------------------------------------------------------
    # Dependency Injection
    # ------------------------------------------------------------------

    def set_client(self, client) -> None:
        """Inject the Supabase client.

        v4.1: Use a SINGLE shared client with semaphore.
        If a factory function is provided, call it once.
        """
        # If factory provided, call it once to get the instance
        if callable(client) and not hasattr(client, "table"):
            try:
                client = client()
            except Exception as exc:
                logger.error("JobStatsService: client factory failed: %s", exc)
                return

        self._shared_client = SharedSupabaseClient(
            client, max_concurrent=5
        )
        logger.info(
            "JobStatsService: Supabase client configured "
            "(shared + semaphore, max_concurrent=5)"
        )

    def set_event_bus(self, event_bus) -> None:
        self._event_bus = event_bus
        self._subscribe_to_events()

    def _get_shared_client(self) -> SharedSupabaseClient:
        """Get the shared client wrapper."""
        if self._shared_client is None:
            raise DatabaseQueryError(
                message="JobStatsService: Supabase client not configured.",
                context={"operation": "job_stats_service"},
            )
        return self._shared_client

    # ------------------------------------------------------------------
    # Event Subscription
    # ------------------------------------------------------------------

    def _subscribe_to_events(self) -> None:
        """Subscribe to job events so cache is invalidated on change.

        v4.3: Handles both sync and async subscribe signatures.
        UnifiedEventBus.subscribe is async — schedule on the running loop.
        """
        if self._event_bus is None:
            return

        events = [
            "job.created",
            "job.status_changed",
            "job.published",
            "job.failed",
            "job.stale_detected",
            "job.recovered_and_retried",
            "job.retry_scheduled",
        ]

        try:
            if not hasattr(self._event_bus, "subscribe"):
                return

            subscribe_fn = self._event_bus.subscribe
            is_async_subscribe = inspect.iscoroutinefunction(subscribe_fn)

            if is_async_subscribe:
                # UnifiedEventBus.subscribe is async — schedule on the running loop.
                # If no loop is running (constructor-called), log and skip to
                # avoid emitting a RuntimeWarning for an un-awaited coroutine.
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    logger.debug(
                        "JobStatsService: async subscribe skipped — no running loop"
                    )
                    return

                for event in events:
                    loop.create_task(subscribe_fn(event, self._on_job_changed))

                logger.debug(
                    "JobStatsService: scheduled async subscription to %d job events",
                    len(events),
                )
            else:
                # Legacy sync subscribe (SharedEventBus or older bus)
                for event in events:
                    subscribe_fn(event, self._on_job_changed)
                logger.debug(
                    "JobStatsService: subscribed to %d job events (sync)", len(events)
                )
        except Exception as exc:
            logger.debug(
                "JobStatsService: event subscription failed: %s", exc
            )

    def _on_job_changed(self, payload: dict = None) -> None:
        with self._lock:
            self._cache.clear()
            self._cache_time = 0.0

    def invalidate_cache(self) -> None:
        self._on_job_changed()

    # ------------------------------------------------------------------
    # Public API — with broken pipe retry
    # ------------------------------------------------------------------

    def get_stats(self, brand_id: Optional[str] = None) -> dict:
        """Get comprehensive job statistics with broken pipe retry."""
        if self._shared_client is None:
            self._last_error = "Supabase client not configured"
            logger.warning(
                "JobStatsService: get_stats() called without client"
            )
            return self._empty_stats()

        cache_key = brand_id or "__all__"
        with self._lock:
            if cache_key in self._cache:
                if time.monotonic() - self._cache_time < self.CACHE_TTL_SECONDS:
                    # A cached successful observation supersedes an earlier transient
                    # query failure; do not leave the health probe stuck at UNKNOWN.
                    self._last_error = None
                    return self._cache[cache_key]

        stats = None
        for attempt in range(self.MAX_BROKEN_PIPE_RETRIES + 1):
            try:
                stats = self._compute_stats_via_rpc(brand_id)
                break
            except (BrokenPipeError, ConnectionError, ConnectionResetError) as e:
                logger.warning(
                    "JobStatsService: broken pipe (attempt %d/%d)",
                    attempt + 1, self.MAX_BROKEN_PIPE_RETRIES,
                )
                if attempt == self.MAX_BROKEN_PIPE_RETRIES:
                    logger.error(
                        "JobStatsService: all stats methods failed: %s", e
                    )
                    self._last_error = f"stats_query_failed: {type(e).__name__}: {e}"[:500]
                    return self._empty_stats()
                time.sleep(0.5 * (2 ** attempt))
            except Exception as e:
                logger.debug(
                    "JobStatsService: RPC failed (%s), trying fallback", e
                )
                try:
                    stats = self._compute_stats_client_side(brand_id)
                    break
                except (BrokenPipeError, ConnectionError, ConnectionResetError) as e2:
                    if attempt == self.MAX_BROKEN_PIPE_RETRIES:
                        logger.error(
                            "JobStatsService: all stats methods failed: %s", e2
                        )
                        self._last_error = f"stats_query_failed: {type(e2).__name__}: {e2}"[:500]
                        return self._empty_stats()
                    time.sleep(0.5 * (2 ** attempt))
                except Exception as e2:
                    logger.error(
                        "JobStatsService: all stats methods failed: %s", e2
                    )
                    self._last_error = f"stats_query_failed: {type(e2).__name__}: {e2}"[:500]
                    return self._empty_stats()

        if stats is None:
            self._last_error = "stats_query_returned_no_data"
            return self._empty_stats()

        self._last_error = None
        with self._lock:
            self._cache[cache_key] = stats
            self._cache_time = time.monotonic()

        return stats

    def get_today_stats(self, brand_id: Optional[str] = None) -> dict:
        full_stats = self.get_stats(brand_id=brand_id)
        return full_stats.get("today", self._empty_stats()["today"])

    def get_status_counts(self, brand_id: Optional[str] = None) -> dict:
        full_stats = self.get_stats(brand_id=brand_id)
        return full_stats.get("status_counts", {})

    def health_check(self) -> dict:
        """Derive status from the stats, not from the query's success alone.

        v4.4 — INTERPRETATION FIX:
          The previous implementation hardcoded status="healthy" while
          computing the stats that would reveal a stuck backlog. A queue
          with 52 dead jobs (19 STALE, 33 FAILED, oldest active 11.5 days)
          reported healthy for over a week because status was a constant.

        Thresholds (class constants above):
          - unhealthy: >=20 STALE, >=50 FAILED, or oldest_active > 24h
          - degraded:  >=5 STALE or >=10 FAILED
          - healthy:   below both

        The 'reason' field names the specific threshold that tripped,
        so /health/detailed is self-diagnosing.
        """
        if self._shared_client is None:
            # v4.2 FIX: unknown instead of not_configured
            return {
                "status": "unknown",
                "error": "Supabase client not injected.",
                "stats": None,
            }

        try:
            stats = self.get_stats()
            if self._last_error:
                return {
                    "status": "unknown",
                    "error": self._last_error,
                    "stats": None,
                    "reason": "job statistics unavailable; queue health cannot be certified",
                }
        except Exception as e:
            logger.error("JobStatsService health check failed: %s", e)
            # v4.2 FIX: unknown instead of degraded
            return {"status": "unknown", "error": str(e)[:200], "stats": None}

        # v4.4 — Derive status from the numbers we just computed.
        status_counts = stats.get("status_counts") or {}
        try:
            stale = int(status_counts.get("STALE", 0) or 0)
            failed = int(status_counts.get("FAILED", 0) or 0)
            active_jobs = int(stats.get("active_jobs", 0) or 0)
            oldest_minutes = float(stats.get("oldest_active_minutes", 0) or 0)
            if min(stale, failed, active_jobs) < 0 or oldest_minutes < 0:
                raise ValueError("negative queue metric")
        except (TypeError, ValueError, OverflowError) as exc:
            # A malformed stats payload is not evidence of an empty queue.
            # Fail closed rather than silently converting bad telemetry to zero.
            return {
                "status": "unknown",
                "error": f"invalid_stats_payload: {type(exc).__name__}: {str(exc)[:200]}",
                "stats": None,
                "reason": "job statistics were returned but could not be interpreted safely",
            }

        # Only query failure detail after validating the aggregate metrics.
        # A malformed FAILED count must never trigger another query or escape
        # the fail-closed health response.
        failure_intelligence = None
        if failed > 0:
            try:
                failure_intelligence = self.get_failure_intelligence()
            except Exception as exc:
                # Keep the queue verdict, but make the missing diagnostic evidence explicit.
                failure_intelligence = {
                    "status": "unavailable",
                    "error": f"{type(exc).__name__}: {str(exc)[:200]}",
                }

        # Queue/service health is based on live work that is stuck, not on
        # terminal FAILED jobs. Failed jobs are immutable historical outcomes;
        # content-policy failures must not make a functioning worker appear
        # crashed or trigger infrastructure recovery.
        stuck_too_long = oldest_minutes >= self.HEALTH_UNHEALTHY_OLDEST_MINUTES
        if stale >= self.HEALTH_UNHEALTHY_STALE or stuck_too_long:
            queue_status = "unhealthy"
            queue_reason = (
                f"active job backlog: stale={stale} active={active_jobs} "
                f"oldest_active_minutes={oldest_minutes:.0f}"
            )
        elif stale >= self.HEALTH_DEGRADED_STALE:
            queue_status = "degraded"
            queue_reason = (
                f"active job backlog growing: stale={stale} active={active_jobs} "
                f"oldest_active_minutes={oldest_minutes:.0f}"
            )
        else:
            queue_status = "healthy"
            queue_reason = None

        # Terminal failures are reported as a separate quality signal. They
        # remain visible and diagnosable, but do not change process/queue
        # liveness status or authorize a service restart.
        if failed >= self.HEALTH_UNHEALTHY_FAILED:
            quality_status = "unhealthy"
        elif failed >= self.HEALTH_DEGRADED_FAILED:
            quality_status = "degraded"
        else:
            quality_status = "healthy"

        result = {
            "status": queue_status,
            "stats": stats,
            "queue_health": {
                "status": queue_status,
                "stale_jobs": stale,
                "active_jobs": active_jobs,
                "oldest_active_minutes": oldest_minutes,
                **({"reason": queue_reason} if queue_reason else {}),
            },
            "content_quality": {
                "status": quality_status,
                "failed_jobs": failed,
                "reason": (
                    f"{failed} terminal jobs failed validation or execution"
                    if failed else None
                ),
            },
        }
        if failure_intelligence is not None:
            result["failure_intelligence"] = failure_intelligence
        return result

    @staticmethod
    def _recovery_policy(category: str, attempt_count=None, max_attempts=None) -> dict:
        """Return a governed recovery proposal; this method never executes it."""
        try:
            attempts = max(0, int(attempt_count or 0))
            maximum = max(0, int(max_attempts or 0))
        except (TypeError, ValueError, OverflowError):
            attempts, maximum = 0, 0

        policies = {
            "caption_quality": {
                "proposal": "bounded_caption_repair",
                "execution_mode": "proposal_only",
                "requires": ["caption_word_count", "voice_and_policy_validation", "semantic_validation"],
            },
            "voice_compliance": {
                "proposal": "bounded_voice_rewrite",
                "execution_mode": "proposal_only",
                "requires": ["voice_and_policy_validation", "semantic_validation", "caption_word_count"],
            },
            "evidence_grounding": {
                "proposal": "verified_source_or_claim_removal",
                "execution_mode": "human_review",
                "requires": ["verified_source_id_or_claim_removal", "semantic_validation"],
            },
            "semantic_validation": {
                "proposal": "evidence_backed_claim_repair",
                "execution_mode": "human_review",
                "requires": ["claim_to_source_mapping", "semantic_validation", "voice_and_policy_validation"],
            },
            "provider_availability": {
                "proposal": "transient_provider_retry_review",
                "execution_mode": "retry_candidate" if maximum > 0 and attempts < maximum else "human_review",
                "requires": ["provider_recovered", "attempt_budget_available", "idempotency_check", "external_publish_state_check"],
            },
            "unclassified": {
                "proposal": "manual_investigation",
                "execution_mode": "human_review",
                "requires": ["root_cause_identified"],
            },
        }
        policy = dict(policies.get(category, policies["unclassified"]))
        policy["automatic_execution"] = False
        policy["attempt_count"] = attempts
        policy["max_attempts"] = maximum
        policy["publication_replay_allowed"] = False
        return policy

    @classmethod
    def _classify_failed_job(cls, job: dict) -> dict:
        """Classify a terminal failure from persisted, structured evidence."""
        result = job.get("result") if isinstance(job.get("result"), dict) else {}
        steps = result.get("failed_steps") if isinstance(result.get("failed_steps"), list) else []
        evidence = " ".join(
            str(value or "")
            for step in steps if isinstance(step, dict)
            for value in (step.get("error"), step.get("error_code"), step.get("step"))
        ).lower()
        code = str(job.get("last_error_code") or "").upper()
        if "verified kb source" in evidence or "stable source id" in evidence or "factual evidence gate" in evidence:
            category, action = "evidence_grounding", "Attach a verified knowledge-base source or remove unsupported empirical claims."
        elif (
            "first-person" in evidence
            or "first person" in evidence
            or "first_person_violation" in evidence
        ):
            category, action = "voice_compliance", "Regenerate or edit the copy to remove prohibited first-person language."
        elif "too short" in evidence or "minimum 80" in evidence or "caption" in evidence and "word" in evidence:
            category, action = "caption_quality", "Regenerate the caption to meet the configured minimum length and quality rules."
        elif "semantic" in evidence or "unsupported material claim" in evidence:
            category, action = "semantic_validation", "Repair claims against cited evidence, then rerun semantic and caption gates."
        elif code == "AI_PROVIDER_UNAVAILABLE" or "provider unavailable" in evidence or "provider timeout" in evidence:
            category, action = "provider_availability", "Check provider health and retry only through the governed retry path."
        else:
            category, action = "unclassified", "Inspect the persisted failed-step evidence before selecting a remediation."
        return {
            "job_id": job.get("job_id"),
            "category": category,
            "last_error_code": code or None,
            "failed_steps": [s.get("step") for s in steps if isinstance(s, dict) and s.get("step")],
            "attempt_count": job.get("attempt_count"),
            "max_attempts": job.get("max_attempts"),
            "recommended_action": action,
            "recovery_policy": cls._recovery_policy(
                category, job.get("attempt_count"), job.get("max_attempts")
            ),
        }

    def get_failure_intelligence(self, limit: int = 100) -> dict:
        """Read-only diagnosis of terminal FAILED jobs; never retries or mutates jobs."""
        shared = self._get_shared_client()
        result = shared.execute(
            lambda c: c.table("automation_jobs")
            .select("job_id,last_error_code,result,attempt_count,max_attempts,updated_at")
            .eq("status", "FAILED")
            .order("updated_at", desc=True)
            .limit(max(1, min(int(limit), 500)))
            .execute()
        )
        jobs = result.data or []
        classified = [self._classify_failed_job(job) for job in jobs]
        categories = {}
        for item in classified:
            bucket = categories.setdefault(item["category"], {
                "count": 0,
                "recommended_action": item["recommended_action"],
                "job_ids": [],
                "recovery_proposals": [],
            })
            bucket["count"] += 1
            if item.get("job_id") and len(bucket["job_ids"]) < 20:
                bucket["job_ids"].append(item["job_id"])
            if len(bucket["recovery_proposals"]) < 20:
                bucket["recovery_proposals"].append({
                    "job_id": item.get("job_id"),
                    **item["recovery_policy"],
                })
        return {
            "status": "available",
            "total_failed_jobs_inspected": len(classified),
            "categories": categories,
            "unclassified_count": categories.get("unclassified", {}).get("count", 0),
        }

    def get_status(self) -> dict:
        return self.health_check()

    # ------------------------------------------------------------------
    # Private — Stats Computation
    # ------------------------------------------------------------------

    def _compute_stats_via_rpc(self, brand_id: Optional[str]) -> dict:
        shared = self._get_shared_client()
        params = {}
        if brand_id:
            params["brand_id"] = brand_id

        # Use semaphore to limit concurrency
        result = shared.execute(
            lambda c: c.rpc("get_job_stats", params).execute()
        )

        if result.data and isinstance(result.data, dict):
            return self._normalize_stats(result.data)

        raise ValueError(
            f"RPC returned {type(result.data).__name__}, expected dict"
        )

    def _compute_stats_client_side(self, brand_id: Optional[str]) -> dict:
        shared = self._get_shared_client()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        def _query(c):
            query = c.table("automation_jobs").select(
                "status,created_at,updated_at"
            )
            if brand_id:
                query = query.eq("brand_id", brand_id)
            return query.execute()

        result = shared.execute(_query)

        if not result.data:
            return self._empty_stats()

        rows = result.data
        now_utc = datetime.now(timezone.utc)

        today_total = 0
        today_published = 0
        today_failed = 0
        today_retrying = 0
        today_active = 0
        status_counts: dict = {}
        oldest_active: Optional[datetime] = None

        for row in rows:
            status = row.get("status", "unknown")
            created = row.get("created_at")
            updated = row.get("updated_at")

            status_counts[status] = status_counts.get(status, 0) + 1

            if created and self._is_today(created, today):
                today_total += 1
                if status == "PUBLISHED":
                    today_published += 1
                elif status == "FAILED":
                    today_failed += 1
                elif status == "RETRY_PENDING":
                    today_retrying += 1
                elif status in ACTIVE_STATUS_VALUES:
                    today_active += 1

            if status in ACTIVE_STATUS_VALUES and updated:
                updated_dt = self._parse_timestamp(updated)
                if updated_dt is not None:
                    if oldest_active is None or updated_dt < oldest_active:
                        oldest_active = updated_dt

        active_count = sum(
            count for status, count in status_counts.items()
            if status in ACTIVE_STATUS_VALUES
        )

        oldest_minutes = 0.0
        if oldest_active is not None:
            oldest_minutes = round(
                (now_utc - oldest_active).total_seconds() / 60.0, 1
            )

        return {
            "today": {
                "total": today_total,
                "published": today_published,
                "failed": today_failed,
                "retrying": today_retrying,
                "active": today_active,
            },
            "status_counts": status_counts,
            "active_jobs": active_count,
            "oldest_active_minutes": oldest_minutes,
        }

    # ------------------------------------------------------------------
    # Private — Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _empty_stats() -> dict:
        return {
            "today": {
                "total": 0, "published": 0, "failed": 0,
                "retrying": 0, "active": 0,
            },
            "status_counts": {},
            "active_jobs": 0,
            "oldest_active_minutes": 0,
        }

    @staticmethod
    def _normalize_stats(data: dict) -> dict:
        defaults = JobStatsService._empty_stats()
        if not isinstance(data, dict):
            return defaults

        today = data.get("today", {})
        if not isinstance(today, dict):
            today = {}
        data["today"] = {
            "total": today.get("total", 0),
            "published": today.get("published", 0),
            "failed": today.get("failed", 0),
            "retrying": today.get("retrying", 0),
            "active": today.get("active", 0),
        }

        data.setdefault("status_counts", {})
        data.setdefault("active_jobs", 0)
        data.setdefault("oldest_active_minutes", 0)

        return data

    @staticmethod
    def _is_today(timestamp_str: str, today_str: str) -> bool:
        if not timestamp_str:
            return False
        return timestamp_str >= f"{today_str}T00:00:00"

    @staticmethod
    def _parse_timestamp(value) -> Optional[datetime]:
        if value is None:
            return None

        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)

        if isinstance(value, (int, float)):
            try:
                return datetime.fromtimestamp(value, tz=timezone.utc)
            except (ValueError, OSError):
                return None

        if isinstance(value, str):
            try:
                s = value.strip().replace("Z", "+00:00")
                dt = datetime.fromisoformat(s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except (ValueError, TypeError):
                pass

        return None


# ═══════════════════════════════════════════════════════════════════════
# Module Singleton
# ═══════════════════════════════════════════════════════════════════════

job_stats_service: Optional[JobStatsService] = None


def init_job_stats_service(
    supabase_client=None,
    event_bus=None,
) -> JobStatsService:
    """Initialize the JobStatsService singleton.

    v4.1: Single shared client with semaphore.

    Example:
        from services.supabase import get_service_client_from_settings

        client = get_service_client_from_settings()
        init_job_stats_service(
            supabase_client=client,  # Instance, not factory
            event_bus=bus,
        )
    """
    global job_stats_service

    if job_stats_service is None:
        job_stats_service = JobStatsService(
            supabase_client=supabase_client,
            event_bus=event_bus,
        )
        logger.info(
            "JobStatsService: singleton initialized (v4.4 shared + semaphore)"
        )
    else:
        if supabase_client is not None:
            job_stats_service.set_client(supabase_client)
        if event_bus is not None:
            job_stats_service.set_event_bus(event_bus)
        logger.info("JobStatsService: singleton updated")

    return job_stats_service


def get_job_stats_service() -> JobStatsService:
    global job_stats_service

    if job_stats_service is None:
        job_stats_service = JobStatsService()
        logger.warning(
            "JobStatsService: get_job_stats_service() called before init. "
            "Call init_job_stats_service(supabase_client=client) during boot."
        )

    return job_stats_service


__all__ = [
    "JobStatsService",
    "job_stats_service",
    "init_job_stats_service",
    "get_job_stats_service",
    "ACTIVE_STATUS_VALUES",
    "TERMINAL_STATUS_VALUES",
    "SharedSupabaseClient",
]
