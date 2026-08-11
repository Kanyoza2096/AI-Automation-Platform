"""
Persistent Job Service — Architecture §7-8.

v3.5 — Added 30-second stats cache to prevent query storms from
       MetaEngine health checks. Fixed recursion in stats queries.

Architecture §7 — Persistent Job Lifecycle:
  CREATED → QUEUED → RUNNING → GENERATING → VALIDATING →
  RENDERING → READY → PUBLISHING → PUBLISHED
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional, List

from models.automation_job import (
    AutomationJob,
    JobStatus,
    WorkflowType,
    ACTIVE_STATES,
    TERMINAL_STATES,
)
from core.errors import (
    DatabaseQueryError,
    DuplicateJobError,
    ErrorCode,
)

logger = logging.getLogger("kanyoza.services.job_service")


class JobService:
    """Persistent CRUD service for automation jobs."""

    # v3.5 — cache TTL for stats to prevent query storms
    STATS_CACHE_TTL_SECONDS = 30

    def __init__(
        self, supabase_client=None, event_bus=None
    ) -> None:
        self._client = supabase_client
        self._event_bus = event_bus
        self._table = "automation_jobs"

        # v3.5 — stats cache
        self._stats_cache: Optional[dict] = None
        self._stats_cache_time: float = 0.0
        self._stats_lock = threading.Lock()

    # ------------------------------------------------------------------
    # EventBus wiring
    # ------------------------------------------------------------------

    def set_event_bus(self, event_bus) -> None:
        self._event_bus = event_bus
        logger.info("JobService: EventBus wired")

    def _emit(
        self, event_name: str, payload: dict
    ) -> None:
        if not self._event_bus:
            return
        try:
            if hasattr(self._event_bus, "publish"):
                self._event_bus.publish(event_name, payload)
            elif hasattr(self._event_bus, "emit"):
                self._event_bus.emit(event_name, payload)
        except Exception as exc:
            logger.debug(
                "JobService event emission failed: %s", exc
            )

    # ------------------------------------------------------------------
    # Client resolution
    # ------------------------------------------------------------------

    def _get_client(self):
        if self._client:
            return self._client
        try:
            from services.supabase import supabase_service
            return (
                supabase_service.service_client
                or supabase_service.client
            )
        except Exception:
            return None

    def _require_client(self):
        client = self._get_client()
        if client is None:
            raise DatabaseQueryError(
                message="Supabase client not available for job service",
                context={"operation": "job_service"},
            )
        return client

    # ═══════════════════════════════════════════════════════════════════
    # Job Creation
    # ═══════════════════════════════════════════════════════════════════

    def create_scheduled_job(
        self, brand_id: str, workspace_id: str, slot_date: str,
        slot_hour: int, job_id: str, platform: str = "facebook",
    ) -> AutomationJob:
        job = AutomationJob.for_scheduled_slot(
            brand_id=brand_id, workspace_id=workspace_id,
            slot_date=slot_date, slot_hour=slot_hour,
            job_id=job_id, platform=platform,
        )
        client = self._require_client()
        data = job.to_db_dict()
        try:
            result = client.table(self._table).insert(data).execute()
            if result.data:
                saved = AutomationJob(**result.data[0])
                logger.info(
                    "JobService: created scheduled job %s for brand=%s slot=%sT%02d:00",
                    job.job_id, brand_id, slot_date, slot_hour,
                )
                self._emit("job.created", {
                    "job_id": saved.job_id, "brand_id": brand_id,
                    "workflow_type": "scheduled", "platform": platform,
                })
                return saved
            raise DatabaseQueryError(message="Insert returned no data", context={"job_id": job_id})
        except Exception as e:
            error_str = str(e).lower()
            if "duplicate" in error_str or "unique" in error_str or "23505" in error_str:
                raise DuplicateJobError(
                    message=f"Slot already claimed: {job.idempotency_key}",
                    context={"idempotency_key": job.idempotency_key, "brand_id": brand_id},
                )
            raise DatabaseQueryError(message=f"Failed to create job: {job_id}", cause=e)

    def create_manual_job(
        self, brand_id: str, workspace_id: str, job_id: str, platform: str = "facebook",
    ) -> AutomationJob:
        job = AutomationJob.for_manual_trigger(
            brand_id=brand_id, workspace_id=workspace_id, job_id=job_id, platform=platform,
        )
        client = self._require_client()
        data = job.to_db_dict()
        try:
            result = client.table(self._table).insert(data).execute()
            if result.data:
                saved = AutomationJob(**result.data[0])
                self._emit("job.created", {
                    "job_id": saved.job_id, "brand_id": brand_id,
                    "workflow_type": "manual", "platform": platform,
                })
                return saved
            raise DatabaseQueryError(message="Insert returned no data")
        except Exception as e:
            raise DatabaseQueryError(message=f"Failed to create manual job: {job_id}", cause=e)

    # ═══════════════════════════════════════════════════════════════════
    # Job Retrieval
    # ═══════════════════════════════════════════════════════════════════

    def find_by_id(self, job_id: str) -> Optional[AutomationJob]:
        client = self._require_client()
        try:
            result = client.table(self._table).select("*").eq("job_id", job_id).execute()
            if result.data:
                return AutomationJob(**result.data[0])
            return None
        except Exception as e:
            raise DatabaseQueryError(message=f"Failed to find job: {job_id}", cause=e)

    def find_by_idempotency_key(self, idempotency_key: str) -> Optional[AutomationJob]:
        client = self._require_client()
        try:
            result = client.table(self._table).select("*").eq("idempotency_key", idempotency_key).limit(1).execute()
            if result.data:
                return AutomationJob(**result.data[0])
            return None
        except Exception as e:
            logger.error("Failed to find job by idempotency key: %s", e)
            return None

    def find_by_brand(
        self, brand_id: str, status: Optional[JobStatus] = None, limit: int = 50,
    ) -> List[AutomationJob]:
        client = self._require_client()
        try:
            query = client.table(self._table).select("*").eq("brand_id", brand_id)
            if status:
                query = query.eq("status", status.value)
            query = query.order("created_at", desc=True).limit(limit)
            result = query.execute()
            return [AutomationJob(**row) for row in (result.data or [])]
        except Exception as e:
            raise DatabaseQueryError(message=f"Failed to find jobs for brand: {brand_id}", cause=e)

    def find_active_jobs(self, brand_id: Optional[str] = None) -> List[AutomationJob]:
        client = self._require_client()
        try:
            active_statuses = [s.value for s in ACTIVE_STATES]
            query = client.table(self._table).select("*").in_("status", active_statuses)
            if brand_id:
                query = query.eq("brand_id", brand_id)
            result = query.execute()
            return [AutomationJob(**row) for row in (result.data or [])]
        except Exception as e:
            raise DatabaseQueryError(message="Failed to find active jobs", cause=e)

    def is_slot_claimed(self, idempotency_key: str) -> bool:
        client = self._require_client()
        try:
            result = client.table(self._table).select("id").eq("idempotency_key", idempotency_key).limit(1).execute()
            return bool(result.data)
        except Exception as e:
            logger.error("Database unavailable during slot check — failing closed: %s", e)
            return True

    # ═══════════════════════════════════════════════════════════════════
    # State Transitions
    # ═══════════════════════════════════════════════════════════════════

    def transition(self, job: AutomationJob, new_status: JobStatus) -> AutomationJob:
        old_status = job.status
        if not job.transition_to(new_status):
            raise ValueError(f"Invalid state transition: {old_status.value} → {new_status.value}")
        client = self._require_client()
        try:
            update_data = {
                "status": job.status.value,
                "updated_at": job.updated_at.isoformat(),
            }
            if job.started_at:
                update_data["started_at"] = job.started_at.isoformat()
            if job.completed_at:
                update_data["completed_at"] = job.completed_at.isoformat()
            if job.duration_ms is not None:
                update_data["duration_ms"] = job.duration_ms
            result = client.table(self._table).update(update_data).eq("id", job.id).execute()
            if result.data:
                updated = AutomationJob(**result.data[0])
                self._emit("job.status_changed", {
                    "job_id": updated.job_id, "old_status": old_status.value,
                    "new_status": new_status.value, "brand_id": updated.brand_id,
                })
                return updated
            raise DatabaseQueryError(message=f"Update returned no data for job {job.id}")
        except ValueError:
            raise
        except Exception as e:
            raise DatabaseQueryError(message=f"Failed to transition job {job.job_id}", cause=e)

    def reset_to_pending(self, job_id: str) -> Optional[AutomationJob]:
        client = self._require_client()
        try:
            result = client.table(self._table).select("*").eq("id", job_id).limit(1).execute()
            if not result.data:
                logger.warning("JobService: job %s not found for reset_to_pending", job_id)
                return None
            job = AutomationJob(**result.data[0])
            if job.status != JobStatus.RETRY_PENDING:
                logger.warning("JobService: job %s is in state %s, not RETRY_PENDING — skipping reset",
                               job_id, job.status.value)
                return None
            update_data = {
                "status": JobStatus.PENDING.value,
                "attempt_count": job.attempt_count + 1,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            update_result = client.table(self._table).update(update_data).eq("id", job_id).execute()
            if update_result.data:
                updated = AutomationJob(**update_result.data[0])
                logger.info("JobService: reset RETRY_PENDING → PENDING for job %s (attempt %d)",
                            job_id, updated.attempt_count)
                self._emit("job.retry_scheduled", {"job_id": updated.job_id, "attempt": updated.attempt_count})
                return updated
            return None
        except Exception as e:
            logger.error("Failed to reset job %s to pending: %s", job_id, e)
            return None

    def record_attempt(
        self, job: AutomationJob, error: Optional[str] = None, error_code: Optional[str] = None,
    ) -> AutomationJob:
        job.record_attempt(error=error, error_code=error_code)
        client = self._require_client()
        try:
            update_data = {
                "attempt_count": job.attempt_count, "last_error": job.last_error,
                "last_error_code": job.last_error_code, "updated_at": job.updated_at.isoformat(),
            }
            result = client.table(self._table).update(update_data).eq("id", job.id).execute()
            if result.data:
                return AutomationJob(**result.data[0])
            return job
        except Exception as e:
            logger.error("Failed to record attempt for job %s: %s", job.job_id, e)
            return job

    def mark_published(
        self, job: AutomationJob, post_id: str, result: Optional[dict] = None,
    ) -> AutomationJob:
        job.mark_success(post_id=post_id, result=result)
        client = self._require_client()
        try:
            update_data = {
                "status": JobStatus.PUBLISHED.value, "platform_post_id": post_id,
                "result": result, "completed_at": job.completed_at.isoformat(),
                "duration_ms": job.duration_ms, "updated_at": job.updated_at.isoformat(),
            }
            response = client.table(self._table).update(update_data).eq("id", job.id).execute()
            if response.data:
                saved = AutomationJob(**response.data[0])
                logger.info("JobService: PUBLISHED — job=%s, post_id=%s, duration=%dms",
                            job.job_id, post_id, job.duration_ms)
                self._emit("job.published", {
                    "job_id": saved.job_id, "brand_id": saved.brand_id,
                    "post_id": post_id, "duration_ms": job.duration_ms,
                })
                return saved
            return job
        except Exception as e:
            raise DatabaseQueryError(message=f"Failed to mark job as published: {job.job_id}", cause=e)

    def mark_failed(
        self, job: AutomationJob, error: str, error_code: Optional[str] = None, retryable: bool = False,
    ) -> AutomationJob:
        job.mark_failed(error=error, error_code=error_code, retryable=retryable)
        client = self._require_client()
        try:
            update_data = {
                "status": job.status.value, "last_error": job.last_error,
                "last_error_code": job.last_error_code,
                "completed_at": job.completed_at.isoformat() if job.completed_at else None,
                "duration_ms": job.duration_ms, "updated_at": job.updated_at.isoformat(),
            }
            response = client.table(self._table).update(update_data).eq("id", job.id).execute()
            if response.data:
                saved = AutomationJob(**response.data[0])
                logger.warning("JobService: %s — job=%s, error=%s", job.status.value, job.job_id, error[:100])
                self._emit("job.failed", {
                    "job_id": saved.job_id, "brand_id": saved.brand_id,
                    "status": saved.status.value, "error": error[:200], "retryable": retryable,
                })
                return saved
            return job
        except Exception as e:
            raise DatabaseQueryError(message=f"Failed to mark job as failed: {job.job_id}", cause=e)

    # ═══════════════════════════════════════════════════════════════════
    # Recovery Operations
    # ═══════════════════════════════════════════════════════════════════

    def detect_stale_jobs(self, stale_minutes: int = 15) -> List[AutomationJob]:
        client = self._require_client()
        try:
            result = client.rpc("detect_stale_jobs", {"stale_threshold_minutes": stale_minutes}).execute()
            if result.data:
                return [AutomationJob(**row) for row in result.data]
            return []
        except Exception as e:
            logger.error("Failed to detect stale jobs: %s", e)
            return self._detect_stale_jobs_fallback(stale_minutes)

    def _detect_stale_jobs_fallback(self, stale_minutes: int) -> List[AutomationJob]:
        client = self._require_client()
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)).isoformat()
        try:
            active_statuses = ["RUNNING", "GENERATING", "RENDERING", "PUBLISHING"]
            result = client.table(self._table).select("*").in_("status", active_statuses).lt("updated_at", cutoff).execute()
            return [AutomationJob(**row) for row in (result.data or [])]
        except Exception as e:
            logger.error("Fallback stale detection failed: %s", e)
            return []

    def recover_stale_jobs(self, stale_minutes: int = 15) -> int:
        stale_jobs = self.detect_stale_jobs(stale_minutes)
        recovered = 0
        for job in stale_jobs:
            try:
                self.transition(job, JobStatus.STALE)
                self._emit("job.stale_detected", {
                    "job_id": job.job_id, "brand_id": job.brand_id,
                    "previous_status": job.status.value, "stale_minutes": stale_minutes,
                })
                recovered += 1
            except Exception as e:
                logger.error("Failed to mark job %s as stale: %s", job.job_id, e)
        if recovered:
            logger.warning("Recovered %d stale jobs (threshold=%d min)", recovered, stale_minutes)
        return recovered

    def recover_and_retry(self, stale_minutes: int = 15) -> int:
        stale_jobs = self.detect_stale_jobs(stale_minutes)
        retried = 0
        for job in stale_jobs:
            try:
                updated = self.reset_to_pending(job.id)
                if updated:
                    self._emit("job.recovered_and_retried", {"job_id": job.job_id, "brand_id": job.brand_id})
                    retried += 1
                else:
                    self.transition(job, JobStatus.STALE)
            except Exception as e:
                logger.error("Failed to recover job %s: %s", job.job_id, e)
        if retried:
            logger.warning("Recovered and retried %d stale jobs", retried)
        return retried

    def get_retry_pending_jobs(self, limit: int = 10) -> List[AutomationJob]:
        client = self._require_client()
        try:
            result = client.table(self._table).select("*").eq("status", JobStatus.RETRY_PENDING.value) \
                .order("updated_at", ascending=True).limit(limit).execute()
            return [AutomationJob(**row) for row in (result.data or [])]
        except Exception as e:
            logger.error("Failed to get retry-pending jobs: %s", e)
            return []

    # ═══════════════════════════════════════════════════════════════════
    # v3.5 — Health & Metrics (WITH CACHE)
    # ═══════════════════════════════════════════════════════════════════

    def _get_cached_stats(self, brand_id: Optional[str] = None) -> Optional[dict]:
        """Return cached stats if still fresh."""
        with self._stats_lock:
            if self._stats_cache is not None:
                if time.monotonic() - self._stats_cache_time < self.STATS_CACHE_TTL_SECONDS:
                    return self._stats_cache
        return None

    def _set_cached_stats(self, stats: dict) -> None:
        """Store stats in cache."""
        with self._stats_lock:
            self._stats_cache = stats
            self._stats_cache_time = time.monotonic()

    def _invalidate_stats_cache(self) -> None:
        """Clear the stats cache (called after job mutations)."""
        with self._stats_lock:
            self._stats_cache = None

    def get_status_counts(self, brand_id: Optional[str] = None) -> dict:
        # v3.5 — check cache first
        cached = self._get_cached_stats(brand_id)
        if cached and "status_counts" in cached:
            return cached["status_counts"]

        client = self._require_client()
        try:
            query = client.table(self._table).select("status")
            if brand_id:
                query = query.eq("brand_id", brand_id)
            result = query.execute()
            counts: dict[str, int] = {}
            for row in result.data or []:
                status = row.get("status", "unknown")
                counts[status] = counts.get(status, 0) + 1
            return counts
        except Exception as e:
            logger.error("Failed to get status counts: %s", e)
            return {}

    def get_today_stats(self, brand_id: Optional[str] = None) -> dict:
        # v3.5 — check cache first
        cached = self._get_cached_stats(brand_id)
        if cached and "today" in cached:
            return cached["today"]

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        client = self._require_client()
        try:
            query = client.table(self._table).select("status").gte("created_at", f"{today}T00:00:00Z")
            if brand_id:
                query = query.eq("brand_id", brand_id)
            result = query.execute()
            stats = {"total": 0, "published": 0, "failed": 0, "retrying": 0, "active": 0}
            for row in result.data or []:
                stats["total"] += 1
                status = row.get("status", "")
                if status == "PUBLISHED":
                    stats["published"] += 1
                elif status == "FAILED":
                    stats["failed"] += 1
                elif status == "RETRY_PENDING":
                    stats["retrying"] += 1
                elif status in [s.value for s in ACTIVE_STATES]:
                    stats["active"] += 1
            return stats
        except Exception as e:
            logger.error("Failed to get today stats: %s", e)
            return {"total": 0, "published": 0, "failed": 0, "retrying": 0, "active": 0}

    def get_stats(self, brand_id: Optional[str] = None) -> dict:
        """Comprehensive stats for dashboards and monitoring.

        v3.5: Cached for 30 seconds to prevent query storms from
        MetaEngine health checks calling this every 60s per engine.
        """
        # v3.5 — return cached stats if fresh
        cached = self._get_cached_stats(brand_id)
        if cached:
            return cached

        # Build fresh stats
        today_stats = self.get_today_stats(brand_id)
        status_counts = self.get_status_counts(brand_id)
        active_jobs = self.find_active_jobs(brand_id)

        stats = {
            "today": today_stats,
            "status_counts": status_counts,
            "active_jobs": len(active_jobs),
            "oldest_active_minutes": (
                round(
                    (datetime.now(timezone.utc) - min(j.updated_at for j in active_jobs)).total_seconds() / 60, 1
                ) if active_jobs else 0
            ),
        }

        # v3.5 — cache for next call
        self._set_cached_stats(stats)
        return stats

    def health_check(self) -> dict:
        """Quick health check for MetaEngine (uses cache)."""
        stats = self.get_stats()
        status = "healthy"
        if stats.get("active_jobs", 0) > 100:
            status = "degraded"
        return {"status": status, "stats": stats}
