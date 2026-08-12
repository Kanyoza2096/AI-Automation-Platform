"""
Brand-aware scheduling engine — UNIFIED v3.8
Replaces PostScheduler with a multi-tenant BrandSchedulerManager.

v3.8 FIXED:
  - Dashboard event loop now works correctly in background threads
  - _emit_dashboard_event() uses provided loop without calling get_running_loop()
  - Prevents 100% event loss from scheduler threads
  - Two-step atomic claim for scheduled posts (prevents race condition)

v3.7 UPGRADES:
  - Dashboard events now use stored event_loop (fixes systematic event loss
    from scheduler threads — audit finding #7)
  - set_event_loop() on BrandSchedulerManager propagates to all schedulers
  - _emit_dashboard_event accepts optional loop parameter

v3.6 UPGRADES:
  - ATOMIC scheduled-post claim via conditional UPDATE (fixes race condition
    where two workers could claim the same queued post)
  - Optional Supabase RPC for PostgreSQL-level atomicity (FOR UPDATE SKIP LOCKED)
  - recover_and_retry() fallback in _recover_all_stale_jobs()
  - EventBus integration (workflow events for MetaEngine/Director)
  - Proper Director event callback usage
  - Manager-level stale job recovery (not per-brand)
  - Health check & statistics
  - _stop_event + _sleep() for instant shutdown
"""

import asyncio
import time
import uuid
import logging
import threading
from datetime import datetime, timezone, date, timedelta
from typing import Dict, List, Optional, TYPE_CHECKING

from models.brand import Brand
from models.ai_profile import AIProfile
from models.automation_job import (
    AutomationJob,
    JobStatus,
    WorkflowType,
)
from repositories.brand_repository import BrandRepository
from repositories.ai_profile_repository import (
    AIProfileRepository,
)

if TYPE_CHECKING:
    from service_container import ServiceContainer
    from services.job_service import JobService

logger = logging.getLogger("kanyoza.scheduler.brand")

_failed_emits: int = 0
_failed_emits_lock = threading.Lock()


# ═══════════════════════════════════════════════════════════════════════════
# DASHBOARD EVENT EMITTER — v3.8 FIXED
# ═══════════════════════════════════════════════════════════════════════════

def _emit_dashboard_event(
    event_name: str,
    payload: dict,
    loop: Optional[asyncio.AbstractEventLoop] = None,
) -> None:
    """
    Best-effort dashboard event emission.

    v3.8: FIXED — When loop is provided, use it directly without
    calling get_running_loop(). This prevents RuntimeError in
    background threads.
    """
    global _failed_emits
    
    try:
        from core.socket_server import emit_to_dashboard
        
        # ✅ FIX: If loop is provided, use it directly
        if loop is not None:
            asyncio.run_coroutine_threadsafe(
                emit_to_dashboard(event_name, payload), 
                loop
            )
            return
        
        # Fallback: Try to get the current loop
        try:
            loop = asyncio.get_running_loop()
            asyncio.run_coroutine_threadsafe(
                emit_to_dashboard(event_name, payload), 
                loop
            )
        except RuntimeError:
            # No running loop — can't emit
            raise
            
    except RuntimeError:
        with _failed_emits_lock:
            _failed_emits += 1
            count = _failed_emits
        if count == 1 or count % 100 == 0:
            logger.warning(
                "Dashboard event '%s' lost (no event loop). "
                "Total dropped: %d",
                event_name, count,
            )
    except Exception as exc:
        logger.debug(
            "emit_dashboard_event error (%s): %s",
            event_name, exc,
        )


# ═══════════════════════════════════════════════════════════════════════════
# BRAND SCHEDULER
# ═══════════════════════════════════════════════════════════════════════════

class BrandScheduler:
    """Handles automated + manual posting for a single brand."""

    def __init__(
        self,
        brand: Brand,
        ai_profile: Optional[AIProfile],
        container: "ServiceContainer",
        job_service: Optional["JobService"] = None,
        event_bus=None,
        event_loop=None,
    ) -> None:
        self.brand = brand
        self.ai_profile = ai_profile
        self._container = container
        self._job_service = job_service
        self._event_bus = event_bus
        self._event_loop = event_loop  # ← Stored for dashboard events
        self._running = False
        self._thread = None
        self._stop_event = threading.Event()

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
            "status": "idle",
            "current_step": "",
            "progress": 0,
            "topic": "",
            "platforms": [],
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
            if hasattr(self._event_bus, "publish"):
                self._event_bus.publish(event_name, payload)
            elif hasattr(self._event_bus, "emit"):
                self._event_bus.emit(event_name, payload)
        except Exception:
            pass

    def _emit_dashboard(self, event_name: str, payload: dict) -> None:
        """Emit a dashboard event using the stored event loop."""
        # ✅ FIX: Always pass the stored loop
        _emit_dashboard_event(event_name, payload, loop=self._event_loop)

    # ═══════════════════════════════════════════════════════════════
    # LIFECYCLE
    # ═══════════════════════════════════════════════════════════════

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info(
            "BrandScheduler: started for '%s' — %d content slots, %d news slots",
            self.brand.name, len(self.content_hours), len(self.news_hours),
        )

    def stop(self) -> None:
        self._running = False
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def _sleep(self, seconds: float) -> None:
        """Sleep that wakes immediately on stop."""
        self._stop_event.wait(timeout=seconds)

    def _run(self) -> None:
        self._sleep(30)
        self._recover_on_startup()
        first_cycle = True
        while self._running:
            try:
                if first_cycle:
                    logger.info(
                        "BrandScheduler '%s': startup complete",
                        self.brand.name,
                    )
                    first_cycle = False
                else:
                    self._check_schedule()
                    self._check_scheduled_posts()
            except Exception as e:
                logger.error(
                    "BrandScheduler '%s' error: %s", self.brand.id, e
                )
            self._sleep(60)

    def _recover_on_startup(self) -> None:
        if not self._job_service:
            return
        try:
            now_utc = datetime.now(timezone.utc)
            today = now_utc.date()
            hour = now_utc.hour
            if hour in self.content_hours:
                idempotency_key = (
                    f"{self.brand.id}-{today.isoformat()}-{hour:02d}-content"
                )
                if not self._job_service.is_slot_claimed(idempotency_key):
                    if now_utc.minute < 30:
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
    # SCHEDULE CHECKING
    # ═══════════════════════════════════════════════════════════════

    def _check_schedule(self) -> None:
        status_raw = getattr(self.brand, "status", "")
        if hasattr(status_raw, "value"):
            status = str(status_raw.value).lower()
        else:
            status = str(status_raw or "").lower()
        if status != "active" or not self.ai_profile:
            return

        now_utc = datetime.now(timezone.utc)
        today = now_utc.date()
        hour = now_utc.hour

        self._fired_slots = {
            (d, h, k) for d, h, k in self._fired_slots if d == today
        }

        if hour in self.content_hours and (today, hour, "content") not in self._fired_slots:
            posts_today = sum(1 for d, h, k in self._fired_slots if k == "content")
            if posts_today < self.max_posts_per_day:
                self._trigger_workflow("scheduled", today, hour)

        if hour in self.news_hours and (today, hour, "news") not in self._fired_slots:
            logger.info("[%s] Scheduled news slot — hour %d UTC", self.brand.name, hour)
            self._fired_slots.add((today, hour, "news"))
            self._trigger_news()

    # ═══════════════════════════════════════════════════════════════
    # v3.8 — ATOMIC SCHEDULED-POST CLAIM (FIXED RACE CONDITION)
    # ═══════════════════════════════════════════════════════════════

    def _check_scheduled_posts(self) -> None:
        """Check and process scheduled posts from the database.

        v3.8: Two-step atomic claim to prevent race conditions.
        Step 1: Find the next queued post
        Step 2: Atomically claim it with conditional UPDATE
        """
        if not self._job_service:
            return
        try:
            from services.supabase import supabase_service

            if not supabase_service or not supabase_service.client:
                return

            now_utc = datetime.now(timezone.utc)

            # ── Try RPC-based atomic claim first ────────────────────
            try:
                result = supabase_service.client.rpc(
                    "claim_scheduled_post",
                    {
                        "p_brand_id": self.brand.id,
                        "p_scheduled_before": now_utc.isoformat(),
                        "p_worker_id": f"scheduler-{uuid.uuid4().hex[:8]}",
                    },
                ).execute()

                if result.data and len(result.data) > 0:
                    post = result.data[0]
                    post_id = post.get("id")
                    logger.info(
                        "[%s] Scheduled post claimed (atomic RPC): id=%s",
                        self.brand.name, post_id,
                    )
                    self._trigger_workflow("scheduled", scheduled_post_id=post_id)
                    return
            except Exception:
                pass  # RPC not available — use fallback

            # ── Fallback: Atomic conditional UPDATE ──────────────────
            # Step 1: Find the next queued post (order BEFORE filters)
            post_result = (
                supabase_service.client.table("scheduled_posts")
                .select("id")
                .order("scheduled_for")
                .eq("brand_id", self.brand.id)
                .lte("scheduled_for", now_utc.isoformat())
                .eq("status", "queued")
                .limit(1)
                .execute()
            )

            if not post_result.data:
                return

            post_id = post_result.data[0]["id"]

            # Step 2: Atomically claim it
            result = (
                supabase_service.client.table("scheduled_posts")
                .update({
                    "status": "processing",
                    "started_at": now_utc.isoformat(),
                })
                .eq("id", post_id)
                .eq("status", "queued")
                .execute()
            )

            claimed = result.data if hasattr(result, 'data') else []
            if claimed:
                logger.info(
                    "[%s] Scheduled post claimed: id=%s",
                    self.brand.name, post_id,
                )
                self._trigger_workflow("scheduled", scheduled_post_id=post_id)

        except Exception as e:
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

        existing = self._job_service.find_by_idempotency_key(idempotency_key)
        if existing:
            if existing.status == JobStatus.RETRY_PENDING:
                logger.info(
                    "[%s] job=%s — reusing RETRY_PENDING job %s for retry",
                    self.brand.name, job_id, existing.id,
                )
                job = self._job_service.reset_to_pending(existing.id)
                if job:
                    return job, idempotency_key
            elif existing.status in (JobStatus.PUBLISHED,):
                logger.info(
                    "[%s] job=%s — slot already completed by job %s",
                    self.brand.name, job_id, existing.id,
                )
                return None, None
            else:
                logger.info(
                    "[%s] job=%s — slot already claimed (status=%s) by job %s",
                    self.brand.name, job_id, existing.status.value, existing.id,
                )
                return None, None

        try:
            automation_job = self._job_service.create_scheduled_job(
                brand_id=brand_id, workspace_id=workspace_id,
                slot_date=slot_date, slot_hour=slot_hour, job_id=job_id,
            )
            logger.info(
                "[%s] job=%s — slot reserved (idempotency_key=%s)",
                self.brand.name, job_id, idempotency_key,
            )
            return automation_job, idempotency_key
        except Exception as e:
            from core.errors import DuplicateJobError
            if isinstance(e, DuplicateJobError):
                logger.info(
                    "[%s] job=%s — slot already claimed "
                    "(race condition, another worker won)",
                    self.brand.name, job_id,
                )
                return None, None
            raise

    # ═══════════════════════════════════════════════════════════════
    # WORKFLOW TRIGGER
    # ═══════════════════════════════════════════════════════════════

    def _trigger_workflow(
        self, source: str = "scheduled", today=None, hour=None,
        scheduled_post_id: Optional[str] = None,
    ) -> None:
        job_id = f"pub_{datetime.now().strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"
        agent_name = self.ai_profile.name if self.ai_profile else "Gemini"

        automation_job: Optional[AutomationJob] = None
        idempotency_key: Optional[str] = None

        if today is not None and hour is not None:
            workspace_id = getattr(self.brand, "workspace_id", "")
            slot_date = today.isoformat()

            if self._job_service:
                automation_job, idempotency_key = self._get_or_create_automation_job(
                    brand_id=self.brand.id, workspace_id=workspace_id,
                    slot_date=slot_date, slot_hour=hour, job_id=job_id, source=source,
                )
                if automation_job is None and idempotency_key is None:
                    return
                if automation_job:
                    try:
                        automation_job = self._job_service.transition(
                            automation_job, JobStatus.RUNNING,
                        )
                    except Exception as e:
                        logger.warning(
                            "[%s] job=%s — failed to transition to RUNNING: %s",
                            self.brand.name, job_id, e,
                        )
            else:
                idempotency_key = (
                    f"{self.brand.id}-{today.isoformat()}-{hour:02d}-content"
                )

        logger.info(
            "[%s] job=%s START | brand=%s | source=%s | agent=%s | slot=%s",
            self.brand.name, job_id, self.brand.name, source, agent_name,
            f"{today}T{hour}:00" if today and hour is not None else "manual",
        )

        self._emit("scheduler.workflow_started", {
            "brand_id": self.brand.id, "brand_name": self.brand.name,
            "job_id": job_id, "source": source,
        })

        # v3.8 — Use stored event loop for dashboard events
        self._emit_dashboard("mission_status", {
            "goal": f"Publish {self.brand.name} Content",
            "stage": "AI Generation", "progress": 5,
            "active_agent": agent_name, "is_active": True,
            "brand_id": self.brand.id, "brand_name": self.brand.name,
            "source": source, "job_id": job_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        self._pipeline_status = {
            "status": "running", "current_step": "ai", "progress": 30,
            "topic": "", "platforms": [], "job_id": job_id,
        }

        try:
            workflow_factory = self._container.resolve(
                "ContentGenerationPipelineFactory"
            )
            workflow = workflow_factory(
                brand=self.brand, ai_profile=self.ai_profile
            )
            self._pipeline_status = {
                "status": "running", "current_step": "render", "progress": 60,
                "topic": "", "platforms": [], "job_id": job_id,
            }
            result = workflow.run()
            self._pipeline_status = {
                "status": "running", "current_step": "publish", "progress": 80,
                "topic": getattr(result, "topic", ""),
                "platforms": [], "job_id": job_id,
            }

            topic, publish_ok, platforms = "", False, []
            for step in result.steps:
                if step.step_name == "select_topic" and step.ok:
                    topic = (step.output or {}).get("topic", "")
                elif step.step_name == "publish":
                    publish_ok = step.ok
                    if step.ok and isinstance(step.output, dict):
                        platforms = [
                            k for k, v in step.output.items() if v.get("ok")
                        ]

            if publish_ok:
                if today is not None and hour is not None:
                    self._fired_slots.add((today, hour, "content"))

                if automation_job and self._job_service:
                    try:
                        post_id = ""
                        for step in result.steps:
                            if step.step_name == "publish" and step.ok:
                                for p, r in (step.output or {}).items():
                                    if r.get("ok") and r.get("post_id"):
                                        post_id = r["post_id"]
                                        break
                        self._job_service.mark_published(
                            automation_job, post_id=post_id,
                            result={"topic": topic, "platforms": platforms},
                        )
                    except Exception as e:
                        logger.error(
                            "[%s] job=%s — failed to mark job published: %s",
                            self.brand.name, job_id, e,
                        )

                if scheduled_post_id:
                    try:
                        from services.supabase import supabase_service
                        if supabase_service and supabase_service.client:
                            supabase_service.client.table(
                                "scheduled_posts"
                            ).update({
                                "status": "published",
                                "completed_at": datetime.now(
                                    timezone.utc
                                ).isoformat(),
                            }).eq("id", scheduled_post_id).execute()
                    except Exception as e:
                        logger.error(
                            "[%s] job=%s — failed to update scheduled_post: %s",
                            self.brand.name, job_id, e,
                        )

                self._posts_published += 1
                self._pipeline_status = {
                    "status": "idle", "current_step": "", "progress": 100,
                    "topic": topic, "platforms": platforms, "job_id": job_id,
                }

                self._emit("scheduler.workflow_completed", {
                    "brand_id": self.brand.id, "job_id": job_id,
                    "topic": topic, "platforms": platforms,
                    "duration_ms": result.total_ms,
                })

                # v3.8 — Use stored event loop
                self._emit_dashboard("mission_status", {
                    "goal": f"Publish {self.brand.name} Content",
                    "stage": "Complete", "progress": 100,
                    "active_agent": agent_name, "is_active": False,
                    "brand_id": self.brand.id, "brand_name": self.brand.name,
                    "source": source, "job_id": job_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                logger.info(
                    "[%s] job=%s COMPLETED — topic='%s' | platforms=%s | %dms",
                    self.brand.name, job_id, topic, platforms, result.total_ms,
                )
            else:
                if idempotency_key:
                    with self._publish_lock:
                        self._published_keys.discard(idempotency_key)

                failed = [s.step_name for s in result.steps if not s.ok]
                if automation_job and self._job_service:
                    try:
                        self._job_service.mark_failed(
                            automation_job, error=f"Steps failed: {failed}",
                            error_code="WORKFLOW_FAILED", retryable=True,
                        )
                    except Exception as e:
                        logger.error(
                            "[%s] job=%s — failed to mark job failed: %s",
                            self.brand.name, job_id, e,
                        )

                self._posts_failed += 1
                self._emit("scheduler.workflow_failed", {
                    "brand_id": self.brand.id, "job_id": job_id,
                    "failed_steps": failed,
                })
                self._pipeline_status = {
                    "status": "error",
                    "current_step": failed[0] if failed else "unknown",
                    "progress": 0, "topic": topic,
                    "platforms": [], "job_id": job_id,
                }
                logger.warning(
                    "[%s] job=%s FAILED — steps=%s",
                    self.brand.name, job_id, failed,
                )

        except Exception as e:
            if idempotency_key:
                with self._publish_lock:
                    self._published_keys.discard(idempotency_key)

            if automation_job and self._job_service:
                try:
                    from core.errors import is_retryable
                    self._job_service.mark_failed(
                        automation_job, error=str(e),
                        error_code="WORKFLOW_EXCEPTION",
                        retryable=is_retryable(e),
                    )
                except Exception as db_exc:
                    logger.error(
                        "[%s] job=%s — failed to mark job failed: %s",
                        self.brand.name, job_id, db_exc,
                    )

            self._posts_failed += 1
            self._emit("scheduler.workflow_exception", {
                "brand_id": self.brand.id, "job_id": job_id, "error": str(e),
            })
            self._pipeline_status = {
                "status": "error", "current_step": "workflow",
                "progress": 0, "topic": "", "platforms": [], "job_id": job_id,
            }
            logger.error(
                "[%s] job=%s EXCEPTION: %s",
                self.brand.name, job_id, e,
            )

    def _trigger_news(self) -> None:
        try:
            from services.news_scraper import news_scraper
            news_scraper.post_news()
            logger.info("[%s] News posted successfully", self.brand.name)
        except Exception as e:
            logger.error("[%s] News post failed: %s", self.brand.name, e)

    # ═══════════════════════════════════════════════════════════════
    # MANUAL TRIGGER
    # ═══════════════════════════════════════════════════════════════

    def trigger_manual(self) -> dict:
        if not self._publish_lock.acquire(blocking=False):
            return {
                "ok": False,
                "error": "A publish is already in progress for this brand",
            }
        try:
            job_id = (
                f"pub_{datetime.now().strftime('%Y%m%d')}_"
                f"{uuid.uuid4().hex[:8]}"
            )
            agent_name = (
                self.ai_profile.name if self.ai_profile else "Gemini"
            )

            automation_job = None
            if self._job_service:
                try:
                    workspace_id = getattr(
                        self.brand, "workspace_id", ""
                    )
                    automation_job = (
                        self._job_service.create_manual_job(
                            brand_id=self.brand.id,
                            workspace_id=workspace_id,
                            job_id=job_id,
                        )
                    )
                    if automation_job:
                        self._job_service.transition(
                            automation_job, JobStatus.RUNNING
                        )
                except Exception as e:
                    logger.warning(
                        "[%s] job=%s — failed to create "
                        "persistent manual job: %s",
                        self.brand.name, job_id, e,
                    )

            logger.info(
                "[%s] job=%s MANUAL TRIGGER | brand=%s",
                self.brand.name, job_id, self.brand.name,
            )
            self._emit("scheduler.manual_triggered", {
                "brand_id": self.brand.id, "job_id": job_id,
            })

            workflow_factory = self._container.resolve(
                "ContentGenerationPipelineFactory"
            )
            workflow = workflow_factory(
                brand=self.brand, ai_profile=self.ai_profile
            )
            result = workflow.run()

            topic, publish_ok, platforms = "", False, []
            for step in result.steps:
                if step.step_name == "select_topic" and step.ok:
                    topic = (step.output or {}).get("topic", "")
                elif step.step_name == "publish":
                    publish_ok = step.ok
                    if step.ok and isinstance(step.output, dict):
                        platforms = [
                            k for k, v in step.output.items()
                            if v.get("ok")
                        ]

            if publish_ok:
                if automation_job and self._job_service:
                    try:
                        post_id = ""
                        for step in result.steps:
                            if step.step_name == "publish" and step.ok:
                                for p, r in (
                                    step.output or {}
                                ).items():
                                    if r.get("ok") and r.get("post_id"):
                                        post_id = r["post_id"]
                                        break
                        self._job_service.mark_published(
                            automation_job, post_id=post_id,
                            result={
                                "topic": topic,
                                "platforms": platforms,
                            },
                        )
                    except Exception as e:
                        logger.error(
                            "[%s] job=%s — failed to mark "
                            "manual job published: %s",
                            self.brand.name, job_id, e,
                        )

                logger.info(
                    "[%s] job=%s MANUAL OK — topic='%s' | "
                    "platforms=%s | %dms",
                    self.brand.name, job_id, topic, platforms,
                    round(result.total_ms),
                )
                return {
                    "ok": True, "brand": self.brand.name,
                    "topic": topic, "platforms": platforms,
                    "duration_ms": round(result.total_ms),
                    "job_id": job_id,
                }

            if automation_job and self._job_service:
                try:
                    self._job_service.mark_failed(
                        automation_job,
                        error="Publish step failed",
                        error_code="PUBLISH_FAILED",
                        retryable=True,
                    )
                except Exception as e:
                    logger.error(
                        "[%s] job=%s — failed to mark "
                        "manual job failed: %s",
                        self.brand.name, job_id, e,
                    )

            return {
                "ok": False, "error": "Publish step failed",
                "brand": self.brand.name,
                "duration_ms": round(result.total_ms),
                "job_id": job_id,
            }
        except Exception as e:
            return {
                "ok": False, "error": str(e),
                "brand": self.brand.name,
            }
        finally:
            self._publish_lock.release()

    # ═══════════════════════════════════════════════════════════════
    # STATUS
    # ═══════════════════════════════════════════════════════════════

    def get_status(self) -> dict:
        today = date.today()
        posts_today = sum(
            1 for d, h, k in self._fired_slots
            if d == today and k == "content"
        )
        job_stats = {}
        if self._job_service:
            try:
                job_stats = self._job_service.get_today_stats(
                    brand_id=self.brand.id
                )
                posts_today = job_stats.get("published", posts_today)
            except Exception:
                pass
        return {
            "brand_id": self.brand.id,
            "brand_name": self.brand.name,
            "running": self._running,
            "posts_today": posts_today,
            "max_posts_per_day": self.max_posts_per_day,
            "content_hours": self.content_hours,
            "news_hours": self.news_hours,
            "publish_busy": self._publish_lock.locked(),
            "job_stats": job_stats,
            "posts_published": self._posts_published,
            "posts_failed": self._posts_failed,
            **self._pipeline_status,
        }


# ═══════════════════════════════════════════════════════════════════════════
# BRAND SCHEDULER MANAGER v3.8
# ═══════════════════════════════════════════════════════════════════════════

class BrandSchedulerManager:
    """Manages all active BrandSchedulers.

    v3.8: Fixed event loop propagation for dashboard events.
    """

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
        self._total_posts_published = 0

    # ═══════════════════════════════════════════════════════════════
    # WIRING
    # ═══════════════════════════════════════════════════════════════

    def set_container(self, container: "ServiceContainer") -> None:
        self._container = container
        try:
            from services.job_service import JobService
            self._job_service = container.try_resolve(JobService)
        except Exception:
            logger.debug(
                "BrandSchedulerManager: JobService not available"
            )
        try:
            from core.event_bus import EventBus
            self._event_bus = container.try_resolve(EventBus)
        except Exception:
            logger.debug(
                "BrandSchedulerManager: EventBus not available"
            )

    def set_event_loop(
        self, loop: asyncio.AbstractEventLoop
    ) -> None:
        """Pass the FastAPI event loop for dashboard events.

        v3.8: Propagates to all existing AND future schedulers.
        Without this, dashboard events from scheduler threads are
        systematically dropped.
        """
        self._event_loop = loop
        for scheduler in self._schedulers.values():
            scheduler._event_loop = loop
        logger.info(
            "BrandSchedulerManager: event loop wired "
            "for dashboard events"
        )

    # ═══════════════════════════════════════════════════════════════
    # LIFECYCLE
    # ═══════════════════════════════════════════════════════════════

    def start(self) -> None:
        if self._running:
            return
        if not self._container:
            logger.error(
                "BrandSchedulerManager: container not set."
            )
            return
        self._running = True
        self._recover_all_stale_jobs()
        self._sync_brands()
        self._manager_thread = threading.Thread(
            target=self._sync_loop,
            daemon=True,
            name="scheduler-manager",
        )
        self._manager_thread.start()
        logger.info("BrandSchedulerManager v3.8: started.")

    def stop(self) -> None:
        self._running = False
        for s in self._schedulers.values():
            s.stop()
        if (
            self._manager_thread
            and self._manager_thread.is_alive()
        ):
            self._manager_thread.join(timeout=5)

    def _recover_all_stale_jobs(self) -> None:
        if not self._job_service:
            return
        try:
            if hasattr(
                self._job_service, "recover_and_retry"
            ):
                stale_count = (
                    self._job_service.recover_and_retry(
                        stale_minutes=15
                    )
                )
            else:
                stale_count = (
                    self._job_service.recover_stale_jobs(
                        stale_minutes=15
                    )
                )
            if stale_count > 0:
                logger.warning(
                    "BrandSchedulerManager: recovered %d "
                    "stale jobs",
                    stale_count,
                )
        except Exception as e:
            logger.error(
                "BrandSchedulerManager: stale job recovery "
                "failed: %s",
                e,
            )

    # ═══════════════════════════════════════════════════════════════
    # SYNC
    # ═══════════════════════════════════════════════════════════════

    def _sync_brands(self) -> None:
        brands = self.brand_repo.find_all()
        active_ids = set()
        for brand in brands:
            brand_status_raw = getattr(brand, "status", "")
            if hasattr(brand_status_raw, "value"):
                brand_status = str(
                    brand_status_raw.value
                ).lower()
            else:
                brand_status = str(
                    brand_status_raw or ""
                ).lower()
            if brand_status != "active":
                continue

            active_ids.add(brand.id)
            if brand.id not in self._schedulers:
                ai_profile = (
                    self.ai_repo.find_by_id(
                        brand.ai_profile_id
                    )
                    if brand.ai_profile_id
                    else None
                )
                if not ai_profile:
                    logger.warning(
                        "Brand '%s' has no AI Profile — skipping.",
                        brand.name,
                    )
                    continue

                scheduler = BrandScheduler(
                    brand,
                    ai_profile,
                    self._container,
                    job_service=self._job_service,
                    event_bus=self._event_bus,
                    event_loop=self._event_loop,  # ← Pass the loop
                )
                self._schedulers[brand.id] = scheduler
                scheduler.start()

        # v3.8 — notify Director via proper callback
        try:
            from services.director import director

            if (
                director
                and hasattr(director, "event_cb")
                and director.event_cb
            ):
                director.event_cb(
                    "brand_scheduler_sync",
                    {
                        "total_brands": len(brands),
                        "active_brands": len(active_ids),
                        "schedulers_running": len(
                            self._schedulers
                        ),
                        "timestamp": datetime.now(
                            timezone.utc
                        ).isoformat(),
                    },
                )
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
                self._sync_brands()
            except Exception as e:
                logger.error(
                    "BrandSchedulerManager sync error: %s", e
                )

    # ═══════════════════════════════════════════════════════════════
    # TRIGGERS
    # ═══════════════════════════════════════════════════════════════

    def trigger_for_brand(self, brand_id: str) -> dict:
        scheduler = self._schedulers.get(brand_id)
        if not scheduler:
            self._sync_brands()
            scheduler = self._schedulers.get(brand_id)
        if not scheduler:
            return {
                "ok": False,
                "error": (
                    f"No active scheduler for "
                    f"brand '{brand_id}'"
                ),
            }
        return scheduler.trigger_manual()

    def trigger_all(self) -> dict:
        results = {
            bid: s.trigger_manual()
            for bid, s in self._schedulers.items()
        }
        return {
            "ok": True,
            "results": results,
            "brands_triggered": len(results),
        }

    # ═══════════════════════════════════════════════════════════════
    # STATUS
    # ═══════════════════════════════════════════════════════════════

    def get_all_status(self) -> list[dict]:
        return [
            s.get_status()
            for s in self._schedulers.values()
        ]

    def get_status_for_brand(self, brand_id: str) -> dict:
        s = self._schedulers.get(brand_id)
        return (
            s.get_status()
            if s
            else {
                "error": (
                    f"No scheduler for brand '{brand_id}'"
                )
            }
        )

    def get_stats(self) -> dict:
        total_published = sum(
            s._posts_published
            for s in self._schedulers.values()
        )
        total_failed = sum(
            s._posts_failed
            for s in self._schedulers.values()
        )
        return {
            "brands_active": len(self._schedulers),
            "sync_count": self._sync_count,
            "total_posts_published": total_published,
            "total_posts_failed": total_failed,
        }

    def health_check(self) -> dict:
        stats = self.get_stats()
        status = "healthy"
        if stats["brands_active"] == 0:
            status = "idle"
        elif (
            stats["total_posts_failed"]
            > stats["total_posts_published"] * 0.3
            and stats["total_posts_published"] > 5
        ):
            status = "degraded"
        return {"status": status, "stats": stats}


# ═══════════════════════════════════════════════════════════════════════════
# EXPOSED FOR TESTING
# ═══════════════════════════════════════════════════════════════════════════

__all__ = [
    "BrandScheduler",
    "BrandSchedulerManager",
    "_emit_dashboard_event",
]
