"""
Persistent Job Service — Architecture §7-8.

Replaces in-memory scheduler state (_fired_slots, _published_keys)
with database-backed job tracking that survives restarts and
works correctly across multiple workers.

v3.3 — Added find_by_idempotency_key() and reset_to_pending()
       for the scheduler retry fix (reuse RETRY_PENDING jobs).

Architecture §7 — Persistent Job Lifecycle:
  CREATED → QUEUED → RUNNING → GENERATING → VALIDATING →
  RENDERING → READY → PUBLISHING → PUBLISHED

Architecture §8 — Database-Level Idempotency:
  UNIQUE(idempotency_key) prevents duplicate execution.

Architecture §25 — Idempotency:
  ONE JOB → ONE PUBLISHING RESULT

Architecture §76 — Worker Recovery:
  detect_stale_jobs() finds jobs stuck in active states > 15 min.
"""

from __future__ import annotations

import logging
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

    def __init__(self, supabase_client=None) -> None:
        self._client = supabase_client
        self._table = "automation_jobs"

    def _get_client(self):
        if self._client:
            return self._client
        try:
            from services.supabase import supabase_service
            return supabase_service.service_client or supabase_service.client
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
                logger.info("JobService: created scheduled job %s for brand=%s slot=%sT%02d:00",
                            job.job_id, brand_id, slot_date, slot_hour)
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
                return AutomationJob(**result.data[0])
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
        """Find a job by its idempotency key.

        Used by the scheduler retry fix (v3.3) to check if a
        RETRY_PENDING job already exists for a given slot before
        attempting to create a new one.
        """
        client = self._require_client()
        try:
            result = client.table(self._table) \
                .select("*") \
                .eq("idempotency_key", idempotency_key) \
                .limit(1) \
                .execute()
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
            result = client.table(self._table) \
                .select("id").eq("idempotency_key", idempotency_key).limit(1).execute()
            return bool(result.data)
        except Exception as e:
            logger.error("Database unavailable during slot check — failing closed: %s", e)
            return True

    # ═══════════════════════════════════════════════════════════════════
    # State Transitions
    # ═══════════════════════════════════════════════════════════════════

    def transition(self, job: AutomationJob, new_status: JobStatus) -> AutomationJob:
        if not job.transition_to(new_status):
            raise ValueError(f"Invalid state transition: {job.status.value} → {new_status.value}")
        client = self._require_client()
        try:
            update_data = {"status": job.status.value, "updated_at": job.updated_at.isoformat()}
            if job.started_at:
                update_data["started_at"] = job.started_at.isoformat()
            if job.completed_at:
                update_data["completed_at"] = job.completed_at.isoformat()
            if job.duration_ms is not None:
                update_data["duration_ms"] = job.duration_ms
            result = client.table(self._table).update(update_data).eq("id", job.id).execute()
            if result.data:
                return AutomationJob(**result.data[0])
            raise DatabaseQueryError(message=f"Update returned no data for job {job.id}")
        except ValueError:
            raise
        except Exception as e:
            raise DatabaseQueryError(message=f"Failed to transition job {job.job_id}", cause=e)

    def reset_to_pending(self, job_id: str) -> Optional[AutomationJob]:
        """Reset a RETRY_PENDING job back to PENDING for retry.

        ARCHITECTURE FIX (v3.3):
        Instead of creating a new job (which hits DuplicateJobError
        on the UNIQUE idempotency_key), reuse the existing job by
        resetting its status. The idempotency key stays the same,
        the attempt count increments, and the slot gets retried.
        """
        client = self._require_client()
        try:
            # Fetch current job
            result = client.table(self._table).select("*").eq("id", job_id).limit(1).execute()
            if not result.data:
                logger.warning("JobService: job %s not found for reset_to_pending", job_id)
                return None

            job = AutomationJob(**result.data[0])

            # Only reset if in RETRY_PENDING state
            if job.status != JobStatus.RETRY_PENDING:
                logger.warning(
                    "JobService: job %s is in state %s, not RETRY_PENDING — skipping reset",
                    job_id, job.status.value,
                )
                return None

            # Increment attempt and reset status
            update_data = {
                "status": JobStatus.PENDING.value,
                "attempt_count": job.attempt_count + 1,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }

            update_result = client.table(self._table).update(update_data).eq("id", job_id).execute()
            if update_result.data:
                updated = AutomationJob(**update_result.data[0])
                logger.info(
                    "JobService: reset RETRY_PENDING → PENDING for job %s (attempt %d)",
                    job_id, updated.attempt_count,
                )
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
                logger.info("JobService: PUBLISHED — job=%s, post_id=%s, duration=%dms",
                            job.job_id, post_id, job.duration_ms)
                return AutomationJob(**response.data[0])
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
                logger.warning("JobService: %s — job=%s, error=%s", job.status.value, job.job_id, error[:100])
                return AutomationJob(**response.data[0])
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
                recovered += 1
            except Exception as e:
                logger.error("Failed to mark job %s as stale: %s", job.job_id, e)
        return recovered

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
    # Health & Metrics
    # ═══════════════════════════════════════════════════════════════════

    def get_status_counts(self, brand_id: Optional[str] = None) -> dict:
        client = self._require_client()
        try:
            query = client.table(self._table).select("status")
            if brand_id:
                query = query.eq("brand_id", brand_id)
            result = query.execute()
            counts: dict[str, int] = {}
            for row in (result.data or []):
                status = row.get("status", "unknown")
                counts[status] = counts.get(status, 0) + 1
            return counts
        except Exception as e:
            logger.error("Failed to get status counts: %s", e)
            return {}

    def get_today_stats(self, brand_id: Optional[str] = None) -> dict:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        client = self._require_client()
        try:
            query = client.table(self._table).select("status").gte("created_at", f"{today}T00:00:00Z")
            if brand_id:
                query = query.eq("brand_id", brand_id)
            result = query.execute()
            stats = {"total": 0, "published": 0, "failed": 0, "retrying": 0, "active": 0}
            for row in (result.data or []):
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
