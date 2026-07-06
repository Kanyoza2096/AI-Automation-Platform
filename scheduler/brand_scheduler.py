"""
Brand-aware scheduling engine.
Replaces the old singleton PostScheduler with a multi-tenant BrandSchedulerManager.
"""
import time
import logging
import threading
from datetime import datetime, timezone
from typing import Dict, Optional

from models.brand import Brand
from models.ai_profile import AIProfile
from repositories.brand_repository import BrandRepository
from repositories.ai_profile_repository import AIProfileRepository

logger = logging.getLogger("kanyoza.scheduler.brand")

class BrandScheduler:
    """A lightweight scheduler dedicated to a single brand."""
    
    def __init__(self, brand: Brand, ai_profile: Optional[AIProfile]):
        self.brand = brand
        self.ai_profile = ai_profile
        self._running = False
        self._thread = None
        self._fired_slots = set()
        
        # Schedule config
        schedule = self.brand.posting_schedule or {}
        self.timezone = schedule.get("timezone", "UTC")
        self.content_hours = schedule.get("content_hours", [])
        
    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("Started scheduler for Brand '%s' with %d slots.", self.brand.name, len(self.content_hours))

    def stop(self):
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def _run(self):
        # Give system time to fully start before firing
        time.sleep(30)
        while self._running:
            try:
                self._check_schedule()
            except Exception as e:
                logger.error("BrandScheduler '%s' error: %s", self.brand.id, e)
            
            # Check every minute
            for _ in range(60):
                if not self._running:
                    break
                time.sleep(1)
            
    def _check_schedule(self):
        if not self.brand.status == "active" or not self.ai_profile:
            return

        now = datetime.now(timezone.utc)
        # TODO: Handle timezone conversion (brand.timezone) if not UTC
        today = now.date()
        hour = now.hour
        
        if hour in self.content_hours and (today, hour) not in self._fired_slots:
            logger.info("[%s] Scheduled slot reached for hour %d", self.brand.name, hour)
            self._fired_slots.add((today, hour))
            self._trigger_workflow()

    def _trigger_workflow(self):
        """Triggers the brand-aware PostPublishWorkflow."""
        try:
            from dependency_registry import build_container
            
            # Note: We should ideally have the container injected into the Manager,
            # but for this transition phase, we'll build or access the global one.
            # Assuming main.py or lifecycle.py handles the global container.
            # For this shim, we will instantiate a new container just to resolve
            container = build_container()
            workflow_factory = container.resolve("PostPublishWorkflowFactory")
            
            workflow = workflow_factory(brand=self.brand, ai_profile=self.ai_profile)
            result = workflow.run()
            logger.info("[%s] PostPublishWorkflow completed in %d ms.", self.brand.name, result.total_ms)
            
        except Exception as e:
            logger.error("[%s] Failed to trigger workflow: %s", self.brand.name, e)


class BrandSchedulerManager:
    """Manages all active BrandSchedulers."""
    
    def __init__(self, brand_repository: BrandRepository, ai_profile_repository: AIProfileRepository):
        self.brand_repo = brand_repository
        self.ai_repo = ai_profile_repository
        self._schedulers: Dict[str, BrandScheduler] = {}
        self._running = False
        self._manager_thread = None

    def start(self):
        if self._running: return
        self._running = True
        self._sync_brands()
                
        self._manager_thread = threading.Thread(target=self._sync_loop, daemon=True, name="scheduler-manager")
        self._manager_thread.start()
        logger.info("BrandSchedulerManager started.")

    def stop(self):
        self._running = False
        for s in self._schedulers.values():
            s.stop()
        if self._manager_thread:
            self._manager_thread.join(timeout=5)

    def _sync_brands(self):
        brands = self.brand_repo.find_all()
        active_ids = set()
        
        for brand in brands:
            if brand.status == 'active':
                active_ids.add(brand.id)
                if brand.id not in self._schedulers:
                    ai_profile = self.ai_repo.find_by_id(brand.ai_profile_id) if brand.ai_profile_id else None
                    if not ai_profile:
                        logger.warning("Brand '%s' has no AI Profile, scheduling skipped.", brand.name)
                        continue
                        
                    scheduler = BrandScheduler(brand, ai_profile)
                    self._schedulers[brand.id] = scheduler
                    scheduler.start()
                    
        # Clean up schedulers for brands that are no longer active
        for bid in list(self._schedulers.keys()):
            if bid not in active_ids:
                logger.info("Stopping scheduler for inactive brand %s", bid)
                self._schedulers[bid].stop()
                del self._schedulers[bid]

    def _sync_loop(self):
        while self._running:
            for _ in range(300): # Sync every 5 mins
                if not self._running:
                    break
                time.sleep(1)
            if not self._running: break
            
            try:
                self._sync_brands()
            except Exception as e:
                logger.error("BrandSchedulerManager sync error: %s", e)
