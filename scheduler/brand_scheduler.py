"""
Brand-aware scheduling engine — UNIFIED v3.4
Replaces PostScheduler with a multi-tenant BrandSchedulerManager.

v3.4 — BRAND STATUS ENUM FIX:
  Brand status is stored as an enum (BrandStatus.ACTIVE). The scheduler
  now correctly extracts the .value attribute from enum types.

v3.3 — RETRY FIX + DIRECTOR INTELLIGENCE
"""

import time
import uuid
import logging
import threading
from datetime import datetime, timezone, date, timedelta
from typing import Dict, Optional, TYPE_CHECKING

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


def _emit_dashboard_event(event_name: str, payload: dict) -> None:
    global _failed_emits
    try:
        import asyncio
        from core.socket_server import emit_to_dashboard
        loop = asyncio.get_running_loop()
        asyncio.run_coroutine_threadsafe(emit_to_dashboard(event_name, payload), loop)
    except RuntimeError:
        with _failed_emits_lock:
            _failed_emits += 1
            count = _failed_emits
        if count == 1 or count % 100 == 0:
            logger.warning("Dashboard event '%s' lost (no event loop). Total dropped: %d", event_name, count)
    except Exception as exc:
        logger.debug("emit_dashboard_event error (%s): %s", event_name, exc)


class BrandScheduler:
    """Handles automated + manual posting for a single brand."""

    def __init__(
        self,
        brand: Brand,
        ai_profile: Optional[AIProfile],
        container: "ServiceContainer",
        job_service: Optional["JobService"] = None,
    ) -> None:
        self.brand = brand
        self.ai_profile = ai_profile
        self._container = container
        self._job_service = job_service
        self._running = False
        self._thread = None

        self._fired_slots: set = set()
        self._published_keys: set = set()
        self._publish_lock = threading.Lock()

        schedule = self.brand.posting_schedule or {}
        self.content_hours = schedule.get("content_hours", [])
        self.news_hours = schedule.get("news_hours", [])
        self.max_posts_per_day = schedule.get("max_posts_per_day", len(self.content_hours))
        self.post_interval_hours = schedule.get("post_interval_hours")
        self.news_interval_hours = schedule.get("news_interval_hours")

        self._pipeline_status: dict = {
            "status": "idle", "current_step": "", "progress": 0,
            "topic": "", "platforms": [],
        }

    def _is_development(self) -> bool:
        try:
            from config.settings import settings
            return settings.is_development
        except Exception:
            return False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("BrandScheduler: started for '%s' — %d content slots, %d news slots",
                     self.brand.name, len(self.content_hours), len(self.news_hours))

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def _run(self) -> None:
        time.sleep(30)
        self._recover_on_startup()
        first_cycle = True
        while self._running:
            try:
                if first_cycle:
                    logger.info("BrandScheduler '%s': startup grace period complete", self.brand.name)
                    first_cycle = False
                else:
                    self._check_schedule()
                    self._check_scheduled_posts()
            except Exception as e:
                logger.error("BrandScheduler '%s' error: %s", self.brand.id, e)
            for _ in range(60):
                if not self._running:
                    break
                time.sleep(1)

    def _recover_on_startup(self) -> None:
        if not self._job_service:
            return
        try:
            stale_count = self._job_service.recover_stale_jobs(stale_minutes=15)
            if stale_count > 0:
                logger.warning("BrandScheduler '%s': recovered %d stale jobs", self.brand.name, stale_count)

            now_utc = datetime.now(timezone.utc)
            today = now_utc.date()
            hour = now_utc.hour

            if hour in self.content_hours:
                idempotency_key = f"{self.brand.id}-{today.isoformat()}-{hour:02d}-content"
                if not self._job_service.is_slot_claimed(idempotency_key):
                    if now_utc.minute < 30:
                        logger.info("BrandScheduler '%s': recovering missed slot hour=%d", self.brand.name, hour)
                        self._trigger_workflow("scheduled", today, hour)
        except Exception as e:
            logger.error("BrandScheduler '%s': startup recovery failed: %s", self.brand.name, e)

    def _check_schedule(self) -> None:
        status_raw = getattr(self.brand, "status", "")
        if hasattr(status_raw, 'value'):
            status = str(status_raw.value).lower()
        else:
            status = str(status_raw or "").lower()
        if status != "active" or not self.ai_profile:
            return

        now_utc = datetime.now(timezone.utc)
        today = now_utc.date()
        hour = now_utc.hour

        self._fired_slots = {(d, h, k) for d, h, k in self._fired_slots if d == today}

        if hour in self.content_hours and (today, hour, "content") not in self._fired_slots:
            posts_today = sum(1 for d, h, k in self._fired_slots if k == "content")
            if posts_today < self.max_posts_per_day:
                self._trigger_workflow("scheduled", today, hour)

        if hour in self.news_hours and (today, hour, "news") not in self._fired_slots:
            logger.info("[%s] Scheduled news slot — hour %d UTC", self.brand.name, hour)
            self._fired_slots.add((today, hour, "news"))
            self._trigger_news()

    def _check_scheduled_posts(self) -> None:
        """Check and process scheduled posts from the database."""
        if not self._job_service:
            return
        try:
            from services.supabase import supabase_service
            if not supabase_service or not supabase_service.client:
                return

            now_utc = datetime.now(timezone.utc)
            
            # First: find a queued post
            result = supabase_service.client.table("scheduled_posts") \
                .select("*") \
                .lte("scheduled_for", now_utc.isoformat()) \
                .eq("status", "queued") \
                .eq("brand_id", self.brand.id) \
                .order("scheduled_for", desc=False) \
                .limit(1) \
                .execute()

            claimed = result.data or []
            if not claimed:
                return

            post = claimed[0]
            post_id = post.get("id")
            
            # Then: update it to processing
            supabase_service.client.table("scheduled_posts") \
                .update({"status": "processing", "started_at": now_utc.isoformat()}) \
                .eq("id", post_id) \
                .execute()

            logger.info("[%s] Scheduled post claimed: id=%s", self.brand.name, post_id)
            self._trigger_workflow("scheduled", scheduled_post_id=post_id)
        except Exception as e:
            logger.error("[%s] Error processing scheduled post: %s", self.brand.name, e)

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
                logger.info("[%s] job=%s — reusing RETRY_PENDING job %s for retry",
                            self.brand.name, job_id, existing.id)
                job = self._job_service.reset_to_pending(existing.id)
                if job:
                    return job, idempotency_key
            elif existing.status in (JobStatus.PUBLISHED,):
                logger.info("[%s] job=%s — slot already completed by job %s",
                            self.brand.name, job_id, existing.id)
                return None, None
            else:
                logger.info("[%s] job=%s — slot already claimed (status=%s) by job %s",
                            self.brand.name, job_id, existing.status.value, existing.id)
                return None, None

        try:
            automation_job = self._job_service.create_scheduled_job(
                brand_id=brand_id, workspace_id=workspace_id,
                slot_date=slot_date, slot_hour=slot_hour, job_id=job_id,
            )
            logger.info("[%s] job=%s — slot reserved (idempotency_key=%s)",
                        self.brand.name, job_id, idempotency_key)
            return automation_job, idempotency_key
        except Exception as e:
            from core.errors import DuplicateJobError
            if isinstance(e, DuplicateJobError):
                logger.info("[%s] job=%s — slot already claimed (race condition, another worker won)",
                            self.brand.name, job_id)
                return None, None
            raise

    def _trigger_workflow(
        self, source: str = "scheduled", today=None, hour=None,
        scheduled_post_id: Optional[str] = None,
    ) -> None:
        job_id = f"pub_{datetime.now().strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"
        agent_name = self.ai_profile.name if self.ai_profile else "Gemini"

        automation_job: Optional[AutomationJob] = None
        idempotency_key: Optional[str] = None

        if today is not None and hour is not None:
            workspace_id = getattr(self.brand, 'workspace_id', '')
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
                        automation_job = self._job_service.transition(automation_job, JobStatus.RUNNING)
                    except Exception as e:
                        logger.warning("[%s] job=%s — failed to transition to RUNNING: %s", self.brand.name, job_id, e)
            else:
                idempotency_key = f"{self.brand.id}-{today.isoformat()}-{hour:02d}-content"
                if self._is_development():
                    with self._publish_lock:
                        if idempotency_key in self._published_keys:
                            logger.info("[%s] job=%s — slot already published (in-memory)", self.brand.name, job_id)
                            return
                        self._published_keys.add(idempotency_key)
                else:
                    logger.error("[%s] job=%s — no JobService in production, slot cannot be claimed", self.brand.name, job_id)
                    return

        logger.info("[%s] job=%s START | brand=%s | source=%s | agent=%s | slot=%s",
                     self.brand.name, job_id, self.brand.name, source, agent_name,
                     f"{today}T{hour}:00" if today and hour is not None else "manual")

        _emit_dashboard_event("mission_status", {
            "goal": f"Publish {self.brand.name} Content", "stage": "AI Generation",
            "progress": 5, "active_agent": agent_name, "is_active": True,
            "brand_id": self.brand.id, "brand_name": self.brand.name,
            "source": source, "job_id": job_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        self._pipeline_status = {"status": "running", "current_step": "ai", "progress": 30,
                                  "topic": "", "platforms": [], "job_id": job_id}

        try:
            workflow_factory = self._container.resolve("ContentGenerationPipelineFactory")
            workflow = workflow_factory(brand=self.brand, ai_profile=self.ai_profile)
            self._pipeline_status = {"status": "running", "current_step": "render", "progress": 60,
                                      "topic": "", "platforms": [], "job_id": job_id}
            result = workflow.run()
            self._pipeline_status = {"status": "running", "current_step": "publish", "progress": 80,
                                      "topic": getattr(result, "topic", ""), "platforms": [], "job_id": job_id}

            topic = ""
            publish_ok = False
            platforms = []
            for step in result.steps:
                if step.step_name == "select_topic" and step.ok:
                    topic = (step.output or {}).get("topic", "")
                elif step.step_name == "publish":
                    publish_ok = step.ok
                    if step.ok and isinstance(step.output, dict):
                        platforms = [k for k, v in step.output.items() if v.get("ok")]

            if publish_ok:
                if today is not None and hour is not None:
                    self._fired_slots.add((today, hour, "content"))

                job_durable_confirmed = False
                if automation_job and self._job_service:
                    try:
                        post_id = ""
                        publish_results = {}
                        for step in result.steps:
                            if step.step_name == "publish" and step.ok:
                                publish_results = step.output or {}
                                for p, r in publish_results.items():
                                    if r.get("ok") and r.get("post_id"):
                                        post_id = r["post_id"]
                                        break
                        self._job_service.mark_published(
                            automation_job, post_id=post_id,
                            result={"topic": topic, "platforms": platforms},
                        )
                        job_durable_confirmed = True
                    except Exception as e:
                        logger.error("[%s] job=%s — failed to mark job published: %s", self.brand.name, job_id, e)
                else:
                    job_durable_confirmed = True

                if scheduled_post_id and job_durable_confirmed:
                    try:
                        from services.supabase import supabase_service
                        if supabase_service and supabase_service.client:
                            supabase_service.client.table("scheduled_posts") \
                                .update({"status": "published", "completed_at": datetime.now(timezone.utc).isoformat()}) \
                                .eq("id", scheduled_post_id).execute()
                    except Exception as e:
                        logger.error("[%s] job=%s — failed to update scheduled_post: %s", self.brand.name, job_id, e)

                self._pipeline_status = {"status": "idle", "current_step": "", "progress": 100,
                                          "topic": topic, "platforms": platforms, "job_id": job_id}

                _emit_dashboard_event("mission_status", {
                    "goal": f"Publish {self.brand.name} Content", "stage": "Complete",
                    "progress": 100, "active_agent": agent_name, "is_active": False,
                    "brand_id": self.brand.id, "brand_name": self.brand.name,
                    "source": source, "job_id": job_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                logger.info("[%s] job=%s COMPLETED — topic='%s' | platforms=%s | %dms",
                            self.brand.name, job_id, topic, platforms, result.total_ms)
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
                        logger.error("[%s] job=%s — failed to mark job failed: %s", self.brand.name, job_id, e)

                self._pipeline_status = {"status": "error", "current_step": failed[0] if failed else "unknown",
                                          "progress": 0, "topic": topic, "platforms": [], "job_id": job_id}
                logger.warning("[%s] job=%s FAILED — steps=%s — slot released for retry",
                               self.brand.name, job_id, failed)

        except Exception as e:
            if idempotency_key:
                with self._publish_lock:
                    self._published_keys.discard(idempotency_key)

            if automation_job and self._job_service:
                try:
                    from core.errors import is_retryable
                    self._job_service.mark_failed(
                        automation_job, error=str(e),
                        error_code="WORKFLOW_EXCEPTION", retryable=is_retryable(e),
                    )
                except Exception as db_exc:
                    logger.error("[%s] job=%s — failed to mark job failed: %s", self.brand.name, job_id, db_exc)

            self._pipeline_status = {"status": "error", "current_step": "workflow",
                                      "progress": 0, "topic": "", "platforms": [], "job_id": job_id}
            logger.error("[%s] job=%s EXCEPTION: %s — slot released for retry", self.brand.name, job_id, e)

    def _trigger_news(self) -> None:
        try:
            from services.news_scraper import news_scraper
            news_scraper.post_news()
            logger.info("[%s] News posted successfully", self.brand.name)
        except Exception as e:
            logger.error("[%s] News post failed: %s", self.brand.name, e)

    def trigger_manual(self) -> dict:
        if not self._publish_lock.acquire(blocking=False):
            return {"ok": False, "error": "A publish is already in progress for this brand"}
        try:
            job_id = f"pub_{datetime.now().strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"
            agent_name = self.ai_profile.name if self.ai_profile else "Gemini"

            automation_job = None
            if self._job_service:
                try:
                    workspace_id = getattr(self.brand, 'workspace_id', '')
                    automation_job = self._job_service.create_manual_job(
                        brand_id=self.brand.id, workspace_id=workspace_id, job_id=job_id,
                    )
                    if automation_job:
                        self._job_service.transition(automation_job, JobStatus.RUNNING)
                except Exception as e:
                    logger.warning("[%s] job=%s — failed to create persistent manual job: %s", self.brand.name, job_id, e)

            logger.info("[%s] job=%s MANUAL TRIGGER | brand=%s", self.brand.name, job_id, self.brand.name)

            workflow_factory = self._container.resolve("ContentGenerationPipelineFactory")
            workflow = workflow_factory(brand=self.brand, ai_profile=self.ai_profile)
            result = workflow.run()

            topic = ""
            publish_ok = False
            platforms = []
            for step in result.steps:
                if step.step_name == "select_topic" and step.ok:
                    topic = (step.output or {}).get("topic", "")
                elif step.step_name == "publish":
                    publish_ok = step.ok
                    if step.ok and isinstance(step.output, dict):
                        platforms = [k for k, v in step.output.items() if v.get("ok")]

            if publish_ok:
                if automation_job and self._job_service:
                    try:
                        post_id = ""
                        for step in result.steps:
                            if step.step_name == "publish" and step.ok:
                                for p, r in (step.output or {}).items():
                                    if r.get("ok") and r.get("post_id"):
                                        post_id = r["post_id"]
                                        break
                        self._job_service.mark_published(automation_job, post_id=post_id, result={"topic": topic, "platforms": platforms})
                    except Exception as e:
                        logger.error("[%s] job=%s — failed to mark manual job published: %s", self.brand.name, job_id, e)

                logger.info("[%s] job=%s MANUAL OK — topic='%s' | platforms=%s | %dms",
                            self.brand.name, job_id, topic, platforms, round(result.total_ms))
                return {"ok": True, "brand": self.brand.name, "topic": topic,
                        "platforms": platforms, "duration_ms": round(result.total_ms), "job_id": job_id}

            if automation_job and self._job_service:
                try:
                    self._job_service.mark_failed(automation_job, error="Publish step failed",
                                                   error_code="PUBLISH_FAILED", retryable=True)
                except Exception as e:
                    logger.error("[%s] job=%s — failed to mark manual job failed: %s", self.brand.name, job_id, e)

            return {"ok": False, "error": "Publish step failed", "brand": self.brand.name,
                    "duration_ms": round(result.total_ms), "job_id": job_id}

        except Exception as e:
            return {"ok": False, "error": str(e), "brand": self.brand.name}
        finally:
            self._publish_lock.release()

    def get_status(self) -> dict:
        today = date.today()
        posts_today = sum(1 for d, h, k in self._fired_slots if d == today and k == "content")
        job_stats = {}
        if self._job_service:
            try:
                job_stats = self._job_service.get_today_stats(brand_id=self.brand.id)
                posts_today = job_stats.get("published", posts_today)
            except Exception:
                pass
        return {
            "brand_id": self.brand.id, "brand_name": self.brand.name,
            "running": self._running, "posts_today": posts_today,
            "max_posts_per_day": self.max_posts_per_day,
            "content_hours": self.content_hours, "news_hours": self.news_hours,
            "publish_busy": self._publish_lock.locked(), "job_stats": job_stats,
            **self._pipeline_status,
        }


class BrandSchedulerManager:
    """Manages all active BrandSchedulers. Single entry point for API routes."""

    def __init__(self, brand_repository: BrandRepository, ai_profile_repository: AIProfileRepository) -> None:
        self.brand_repo = brand_repository
        self.ai_repo = ai_profile_repository
        self._schedulers: Dict[str, BrandScheduler] = {}
        self._running = False
        self._manager_thread = None
        self._container = None
        self._job_service = None

    def set_container(self, container: "ServiceContainer") -> None:
        self._container = container
        try:
            from services.job_service import JobService
            self._job_service = container.try_resolve(JobService)
        except Exception:
            logger.debug("BrandSchedulerManager: JobService not available")

    def start(self) -> None:
        if self._running:
            return
        if not self._container:
            logger.error("BrandSchedulerManager: container not set.")
            return
        self._running = True
        self._sync_brands()
        self._manager_thread = threading.Thread(target=self._sync_loop, daemon=True, name="scheduler-manager")
        self._manager_thread.start()
        logger.info("BrandSchedulerManager: started.")

    def stop(self) -> None:
        self._running = False
        for s in self._schedulers.values():
            s.stop()
        if self._manager_thread and self._manager_thread.is_alive():
            self._manager_thread.join(timeout=5)

    def _sync_brands(self) -> None:
        brands = self.brand_repo.find_all()
        active_ids = set()
        for brand in brands:
            brand_status_raw = getattr(brand, "status", "")
            if hasattr(brand_status_raw, 'value'):
                brand_status = str(brand_status_raw.value).lower()
            else:
                brand_status = str(brand_status_raw or "").lower()
            if brand_status != "active":
                continue
            active_ids.add(brand.id)
            if brand.id not in self._schedulers:
                ai_profile = self.ai_repo.find_by_id(brand.ai_profile_id) if brand.ai_profile_id else None
                if not ai_profile:
                    logger.warning("Brand '%s' has no AI Profile — skipping.", brand.name)
                    continue
                scheduler = BrandScheduler(brand, ai_profile, self._container, job_service=self._job_service)
                self._schedulers[brand.id] = scheduler
                scheduler.start()

        try:
            from services.director import director
            if director and hasattr(director, '_event'):
                director._event("brand_scheduler_sync", {
                    "total_brands": len(brands),
                    "active_brands": len(active_ids),
                    "schedulers_running": len(self._schedulers),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
        except Exception:
            pass

        for bid in list(self._schedulers.keys()):
            if bid not in active_ids:
                self._schedulers[bid].stop()
                del self._schedulers[bid]

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
                logger.error("BrandSchedulerManager sync error: %s", e)

    def trigger_for_brand(self, brand_id: str) -> dict:
        scheduler = self._schedulers.get(brand_id)
        if not scheduler:
            self._sync_brands()
            scheduler = self._schedulers.get(brand_id)
        if not scheduler:
            return {"ok": False, "error": f"No active scheduler for brand '{brand_id}'"}
        return scheduler.trigger_manual()

    def trigger_all(self) -> dict:
        results = {bid: s.trigger_manual() for bid, s in self._schedulers.items()}
        return {"ok": True, "results": results, "brands_triggered": len(results)}

    def get_all_status(self) -> list[dict]:
        return [s.get_status() for s in self._schedulers.values()]

    def get_status_for_brand(self, brand_id: str) -> dict:
        s = self._schedulers.get(brand_id)
        return s.get_status() if s else {"error": f"No scheduler for brand '{brand_id}'"}
