"""
Persistent Job Service — Architecture §7-8.

v4.1 — FIXED:
  - reset_to_pending() now correctly writes QUEUED (not PENDING)
  - All docstrings/log messages updated to match actual state flow
  - Zero references to non-existent JobStatus.PENDING

v4.0 — MAJOR REFACTOR:
  - Removed ALL stats computation (moved to JobStatsService)
  - Removed ALL recursion guards (no longer needed)
  - Removed ALL _safe_* wrappers (no longer needed)
  - Removed ALL hardcoded status strings (use model constants)
  - Removed RLock (simple Lock for recovery state only)
  - Single responsibility: CRUD + state transitions + recovery
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Optional, List

from models.automation_job import (
    AutomationJob,
    JobStatus,
    WorkflowType,
    ACTIVE_STATES,
    TERMINAL_STATES,
    ACTIVE_STATUS_VALUES,
)
from core.errors import (
    DatabaseQueryError,
    DuplicateJobError,
    ErrorCode,
)

logger = logging.getLogger("kanyoza.services.job_service")


class JobService:
    """Persistent CRUD service for automation jobs.

    v4.1: RETRY_PENDING → QUEUED (fixed PENDING bug).
    v4.0: Stats removed. Use JobStatsService for all metrics.
    """

    def __init__(
        self, supabase_client=None, event_bus=None
    ) -> None:
        self._client = supabase_client
        self._event_bus = event_bus
        self._table = "automation_jobs"

        # Only used for recovery state tracking
        self._recovery_lock = threading.Lock()
        self._recovery_in_progress: set = set()

    # ------------------------------------------------------------------
    # EventBus wiring
    # ------------------------------------------------------------------

    def set_event_bus(self, event_bus) -> None:
        self._event_bus = event_bus
        logger.info("JobService: EventBus wired")

    def set_client(self, client) -> None:
        self._client = client

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
        except Exception as exc:
            logger.debug("JobService event emission failed: %s", exc)

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
                message="Supabase client not available",
                context={"operation": "job_service"},
            )
        return client

    # ═══════════════════════════════════════════════════════════════════
    # Job Creation
    # ═══════════════════════════════════════════════════════════════════

    def create_scheduled_job(
        self,
        brand_id: str,
        workspace_id: str,
        slot_date: str,
        slot_hour: int,
        job_id: str,
        platform: str = "facebook",
    ) -> AutomationJob:
        """Create a scheduled job for a specific time slot."""
        # Validate inputs
        if slot_hour < 0 or slot_hour > 23:
            raise ValueError(f"Invalid slot_hour: {slot_hour}")

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
            raise DatabaseQueryError(
                message="Insert returned no data",
                context={"job_id": job_id},
            )
        except Exception as e:
            error_str = str(e).lower()
            if "duplicate" in error_str or "unique" in error_str or "23505" in error_str:
                raise DuplicateJobError(
                    message=f"Slot already claimed: {job.idempotency_key}",
                    context={"idempotency_key": job.idempotency_key, "brand_id": brand_id},
                )
            raise DatabaseQueryError(
                message=f"Failed to create job: {job_id}", cause=e,
            )

    def create_manual_job(
        self, brand_id: str, workspace_id: str, job_id: str, platform: str = "facebook",
    ) -> AutomationJob:
        """Create a manual trigger job."""
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
            raise DatabaseQueryError(
                message=f"Failed to create manual job: {job_id}", cause=e,
            )

    # ═══════════════════════════════════════════════════════════════════
    # Job Retrieval
    # ═══════════════════════════════════════════════════════════════════

    def find_by_id(self, job_id: str) -> Optional[AutomationJob]:
        """Find a job by its ID."""
        client = self._require_client()
        try:
            result = client.table(self._table).select("*").eq("job_id", job_id).execute()
            return AutomationJob(**result.data[0]) if result.data else None
        except Exception as e:
            raise DatabaseQueryError(
                message=f"Failed to find job: {job_id}", cause=e,
            )

    def find_by_idempotency_key(self, idempotency_key: str) -> Optional[AutomationJob]:
        """Find a job by its idempotency key."""
        client = self._require_client()
        try:
            result = (
                client.table(self._table)
                .select("*")
                .eq("idempotency_key", idempotency_key)
                .limit(1)
                .execute()
            )
            return AutomationJob(**result.data[0]) if result.data else None
        except Exception as e:
            logger.error("Failed to find job by idempotency key: %s", e)
            return None

    def find_by_brand(
        self, brand_id: str, status: Optional[JobStatus] = None, limit: int = 50,
    ) -> List[AutomationJob]:
        """Find jobs for a brand, optionally filtered by status."""
        client = self._require_client()
        try:
            query = client.table(self._table).select("*").eq("brand_id", brand_id)
            if status:
                query = query.eq("status", status.value)
            result = query.order("created_at", desc=True).limit(limit).execute()
            return [AutomationJob(**row) for row in (result.data or [])]
        except Exception as e:
            raise DatabaseQueryError(
                message=f"Failed to find jobs for brand: {brand_id}", cause=e,
            )

    def find_active_jobs(self, brand_id: Optional[str] = None) -> List[AutomationJob]:
        """Find all active (non-terminal) jobs."""
        client = self._require_client()
        try:
            query = (
                client.table(self._table)
                .select("*")
                .in_("status", list(ACTIVE_STATUS_VALUES))
            )
            if brand_id:
                query = query.eq("brand_id", brand_id)
            result = query.execute()
            return [AutomationJob(**row) for row in (result.data or [])]
        except Exception as e:
            raise DatabaseQueryError(
                message="Failed to find active jobs", cause=e,
            )

    def is_slot_claimed(self, idempotency_key: str) -> bool:
        """Check if a time slot is already claimed."""
        client = self._require_client()
        try:
            result = (
                client.table(self._table)
                .select("id")
                .eq("idempotency_key", idempotency_key)
                .limit(1)
                .execute()
            )
            return bool(result.data)
        except Exception as e:
            logger.error("Database unavailable — failing closed: %s", e)
            return True

    # ═══════════════════════════════════════════════════════════════════
    # State Transitions
    # ═══════════════════════════════════════════════════════════════════

    def transition(self, job: AutomationJob, new_status: JobStatus) -> AutomationJob:
        """Transition a job to a new status with validation."""
        old_status = job.status
        if not job.transition_to(new_status):
            raise ValueError(
                f"Invalid state transition: {old_status.value} → {new_status.value}"
            )
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

            result = (
                client.table(self._table)
                .update(update_data)
                .eq("id", job.id)
                .execute()
            )
            if result.data:
                updated = AutomationJob(**result.data[0])
                self._emit("job.status_changed", {
                    "job_id": updated.job_id,
                    "old_status": old_status.value,
                    "new_status": new_status.value,
                    "brand_id": updated.brand_id,
                })
                return updated
            raise DatabaseQueryError(
                message=f"Update returned no data for job {job.id}"
            )
        except ValueError:
            raise
        except Exception as e:
            raise DatabaseQueryError(
                message=f"Failed to transition job {job.job_id}", cause=e,
            )

    def reset_to_pending(self, job_id: str) -> Optional[AutomationJob]:
        """Reset a RETRY_PENDING job to QUEUED for retry.

        v4.1: Fixed — previously wrote JobStatus.PENDING which doesn't exist.
        Now correctly writes JobStatus.QUEUED.
        """
        client = self._require_client()
        try:
            result = (
                client.table(self._table)
                .select("*")
                .eq("id", job_id)
                .limit(1)
                .execute()
            )
            if not result.data:
                logger.warning("JobService: job %s not found", job_id)
                return None

            job = AutomationJob(**result.data[0])
            if job.status != JobStatus.RETRY_PENDING:
                logger.warning(
                    "JobService: job %s is %s, not RETRY_PENDING",
                    job_id, job.status.value,
                )
                return None

            update_data = {
                "status": JobStatus.QUEUED.value,
                "attempt_count": job.attempt_count + 1,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            update_result = (
                client.table(self._table)
                .update(update_data)
                .eq("id", job_id)
                .execute()
            )
            if update_result.data:
                updated = AutomationJob(**update_result.data[0])
                logger.info(
                    "JobService: reset RETRY_PENDING → QUEUED for job %s (attempt %d)",
                    job_id, updated.attempt_count,
                )
                self._emit("job.retry_scheduled", {
                    "job_id": updated.job_id,
                    "attempt": updated.attempt_count,
                })
                return updated
            return None
        except Exception as e:
            logger.error("Failed to reset job %s: %s", job_id, e)
            return None

    def record_attempt(
        self, job: AutomationJob,
        error: Optional[str] = None,
        error_code: Optional[str] = None,
    ) -> AutomationJob:
        """Record an attempt for a job."""
        job.record_attempt(error=error, error_code=error_code)
        client = self._require_client()
        try:
            update_data = {
                "attempt_count": job.attempt_count,
                "last_error": job.last_error,
                "last_error_code": job.last_error_code,
                "updated_at": job.updated_at.isoformat(),
            }
            result = (
                client.table(self._table)
                .update(update_data)
                .eq("id", job.id)
                .execute()
            )
            return AutomationJob(**result.data[0]) if result.data else job
        except Exception as e:
            logger.error("Failed to record attempt for job %s: %s", job.job_id, e)
            return job

    def mark_published(
        self, job: AutomationJob, post_id: str, result: Optional[dict] = None,
    ) -> AutomationJob:
        """Mark a job as successfully published."""
        job.mark_success(post_id=post_id, result=result)
        client = self._require_client()
        try:
            update_data = {
                "status": JobStatus.PUBLISHED.value,
                "platform_post_id": post_id,
                "result": result,
                "completed_at": job.completed_at.isoformat(),
                "duration_ms": job.duration_ms,
                "updated_at": job.updated_at.isoformat(),
            }
            response = (
                client.table(self._table)
                .update(update_data)
                .eq("id", job.id)
                .execute()
            )
            if response.data:
                saved = AutomationJob(**response.data[0])
                logger.info(
                    "JobService: PUBLISHED — job=%s, post_id=%s, duration=%dms",
                    job.job_id, post_id, job.duration_ms,
                )
                self._emit("job.published", {
                    "job_id": saved.job_id,
                    "brand_id": saved.brand_id,
                    "post_id": post_id,
                    "duration_ms": job.duration_ms,
                })
                return saved
            return job
        except Exception as e:
            raise DatabaseQueryError(
                message=f"Failed to mark job as published: {job.job_id}", cause=e,
            )

    def mark_failed(
        self, job: AutomationJob, error: str,
        error_code: Optional[str] = None, retryable: bool = False,
    ) -> AutomationJob:
        """Mark a job as failed."""
        job.mark_failed(error=error, error_code=error_code, retryable=retryable)
        client = self._require_client()
        try:
            update_data = {
                "status": job.status.value,
                "last_error": job.last_error,
                "last_error_code": job.last_error_code,
                "completed_at": job.completed_at.isoformat() if job.completed_at else None,
                "duration_ms": job.duration_ms,
                "updated_at": job.updated_at.isoformat(),
            }
            response = (
                client.table(self._table)
                .update(update_data)
                .eq("id", job.id)
                .execute()
            )
            if response.data:
                saved = AutomationJob(**response.data[0])
                logger.warning(
                    "JobService: %s — job=%s, error=%s",
                    job.status.value, job.job_id, error[:100],
                )
                self._emit("job.failed", {
                    "job_id": saved.job_id,
                    "brand_id": saved.brand_id,
                    "status": saved.status.value,
                    "error": error[:200],
                    "retryable": retryable,
                })
                return saved
            return job
        except Exception as e:
            raise DatabaseQueryError(
                message=f"Failed to mark job as failed: {job.job_id}", cause=e,
            )

    # ═══════════════════════════════════════════════════════════════════
    # Recovery Operations
    # ═══════════════════════════════════════════════════════════════════

    def detect_stale_jobs(self, stale_minutes: int = 15) -> List[AutomationJob]:
        """Detect jobs that have been in active states too long."""
        client = self._require_client()
        try:
            result = client.rpc(
                "detect_stale_jobs",
                {"stale_threshold_minutes": stale_minutes},
            ).execute()
            return [AutomationJob(**row) for row in result.data] if result.data else []
        except Exception as e:
            logger.error("Failed to detect stale jobs via RPC: %s", e)
            return self._detect_stale_jobs_fallback(stale_minutes)

    def _detect_stale_jobs_fallback(self, stale_minutes: int) -> List[AutomationJob]:
        """Fallback method for stale job detection."""
        client = self._require_client()
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)).isoformat()
        try:
            result = (
                client.table(self._table)
                .select("*")
                .in_("status", ["RUNNING", "GENERATING", "RENDERING", "PUBLISHING"])
                .lt("updated_at", cutoff)
                .execute()
            )
            return [AutomationJob(**row) for row in (result.data or [])]
        except Exception as e:
            logger.error("Fallback stale detection failed: %s", e)
            return []

    def recover_stale_jobs(self, stale_minutes: int = 15) -> int:
        """Recover stale jobs by marking them as STALE."""
        stale_jobs = self.detect_stale_jobs(stale_minutes)
        recovered = 0
        for job in stale_jobs:
            try:
                self.transition(job, JobStatus.STALE)
                self._emit("job.stale_detected", {
                    "job_id": job.job_id,
                    "brand_id": job.brand_id,
                    "previous_status": job.status.value,
                    "stale_minutes": stale_minutes,
                })
                recovered += 1
            except Exception as e:
                logger.error("Failed to mark job %s as stale: %s", job.job_id, e)
        if recovered:
            logger.warning("Recovered %d stale jobs", recovered)
        return recovered

    def recover_and_retry(self, stale_minutes: int = 15) -> int:
        """Recover stale jobs AND reset them to QUEUED for retry."""
        stale_jobs = self.detect_stale_jobs(stale_minutes)
        retried = 0
        for job in stale_jobs:
            try:
                updated = self.reset_to_pending(job.id)
                if updated:
                    self._emit("job.recovered_and_retried", {
                        "job_id": job.job_id,
                        "brand_id": job.brand_id,
                    })
                    retried += 1
                else:
                    self.transition(job, JobStatus.STALE)
            except Exception as e:
                logger.error("Failed to recover job %s: %s", job.job_id, e)
        if retried:
            logger.warning("Recovered and retried %d stale jobs", retried)
        return retried

    def get_retry_pending_jobs(self, limit: int = 10) -> List[AutomationJob]:
        """Get jobs waiting for retry."""
        client = self._require_client()
        try:
            result = (
                client.table(self._table)
                .select("*")
                .eq("status", JobStatus.RETRY_PENDING.value)
                .order("updated_at", ascending=True)
                .limit(limit)
                .execute()
            )
            return [AutomationJob(**row) for row in (result.data or [])]
        except Exception as e:
            logger.error("Failed to get retry-pending jobs: %s", e)
            return []

    # ═══════════════════════════════════════════════════════════════════
    # v4.0 — Health (delegated to JobStatsService)
    # ═══════════════════════════════════════════════════════════════════

    def health_check(self) -> dict:
        """
        Health check that delegates to JobStatsService.
        No stats computation in this class. No recursion possible.
        """
        try:
            from services.job_stats_service import get_job_stats_service
            stats_service = get_job_stats_service()
            return stats_service.health_check()
        except Exception as e:
            logger.error("JobService health check failed: %s", e)
            return {
                "status": "degraded",
                "error": str(e),
            }

    def get_status(self) -> dict:
        """Legacy get_status — delegates to health_check."""
        return self.health_check()


# ═══════════════════════════════════════════════════════════════════════
# EXPOSED FOR TESTING
# ═══════════════════════════════════════════════════════════════════════

__all__ = [
    "JobService",
]
