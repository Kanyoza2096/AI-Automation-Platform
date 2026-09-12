"""
Automated Facebook post scheduler v8.4 — 10 posts/day, mixed topics.

New in v8.4
-----------
* Startup grace period: skips first enqueue cycle after deploy to prevent
  Gemini rate-limit stampede when multiple schedulers fire simultaneously.
* Multi-platform publishing via PluginRegistry: routes through FacebookPlugin
  which handles brand-specific credential resolution including
  platform_config.page_access_token fallback.
* Granular pipeline step tracking: emits current_step + progress to get_status()
  so the Workflow Studio frontend can animate nodes in real-time.
* Guard: _check_and_enqueue checks _running flag before processing slots.

Scheduling model
----------------
* 10 evenly-spaced UTC hour slots are read from settings.content_posting_hours_utc
* Each slot fires ONCE per calendar day
* 90-minute minimum gap between posts
* Watchdog auto-restarts scheduler if heartbeat stale >5 minutes
"""

import json
import os
import time
import random
import logging
import threading
import queue
from datetime import datetime, timedelta, timezone, date
from typing import Optional

from config.settings import settings
from services.content_engine import content_engine
from services.news_scraper import news_scraper
from services.facebook import facebook
from services.gemini import gemini
from services.growth import growth
from analytics.tracker import analytics
from utils.rate_limiter import rate_limiter

logger = logging.getLogger("kanyoza.scheduler")

MAX_JOB_QUEUE_SIZE = 10
MAX_REPLIED_COMMENTS = 500
MAX_COMMENTS_PER_POST = 3

_STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "scheduler_state.json")



def _job_call(service, method: str, *args, **kwargs):
    """Run JobService async API from legacy synchronous scheduler threads."""
    import asyncio
    import inspect
    coro = getattr(service, method)(*args, **kwargs)
    if not inspect.isawaitable(coro):
        return coro
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Scheduler should normally be outside an event loop; if embedded, execute
    # the coroutine on a dedicated thread to avoid nested-loop failures.
    import threading
    box = {}
    def runner():
        try: box["value"] = asyncio.run(coro)
        except BaseException as exc: box["error"] = exc
    t = threading.Thread(target=runner, daemon=True); t.start(); t.join()
    if "error" in box: raise box["error"]
    return box.get("value")


class PostScheduler:
    """10-posts-per-day scheduler with per-slot deduplication, granular pipeline tracking,
    and multi-platform publishing via PluginRegistry."""

    def __init__(self, supabase_client=None):
        self.supabase = supabase_client
        self.workflow_factory = None
        self.last_post_time: Optional[datetime] = None
        self.min_interval = timedelta(minutes=90)
        self.last_news_post_time: Optional[datetime] = None
        self.last_engagement_check: Optional[datetime] = None
        self.replied_comments: set = set()
        self.content_hours_utc = settings.content_posting_hours_utc
        self.news_hours_utc = settings.news_posting_hours_utc

        self._fired_slots: set = set()
        self._job_queue: queue.Queue = queue.Queue(maxsize=MAX_JOB_QUEUE_SIZE)
        self._running = False
        self._subthreads_running = False
        self.container = None

        # Granular pipeline status — read by get_status() for Workflow Studio
        self._current_pipeline_status: dict = {
            "status": "idle",
            "current_step": "",
            "progress": 0,
            "topic": "",
            "platforms": [],
        }

        self._lock = threading.Lock()
        self._publish_lock = threading.Lock()

        self._scheduler_thread: Optional[threading.Thread] = None
        self._worker_thread: Optional[threading.Thread] = None
        self._engagement_thread: Optional[threading.Thread] = None
        self._watchdog_thread: Optional[threading.Thread] = None

        self._load_state()

    # ═══════════════════════════════════════════════════════════════════════
    # State persistence
    # ═══════════════════════════════════════════════════════════════════════

    def _load_state(self):
        if self.supabase:
            try:
                result = self.supabase.table('scheduler_state').select('*').execute()
                if result.data:
                    for row in result.data:
                        if row['key'] == 'last_post_time' and row['value']:
                            self.last_post_time = datetime.fromisoformat(row['value'])
                        elif row['key'] == 'last_news_post_time' and row['value']:
                            self.last_news_post_time = datetime.fromisoformat(row['value'])
                        elif row['key'] == 'last_engagement_check' and row['value']:
                            self.last_engagement_check = datetime.fromisoformat(row['value'])
                return
            except Exception as e:
                logger.error("Failed to load scheduler state from Supabase: %s", e)

        try:
            state_path = os.path.abspath(_STATE_FILE)
            if not os.path.exists(state_path):
                return
            with open(state_path, "r") as f:
                data = json.load(f)
            if data.get("last_post_time"):
                self.last_post_time = datetime.fromisoformat(data["last_post_time"])
            if data.get("last_news_post_time"):
                self.last_news_post_time = datetime.fromisoformat(data["last_news_post_time"])
            today = datetime.now(timezone.utc).date()
            for entry in data.get("fired_slots", []):
                try:
                    d = date.fromisoformat(entry[0])
                    h = int(entry[1])
                    k = str(entry[2])
                    if d == today:
                        self._fired_slots.add((d, h, k))
                except Exception:
                    pass
            logger.info("Scheduler state loaded from file: last_post=%s  fired_today=%d",
                        self.last_post_time, len(self._fired_slots))
        except Exception as e:
            logger.warning("Could not load scheduler state file: %s", e)

    def _save_state(self, key: str, value: str):
        if self.supabase:
            try:
                self.supabase.table('scheduler_state').upsert({
                    'key': key, 'value': value,
                    'updated_at': datetime.now(timezone.utc).isoformat(),
                }).execute()
            except Exception as e:
                logger.error("Failed to save scheduler state to Supabase: %s", e)
        self._flush_state_file()

    def _flush_state_file(self):
        try:
            state_path = os.path.abspath(_STATE_FILE)
            with self._lock:
                slots_snapshot = list(self._fired_slots)
                last_post = self.last_post_time
                last_news = self.last_news_post_time
            data = {
                "last_post_time": last_post.isoformat() if last_post else None,
                "last_news_post_time": last_news.isoformat() if last_news else None,
                "fired_slots": [[d.isoformat(), h, k] for d, h, k in slots_snapshot],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            with open(state_path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning("Could not write scheduler state file: %s", e)

    # ═══════════════════════════════════════════════════════════════════════
    # Lifecycle
    # ═══════════════════════════════════════════════════════════════════════

    def start(self):
        if self._running:
            return
        self._running = True
        self._start_subthreads()
        self._watchdog_thread = threading.Thread(
            target=self._run_watchdog, daemon=True, name="scheduler-watchdog"
        )
        self._watchdog_thread.start()
        logger.info("Scheduler started. Content slots (UTC): %s  (%d posts/day)",
                    self.content_hours_utc, len(self.content_hours_utc))

    def _start_subthreads(self):
        self._subthreads_running = True
        self._scheduler_thread = threading.Thread(
            target=self._run, daemon=True, name="scheduler-producer"
        )
        self._worker_thread = threading.Thread(
            target=self._worker, daemon=True, name="scheduler-worker"
        )
        self._engagement_thread = threading.Thread(
            target=self._engagement_loop, daemon=True, name="engagement-loop"
        )
        self._scheduler_thread.start()
        self._worker_thread.start()
        self._engagement_thread.start()
        logger.info("Scheduler posting subthreads started.")

    def _stop_subthreads(self):
        self._subthreads_running = False
        try:
            self._job_queue.put(None, timeout=2)
        except Exception:
            pass
        for t in [self._scheduler_thread, self._worker_thread, self._engagement_thread]:
            if t and t.is_alive():
                t.join(timeout=5.0)

    def stop(self, timeout: float = 60.0):
        self._running = False
        self._subthreads_running = False
        try:
            self._job_queue.put(None, timeout=2)
        except queue.Full:
            pass
        for t in [self._scheduler_thread, self._worker_thread, self._engagement_thread, self._watchdog_thread]:
            if t and t.is_alive():
                t.join(timeout=timeout)
        gemini.close()
        facebook.close()
        news_scraper.close()

    # ═══════════════════════════════════════════════════════════════════════
    # Watchdog
    # ═══════════════════════════════════════════════════════════════════════

    def _run_watchdog(self):
        logger.info("Scheduler watchdog thread started.")
        time.sleep(120)
        while self._running:
            try:
                if self.container:
                    from core.redis_client import UpstashRedisClient
                    redis = self.container.try_resolve(UpstashRedisClient)
                    if redis and redis.is_available:
                        hb_str = redis.get("scheduler:heartbeat")
                        if hb_str:
                            hb_time = datetime.fromisoformat(hb_str)
                            now = datetime.now(timezone.utc)
                            if (now - hb_time) > timedelta(minutes=5):
                                logger.warning("Scheduler heartbeat stale (last: %s). Restarting...", hb_str)
                                self._trigger_restart()
            except Exception as e:
                logger.error("Watchdog check cycle failed: %s", e)
            for _ in range(30):
                if not self._running:
                    break
                time.sleep(1)

    def _trigger_restart(self):
        try:
            logger.warning("Watchdog: Triggering scheduler subthread restart.")
            self._stop_subthreads()
            time.sleep(2)
            self._start_subthreads()
            logger.info("Watchdog: Scheduler subthreads restarted successfully.")
        except Exception as e:
            logger.error("Watchdog restart execution failed: %s", e)

    def _write_heartbeat(self):
        try:
            if self.container:
                from core.redis_client import UpstashRedisClient
                redis = self.container.try_resolve(UpstashRedisClient)
                if redis and redis.is_available:
                    redis.set("scheduler:heartbeat", datetime.now(timezone.utc).isoformat())
        except Exception as e:
            logger.warning("Failed to write scheduler heartbeat to Redis: %s", e)

    # ═══════════════════════════════════════════════════════════════════════
    # Producer — checks schedule and enqueues jobs
    # ═══════════════════════════════════════════════════════════════════════

    def _run(self):
        time.sleep(30)
        first_cycle = True
        while self._subthreads_running:
            try:
                if first_cycle:
                    logger.info(
                        "PostScheduler: startup grace period — skipping first enqueue cycle "
                        "(prevents Gemini stampede on deploy)"
                    )
                    first_cycle = False
                else:
                    self._check_and_enqueue()
                self._write_heartbeat()
            except Exception as e:
                logger.error("Scheduler check error: %s", e)
            for _ in range(60):
                if not self._subthreads_running:
                    break
                time.sleep(1)

    def _check_and_enqueue(self):
        if not self._running:
            return

        now = datetime.now(timezone.utc)
        today = now.date()
        with self._lock:
            self._fired_slots = {(d, h, k) for d, h, k in self._fired_slots if d == today}
            content_slot = (today, now.hour, "content")
            content_due = now.hour in self.content_hours_utc
            already_fired = content_slot in self._fired_slots
            gap_ok = self.last_post_time is None or (now - self.last_post_time) >= self.min_interval
            rate_ok = rate_limiter.is_allowed("scheduler", "post_per_hour")
            enqueue_content = content_due and not already_fired and gap_ok and rate_ok
            if enqueue_content:
                self._fired_slots.add(content_slot)
        if enqueue_content:
            self._flush_state_file()
            self._enqueue_job("content")
            logger.info("Scheduled content post enqueued for slot UTC %02d:xx (%s)", now.hour, today)

    def _enqueue_job(self, job_type: str, **kwargs):
        try:
            self._job_queue.put((job_type, kwargs), timeout=10)
        except queue.Full:
            logger.warning("Job queue full, dropping: %s", job_type)

    # ═══════════════════════════════════════════════════════════════════════
    # Worker — processes jobs from the queue
    # ═══════════════════════════════════════════════════════════════════════

    def _worker(self):
        while self._subthreads_running:
            try:
                job = self._job_queue.get(timeout=1)
            except queue.Empty:
                continue
            if job is None:
                break
            job_type, kwargs = job
            try:
                if job_type == "content":
                    self._publish_content()
                elif job_type == "news":
                    self._publish_news(kwargs.get("category"))
                elif job_type == "engagement":
                    self._process_engagement()
            except Exception as e:
                logger.error("Worker error (%s): %s", job_type, e)
            finally:
                self._job_queue.task_done()

    # ═══════════════════════════════════════════════════════════════════════
    # Engagement
    # ═══════════════════════════════════════════════════════════════════════

    def _engagement_loop(self):
        time.sleep(120)
        while self._subthreads_running:
            try:
                self._process_engagement()
            except Exception as e:
                logger.error("Engagement error: %s", e)
            for _ in range(180):
                if not self._subthreads_running:
                    break
                time.sleep(10)

    def _process_engagement(self):
        now = datetime.now(timezone.utc)
        if self.last_engagement_check and (now - self.last_engagement_check) < timedelta(minutes=25):
            return
        self.last_engagement_check = now
        self._save_state('last_engagement_check', now.isoformat())
        posts = facebook.get_recent_posts(limit=5)
        for post in posts:
            self._engage_with_post(post["id"])
        growth.cross_engage()

    def _engage_with_post(self, post_id: str):
        comments = facebook.get_unreplied_comments(post_id)
        for comment in comments[:MAX_COMMENTS_PER_POST]:
            comment_id = comment["id"]
            commenter_id = comment.get("from", {}).get("id")
            username = comment.get("from", {}).get("name", "there")
            with self._lock:
                if self._is_already_replied(comment_id):
                    continue
            message = comment.get("message", "")
            reply = self._generate_comment_reply(message, username)
            if reply and facebook.reply_to_comment(comment_id, reply):
                self._mark_replied(comment_id, post_id, username, commenter_id, reply)
                facebook.like_comment(comment_id)
                growth.invite_commenter(commenter_id, username)
                time.sleep(5)

    def _is_already_replied(self, comment_id: str) -> bool:
        if comment_id in self.replied_comments:
            return True
        if self.supabase:
            try:
                result = self.supabase.table('replied_comments') \
                    .select('id') \
                    .eq('comment_id', comment_id) \
                    .maybe_single() \
                    .execute()
                if result.data:
                    self.replied_comments.add(comment_id)
                    return True
            except Exception:
                pass
        return False

    def _mark_replied(self, comment_id, post_id, username, commenter_id, reply):
        with self._lock:
            self.replied_comments.add(comment_id)
        if self.supabase:
            try:
                self.supabase.table('replied_comments').upsert({
                    'id': f"rc-{comment_id}",
                    'comment_id': comment_id,
                    'post_id': post_id,
                    'commenter_name': username,
                    'commenter_id': commenter_id,
                    'reply_text': reply[:500],
                    'replied_at': datetime.now(timezone.utc).isoformat(),
                }).execute()
            except Exception as e:
                logger.error("Failed to save replied comment: %s", e)

    def _generate_comment_reply(self, comment: str, username: str) -> Optional[str]:
        comment_lower = comment.lower()
        if "?" in comment and len(comment) > 20:
            return gemini.generate_comment_reply(comment=comment, username=username)
        if any(w in comment_lower for w in ["thanks", "thank you", "helpful", "great"]):
            return random.choice([
                f"Glad it helped, {username}! 🙌",
                f"Thanks {username}! Appreciate it 🔥",
            ])
        if len(comment.split()) <= 3:
            return random.choice(["👍", f"Thanks {username}!"])
        return random.choice([
            f"Good point, {username}! 💡",
            f"Interesting take, {username}! 🤔",
        ])

    # ═══════════════════════════════════════════════════════════════════════
    # Multi-Platform Publisher — routes through PluginRegistry
    # ═══════════════════════════════════════════════════════════════════════

    def _publish_to_all_platforms(self, caption: str, image_bytes=None) -> dict:
        """Deprecated compatibility shim: direct plugin publishing is forbidden."""
        return {"none": {
            "ok": False,
            "error": "direct PluginRegistry publication disabled; configure canonical workflow",
        }}

    # ═══════════════════════════════════════════════════════════════════════
    # Content Publisher — with Granular Step Tracking
    # ═══════════════════════════════════════════════════════════════════════

    def _publish_content(self):
        if not self._publish_lock.acquire(blocking=True, timeout=300):
            logger.warning("_publish_content: could not acquire publish lock — skipping")
            return
        try:
            self._do_publish_content()
        finally:
            self._publish_lock.release()

    def _do_publish_content(self):
        """Inner publish logic with granular step tracking for Workflow Studio."""
        if self.workflow_factory is not None:
            try:
                self._current_pipeline_status = {
                    "status": "running", "current_step": "trigger", "progress": 10,
                    "topic": "", "platforms": [],
                }
                workflow = self.workflow_factory()

                self._current_pipeline_status = {
                    "status": "running", "current_step": "ai", "progress": 30,
                    "topic": "", "platforms": [],
                }
                wf_result = workflow.run()

                self._current_pipeline_status = {
                    "status": "running", "current_step": "render", "progress": 60,
                    "topic": "", "platforms": [],
                }

                publish_ok = any(s.step_name == "publish" and s.ok for s in wf_result.steps)

                self._current_pipeline_status = {
                    "status": "running", "current_step": "publish", "progress": 80,
                    "topic": "", "platforms": [],
                }

                if publish_ok:
                    self.last_post_time = datetime.now(timezone.utc)
                    self._save_state('last_post_time', self.last_post_time.isoformat())
                    self._current_pipeline_status = {
                        "status": "idle", "current_step": "", "progress": 100,
                        "topic": getattr(wf_result, 'topic', ''),
                        "platforms": [],
                    }
                    logger.info("PostPublishWorkflow completed successfully (%.0fms)", wf_result.total_ms)
                    return

                failed = [s.step_name for s in wf_result.steps if not s.ok]
                self._current_pipeline_status = {
                    "status": "error", "current_step": failed[0] if failed else "unknown",
                    "progress": 0, "topic": "", "platforms": [],
                }
                logger.warning("PostPublishWorkflow publish step failed %s", failed)
                return

            except Exception as exc:
                self._current_pipeline_status = {
                    "status": "error", "current_step": "workflow", "progress": 0,
                    "topic": "", "platforms": [],
                }
                logger.error("PostPublishWorkflow raised unexpectedly: %s", exc)

        # ── No workflow factory: fail closed rather than bypass governance ──
        self._current_pipeline_status = {
            "status": "error", "current_step": "governance", "progress": 0,
            "topic": "", "platforms": [],
        }
        logger.error("PostScheduler: workflow_factory is not configured; refusing direct plugin publication")
        return

    def _publish_news(self, category: str = None):
        if self.last_news_post_time and datetime.now(timezone.utc) - self.last_news_post_time < timedelta(hours=3):
            return
        if news_scraper.post_news(category):
            self.last_post_time = datetime.now(timezone.utc)
            self.last_news_post_time = datetime.now(timezone.utc)
            self._save_state('last_post_time', self.last_post_time.isoformat())
            self._save_state('last_news_post_time', self.last_news_post_time.isoformat())

    # ═══════════════════════════════════════════════════════════════════════
    # Manual controls
    # ═══════════════════════════════════════════════════════════════════════

    def force_post(self) -> bool:
        try:
            self._job_queue.put(("content", {}), timeout=5)
            return True
        except queue.Full:
            return False

    def post_now(self) -> str:
        queued = self.force_post()
        return "content post queued" if queued else "queue full — try again shortly"

    def run_content_now(self) -> dict:
        """Run the configured canonical workflow; never bypass governance."""
        if not self._publish_lock.acquire(blocking=False):
            return {"ok": False, "error": "a publish is already in progress — try again shortly"}
        try:
            if self.workflow_factory is None:
                self._current_pipeline_status = {
                    "status": "error", "current_step": "governance", "progress": 0,
                    "topic": "", "platforms": [],
                }
                return {"ok": False, "error": "Canonical workflow factory is not configured; direct publishing is disabled"}

            self._current_pipeline_status = {
                "status": "running", "current_step": "trigger", "progress": 10,
                "topic": "", "platforms": [],
            }
            workflow = self.workflow_factory()
            wf_result = workflow.run()
            topic = ""
            publish_result = {}
            for step in wf_result.steps:
                if step.step_name == "select_topic" and step.ok:
                    topic = (step.output or {}).get("topic", "")
                elif step.step_name == "publish" and step.ok:
                    publish_result = step.output if isinstance(step.output, dict) else {}

            if publish_result.get("queued"):
                self._current_pipeline_status = {
                    "status": "queued", "current_step": "outbox", "progress": 90,
                    "topic": topic, "platforms": [],
                }
                return {"ok": True, "queued": True, "topic": topic, "results": publish_result, "duration_ms": round(wf_result.total_ms)}

            if publish_result.get("ok"):
                self._current_pipeline_status = {
                    "status": "idle", "current_step": "", "progress": 100,
                    "topic": topic, "platforms": [publish_result.get("platform", "")],
                }
                return {"ok": True, "topic": topic, "results": publish_result, "duration_ms": round(wf_result.total_ms)}

            failed = [s.step_name for s in wf_result.steps if not s.ok]
            self._current_pipeline_status = {
                "status": "error", "current_step": failed[0] if failed else "publish", "progress": 0,
                "topic": topic, "platforms": [],
            }
            return {"ok": False, "error": f"workflow failed: {failed}", "topic": topic}
        except Exception as exc:
            self._current_pipeline_status = {
                "status": "error", "current_step": "workflow", "progress": 0,
                "topic": "", "platforms": [],
            }
            logger.error("run_content_now failed: %s", exc, exc_info=True)
            return {"ok": False, "error": str(exc)}
        finally:
            self._publish_lock.release()

    def post_news_now(self) -> str:
        queued = self.force_news()
        return "news post queued" if queued else "queue full — try again shortly"

    def force_news(self, category=None) -> bool:
        try:
            self._job_queue.put(("news", {"category": category}), timeout=5)
            return True
        except queue.Full:
            return False

    def force_engagement(self) -> bool:
        try:
            self._job_queue.put(("engagement", {}), timeout=5)
            return True
        except queue.Full:
            return False

    # ═══════════════════════════════════════════════════════════════════════
    # Config & Status
    # ═══════════════════════════════════════════════════════════════════════

    def get_config(self) -> dict:
        return {
            "content_hours_utc": list(self.content_hours_utc),
            "news_hours_utc": list(self.news_hours_utc),
            "post_interval_hours": getattr(self, "post_interval_hours", None),
            "news_interval_hours": getattr(self, "news_interval_hours", None),
            "enabled": getattr(self, "enabled", True),
        }

    def update_config(self, **kwargs) -> dict:
        for hour_key in ("content_hours_utc", "news_hours_utc"):
            if hour_key in kwargs:
                hours = kwargs[hour_key]
                if not isinstance(hours, list) or not all(
                    isinstance(h, int) and 0 <= h <= 23 for h in hours
                ):
                    raise ValueError(f"{hour_key} must be a list of integers in 0–23")
                setattr(self, hour_key, sorted(set(hours)))
        for key in ("post_interval_hours", "news_interval_hours", "enabled"):
            if key in kwargs:
                setattr(self, key, kwargs[key])
        cfg = self.get_config()
        logger.info("Scheduler config updated: %s", cfg)
        return cfg

    def get_status(self) -> dict:
        now = datetime.now(timezone.utc)
        today = now.date()
        with self._lock:
            posts_today = sum(
                1 for d, h, k in self._fired_slots if d == today and k == "content"
            )
        return {
            "last_post": str(self.last_post_time) if self.last_post_time else None,
            "last_news_post": str(self.last_news_post_time) if self.last_news_post_time else None,
            "last_engagement": str(self.last_engagement_check) if self.last_engagement_check else None,
            "content_hours_utc": self.content_hours_utc,
            "news_hours_utc": self.news_hours_utc,
            "posts_today": posts_today,
            "target_posts_per_day": len(self.content_hours_utc),
            "queue_size": self._job_queue.qsize(),
            "running": self._running,
            "publish_busy": self._publish_lock.locked(),
            "current_step": self._current_pipeline_status.get("current_step", ""),
            "progress": self._current_pipeline_status.get("progress", 0),
            "topic": self._current_pipeline_status.get("topic", ""),
            "platforms": self._current_pipeline_status.get("platforms", []),
        }


scheduler = PostScheduler()
