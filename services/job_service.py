"""
Persistent Job Service — Architecture §7-8.

v5.0.1 — ASYNC-NATIVE + HEALTH CHECK FIX:
  - asyncio.Lock replaces threading.Lock
  - All methods async where they touch the lock
  - UnifiedEventBus integration
  - health_check now returns "unknown" (not "degraded") on error
  - All v4.1 features preserved
"""

from __future__ import annotations

import asyncio
import logging
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
    """Persistent CRUD service for automation jobs — ASYNC-NATIVE.

    v5.0.1: health_check returns "unknown" on error.
    v5.0: asyncio.Lock, async methods, UnifiedEventBus.
    v4.1: RETRY_PENDING → QUEUED (fixed PENDING bug).
    """

    def __init__(self, supabase_client=None, event_bus=None) -> None:
        self._client = supabase_client
        self._event_bus = event_bus
        self._table = "automation_jobs"

        # ✅ Async lock for recovery state
        self._recovery_lock = asyncio.Lock()
        self._recovery_in_progress: set = set()

    # ------------------------------------------------------------------
    # EventBus wiring
    # ------------------------------------------------------------------

    def set_event_bus(self, event_bus) -> None:
        self._event_bus = event_bus
        logger.info("JobService: EventBus wired")

    def set_client(self, client) -> None:
        self._client = client

    async def _emit(self, event_name: str, payload: dict) -> None:
        if not self._event_bus:
            return
        try:
            if hasattr(self._event_bus, "publish"):
                result = self._event_bus.publish(event_name, payload)
                if asyncio.iscoroutine(result):
                    await result
            elif hasattr(self._event_bus, "publish_event"):
                self._event_bus.publish_event(event_name, payload)
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
            return supabase_service.service_client or supabase_service.client
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
    # Job Creation (ASYNC)
    # ═══════════════════════════════════════════════════════════════════

    async def create_scheduled_job(
        self, brand_id: str, workspace_id: str,
        slot_date: str, slot_hour: int, job_id: str,
        platform: str = "facebook",
    ) -> AutomationJob:
        """Create a scheduled job for a specific time slot (async)."""
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
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, lambda: client.table(self._table).insert(data).execute()
            )
            if result.data:
                saved = AutomationJob(**result.data[0])
                await self._emit("job.created", {
                    "job_id": saved.job_id, "brand_id": brand_id,
                    "workspace_id": saved.workspace_id,
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

    async def create_manual_job(
        self,
        brand_id: str,
        workspace_id: str,
        job_id: str,
        platform: str = "facebook",
        *,
        topic: str | None = None,
        decision_id: str | None = None,
        caption: str | None = None,
        result: dict | None = None,
        status: "JobStatus | None" = None,
    ) -> AutomationJob:
        """Create a manual trigger job (async).

        Optional topic/decision_id/caption are stored on the canonical
        AutomationJob shape (no obsolete payload/job_type fields).
        """
        from models.automation_job import JobStatus as _JS
        job = AutomationJob.for_manual_trigger(
            brand_id=brand_id, workspace_id=workspace_id, job_id=job_id, platform=platform,
        )
        if topic is not None:
            job.topic = topic
        if decision_id is not None:
            job.decision_id = decision_id
        extra = dict(result or {})
        if caption is not None:
            extra.setdefault("caption", caption)
        if extra:
            job.result = extra
        if status is not None:
            job.status = status
        else:
            job.status = _JS.QUEUED
        client = self._require_client()
        data = job.to_db_dict()
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, lambda: client.table(self._table).insert(data).execute()
            )
            if result.data:
                saved = AutomationJob(**result.data[0])
                await self._emit("job.created", {
                    "job_id": saved.job_id, "brand_id": brand_id,
                    "workspace_id": saved.workspace_id,
                    "workflow_type": "manual", "platform": platform,
                })
                return saved
            raise DatabaseQueryError(message="Insert returned no data")
        except Exception as e:
            raise DatabaseQueryError(message=f"Failed to create manual job: {job_id}", cause=e)

    # ═══════════════════════════════════════════════════════════════════
    # Job Retrieval (ASYNC)
    # ═══════════════════════════════════════════════════════════════════

    async def find_by_id(self, job_id: str) -> Optional[AutomationJob]:
        client = self._require_client()
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, lambda: client.table(self._table).select("*").eq("job_id", job_id).execute()
            )
            return AutomationJob(**result.data[0]) if result.data else None
        except Exception as e:
            raise DatabaseQueryError(message=f"Failed to find job: {job_id}", cause=e)

    async def find_by_idempotency_key(self, idempotency_key: str) -> Optional[AutomationJob]:
        client = self._require_client()
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: client.table(self._table)
                .select("*")
                .eq("idempotency_key", idempotency_key)
                .limit(1)
                .execute(),
            )
            return AutomationJob(**result.data[0]) if result.data else None
        except Exception as e:
            logger.error("Failed to find job by idempotency key: %s", e)
            return None

    async def find_by_brand(
        self,
        brand_id: str,
        status: Optional[JobStatus] = None,
        limit: int = 50,
        *,
        oldest_first: bool = False,
    ) -> List[AutomationJob]:
        """Find brand jobs, optionally prioritizing the oldest backlog entries."""
        client = self._require_client()
        try:
            loop = asyncio.get_event_loop()
            query = client.table(self._table).select("*").eq("brand_id", brand_id)
            if status:
                query = query.eq("status", status.value)
            result = await loop.run_in_executor(
                None,
                lambda: query.order(
                    "created_at", desc=not oldest_first
                ).limit(limit).execute(),
            )
            return [AutomationJob(**row) for row in (result.data or [])]
        except Exception as e:
            raise DatabaseQueryError(message=f"Failed to find jobs for brand: {brand_id}", cause=e)

    async def is_slot_claimed(self, idempotency_key: str) -> bool:
        client = self._require_client()
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: client.table(self._table)
                .select("id")
                .eq("idempotency_key", idempotency_key)
                .limit(1)
                .execute(),
            )
            return bool(result.data)
        except Exception as e:
            logger.error("Database unavailable — failing closed: %s", e)
            return True

    # ═══════════════════════════════════════════════════════════════════
    # State Transitions (ASYNC)
    # ═══════════════════════════════════════════════════════════════════

    async def transition(self, job: AutomationJob, new_status: JobStatus) -> AutomationJob:
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

            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, lambda: client.table(self._table).update(update_data).eq("id", job.id).execute()
            )
            if result.data:
                updated = AutomationJob(**result.data[0])
                await self._emit("job.status_changed", {
                    "job_id": updated.job_id,
                    "workspace_id": updated.workspace_id,
                    "old_status": old_status.value,
                    "new_status": new_status.value,
                    "brand_id": updated.brand_id,
                })
                return updated
            raise DatabaseQueryError(message=f"Update returned no data for job {job.id}")
        except ValueError:
            raise
        except Exception as e:
            raise DatabaseQueryError(message=f"Failed to transition job {job.job_id}", cause=e)

    # ═══════════════════════════════════════════════════════════════════
    # Recovery (ASYNC — uses asyncio.Lock)
    # ═══════════════════════════════════════════════════════════════════

    async def recover_stale_jobs(self, stale_minutes: int = 15) -> int:
        """Recover stale jobs with async lock."""
        async with self._recovery_lock:
            if "global" in self._recovery_in_progress:
                return 0
            self._recovery_in_progress.add("global")
            try:
                stale_jobs = await self.detect_stale_jobs(stale_minutes)
                recovered = 0
                for job in stale_jobs:
                    try:
                        await self.transition(job, JobStatus.STALE)
                        recovered += 1
                    except Exception:
                        pass
                return recovered
            finally:
                self._recovery_in_progress.discard("global")

    async def detect_stale_jobs(self, stale_minutes: int = 15) -> List[AutomationJob]:
        client = self._require_client()
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)).isoformat()
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: client.table(self._table)
                .select("*")
                .in_("status", ["RUNNING", "GENERATING", "RENDERING", "PUBLISHING"])
                .lt("updated_at", cutoff)
                .execute(),
            )
            return [AutomationJob(**row) for row in (result.data or [])]
        except Exception as e:
            logger.error("Fallback stale detection failed: %s", e)
            return []

    # ═══════════════════════════════════════════════════════════════════
    # Health (v5.0.1 FIX: unknown instead of degraded)
    # ═══════════════════════════════════════════════════════════════════

    def health_check(self) -> dict:
        try:
            from services.job_stats_service import get_job_stats_service
            stats_service = get_job_stats_service()
            result = stats_service.health_check()
            # v5.0.1: Propagate unknown/not_configured so MetaEngine investigates
            if result.get("status") in ("not_configured", "unknown"):
                result["status"] = "unknown"
            from core.director_signal_contract import director_signal_metrics
            result = dict(result)
            metrics = dict(result.get("metrics") or {}) if isinstance(result.get("metrics"), dict) else {}
            signals = self._director_backlog_signals(result)
            metrics.update(director_signal_metrics(signals))
            result["metrics"] = metrics
            return result
        except Exception as e:
            logger.error("JobService health check failed: %s", e)
            from core.director_signal_contract import director_signal_metrics
            return {"status": "unknown", "error": str(e)[:200], "metrics": director_signal_metrics()}

    @staticmethod
    def _director_backlog_signals(health_result: dict) -> list[dict]:
        """Publish a conservative advisory only from explicit, healthy stats.

        The Director's capability/sector and governance checks remain authoritative.
        Degraded, unhealthy, unknown, missing, or malformed evidence produces no signal.
        """
        if not isinstance(health_result, dict):
            return []
        status = str(health_result.get("status", "unknown")).strip().lower()
        if status not in {"healthy", "nominal", "ok", "ready"}:
            return []
        stats = health_result.get("stats")
        if not isinstance(stats, dict):
            return []
        status_counts = stats.get("status_counts")
        if not isinstance(status_counts, dict):
            return []

        def _count(key: str) -> int | None:
            value = status_counts.get(key, 0)
            if isinstance(value, bool):
                return None
            try:
                parsed = int(value)
            except (TypeError, ValueError, OverflowError):
                return None
            if parsed < 0 or str(parsed) != str(value).strip():
                # Permit ordinary integer values, but not lossy floats or malformed text.
                if not isinstance(value, int) or parsed < 0:
                    return None
            return parsed

        failed = _count("FAILED")
        stale = _count("STALE")
        if failed is None or stale is None or failed + stale <= 0:
            return []
        return [{
            "capability": "optimize_infrastructure",
            "description": f"Review job backlog: {failed} failed and {stale} stale jobs remain below the service alert threshold",
            "params": {"component": "job_service"},
            "confidence": 0.72,
            "impact_score": min(60, 20 + failed * 2 + stale * 3),
            "mission_priority": 45,
            "rationale": f"JobStatsService reported healthy status with explicit status_counts: FAILED={failed}, STALE={stale}. This is an advisory review, not an automatic retry or job mutation.",
        }]

    def get_status(self) -> dict:
        return self.health_check()

    async def reset_to_pending(self, job_id: str) -> Optional[AutomationJob]:
        job = await self.find_by_id(job_id)
        if not job:
            return None
        if job.status == JobStatus.RETRY_PENDING:
            return await self.transition(job, JobStatus.QUEUED)
        return job

    async def mark_published(self, job: AutomationJob, *, post_id: Optional[str] = None, result: Optional[dict] = None) -> AutomationJob:
        """Persist PUBLISHED only when the provider returned a concrete post ID."""
        post_id = str(post_id or "").strip()
        if not post_id:
            raise ValueError(
                "Cannot mark job PUBLISHED without a confirmed provider post ID"
            )
        current = job
        if current.status == JobStatus.QUEUED:
            current = await self.transition(current, JobStatus.RUNNING)
        if current.status in {JobStatus.RUNNING, JobStatus.GENERATING, JobStatus.VALIDATING, JobStatus.RENDERING, JobStatus.READY}:
            current = await self.transition(current, JobStatus.PUBLISHING)
        if current.status == JobStatus.PUBLISHING:
            current.platform_post_id = post_id
            if result:
                current.result = result
            return await self.transition(current, JobStatus.PUBLISHED)
        if current.status == JobStatus.PUBLISHED:
            return current
        raise ValueError(f"Cannot mark job {job.job_id} published from {current.status.value}")

    async def mark_failed(self, job: AutomationJob, *, error: str, error_code: Optional[str] = None, retryable: bool = False) -> AutomationJob:
        if job.status in TERMINAL_STATES and job.status != JobStatus.FAILED:
            raise ValueError(f"Cannot fail terminal job {job.job_id} from {job.status.value}")
        target = JobStatus.RETRY_PENDING if retryable and job.attempt_count + 1 < job.max_attempts else JobStatus.FAILED
        if not job.mark_failed(error=error, error_code=error_code, retryable=retryable):
            raise ValueError(f"Cannot fail job {job.job_id} from {job.status.value}")
        return await self._persist_job(job)

    async def _persist_job(self, job: AutomationJob) -> AutomationJob:
        client = self._require_client()
        data = job.to_db_dict()
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lambda: client.table(self._table).update(data).eq("id", job.id).execute())
        if result.data:
            return AutomationJob(**result.data[0])
        raise DatabaseQueryError(message=f"Failed to persist job {job.job_id}")

    async def get_today_stats(self, brand_id: Optional[str] = None) -> dict:
        from services.job_stats_service import get_job_stats_service
        return get_job_stats_service().get_today_stats(brand_id=brand_id)

    async def recover_and_retry(self, stale_minutes: int = 15) -> int:
        return await self.recover_stale_jobs(stale_minutes=stale_minutes)



__all__ = ["JobService"]
