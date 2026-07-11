"""
Automated Facebook post scheduler v8.1 — 10 posts/day, mixed topics.

Bug fixes in v8.1
-----------------
* File-based state persistence: _fired_slots and last_post_time are written to
  scheduler_state.json so app restarts do not re-fire already-executed slots.
* Publish lock: _publish_lock prevents concurrent publishes between the
  scheduled worker and a manual /post command.
* News/content overlap: if content fires at a given hour, news is skipped that
  cycle so two posts never go out simultaneously.
* Default news hours (6, 14, 20) no longer overlap with content hours.

Scheduling model
----------------
* 10 evenly-spaced UTC hour slots are read from settings.content_posting_hours_utc
  (default: 1, 3, 5, 7, 9, 11, 13, 15, 17, 19 — one slot every ~2 h).
* Each slot fires ONCE per calendar day: the scheduler tracks fired (date, hour)
  pairs in _fired_slots (persisted to disk) and skips any slot already executed.
* A 90-minute minimum gap between posts prevents back-to-back firing.
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


class PostScheduler:
    """10-posts-per-day scheduler with per-slot deduplication."""

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

        # Track which slots have already been posted today.
        # Format: set of 3-tuples (datetime.date, int hour, str kind)
        self._fired_slots: set = set()

        self._job_queue: queue.Queue = queue.Queue(maxsize=MAX_JOB_QUEUE_SIZE)
        self._running = False

        # _lock protects _fired_slots and scheduling state.
        # _publish_lock ensures only one publish runs at a time (scheduled OR manual).
        self._lock = threading.Lock()
        self._publish_lock = threading.Lock()

        self._scheduler_thread: Optional[threading.Thread] = None
        self._worker_thread: Optional[threading.Thread] = None
        self._engagement_thread: Optional[threading.Thread] = None

        self._load_state()

    # ── State persistence ──────────────────────────────────────────────────────

    def _load_state(self):
        """Load persisted state from Supabase (preferred) or local JSON file."""
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

            logger.info(
                "Scheduler state loaded from file: last_post=%s  fired_today=%d",
                self.last_post_time, len(self._fired_slots),
            )
        except Exception as e:
            logger.warning("Could not load scheduler state file: %s", e)

    def _save_state(self, key: str, value: str):
        """Persist state to Supabase (if available) and local JSON file."""
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
        """Write current in-memory state to the local JSON file."""
        try:
            state_path = os.path.abspath(_STATE_FILE)
            with self._lock:
                slots_snapshot = list(self._fired_slots)
                last_post = self.last_post_time
                last_news = self.last_news_post_time

            data = {
                "last_post_time": last_post.isoformat() if last_post else None,
                "last_news_post_time": last_news.isoformat() if last_news else None,
                "fired_slots": [
                    [d.isoformat(), h, k] for d, h, k in slots_snapshot
                ],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            with open(state_path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning("Could not write scheduler state file: %s", e)

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start(self):
        if self._running:
            return
        self._running = True
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
        logger.info(
            "Scheduler started. Content slots (UTC): %s  (%d posts/day)",
            self.content_hours_utc, len(self.content_hours_utc),
        )

    def stop(self, timeout: float = 60.0):
        self._running = False
        try:
            self._job_queue.put(None, timeout=2)
        except queue.Full:
            pass
        for t in [self._scheduler_thread, self._worker_thread, self._engagement_thread]:
            if t and t.is_alive():
                t.join(timeout=timeout)
        gemini.close()
        facebook.close()
        news_scraper.close()

    # ── Producer ───────────────────────────────────────────────────────────────

    def _run(self):
        """Check schedule every 60 s and enqueue content/news jobs."""
        time.sleep(30)
        while self._running:
            try:
                self._check_and_enqueue()
            except Exception as e:
                logger.error("Scheduler check error: %s", e)
            for _ in range(60):
                if not self._running:
                    break
                time.sleep(1)

    def _check_and_enqueue(self):
        now = datetime.now(timezone.utc)
        today = now.date()

        with self._lock:
            self._fired_slots = {
                (d, h, k) for d, h, k in self._fired_slots if d == today
            }

            content_slot = (today, now.hour, "content")
            content_due = now.hour in self.content_hours_utc
            already_fired = content_slot in self._fired_slots
            gap_ok = (
                self.last_post_time is None
                or (now - self.last_post_time) >= self.min_interval
            )
            rate_ok = rate_limiter.is_allowed("scheduler", "post_per_hour")

            if content_due and not already_fired and gap_ok and rate_ok:
                self._fired_slots.add(content_slot)
                enqueue_content = True
            else:
                enqueue_content = False

        if enqueue_content:
            self._flush_state_file()
            self._enqueue_job("content")
            logger.info(
                "Scheduled content post enqueued for slot UTC %02d:xx (%s)",
                now.hour, today,
            )

    def _enqueue_job(self, job_type: str, **kwargs):
        try:
            self._job_queue.put((job_type, kwargs), timeout=10)
        except queue.Full:
            logger.warning("Job queue full, dropping: %s", job_type)

    # ── Worker ─────────────────────────────────────────────────────────────────

    def _worker(self):
        while self._running:
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

    # ── Engagement loop ────────────────────────────────────────────────────────

    def _engagement_loop(self):
        time.sleep(120)
        while self._running:
            try:
                self._process_engagement()
            except Exception as e:
                logger.error("Engagement error: %s", e)
            for _ in range(180):
                if not self._running:
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
                result = (
                    self.supabase.table('replied_comments')
                    .select('id').eq('comment_id', comment_id).maybe_single().execute()
                )
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
                    'id': f"rc-{comment_id}", 'comment_id': comment_id,
                    'post_id': post_id, 'commenter_name': username,
                    'commenter_id': commenter_id, 'reply_text': reply[:500],
                    'replied_at': datetime.now(timezone.utc).isoformat(),
                }).execute()
            except Exception as e:
                logger.error("Failed to save replied comment: %s", e)

    def _generate_comment_reply(self, comment: str, username: str) -> Optional[str]:
        comment_lower = comment.lower()
        if "?" in comment and len(comment) > 20:
            return gemini.generate_comment_reply(comment=comment, username=username)
        if any(w in comment_lower for w in ["thanks", "thank you", "helpful", "great"]):
            return random.choice([f"Glad it helped, {username}! 🙌", f"Thanks {username}! Appreciate it 🔥"])
        if len(comment.split()) <= 3:
            return random.choice(["👍", f"Thanks {username}!"])
        return random.choice([f"Good point, {username}! 💡", f"Interesting take, {username}! 🤔"])

    # ── Publishers ─────────────────────────────────────────────────────────────

    def _publish_to_all_platforms(self, caption: str, image_bytes=None) -> dict:
        """Publish content to ALL connected social accounts via PluginRegistry."""
        results = {}
        try:
            from flask import current_app
            from plugins.base import PluginRegistry
            container = current_app.extensions.get("service_container")
            if container:
                registry = container.resolve(PluginRegistry)
                for plugin in registry.get_all():
                    try:
                        res = plugin.publish(caption, image_bytes=image_bytes)
                        results[plugin.name] = {"ok": res.get("ok", False), "error": res.get("reason")}
                        if res.get("ok"):
                            logger.info("Published to %s", plugin.name)
                        else:
                            logger.warning("Failed to publish to %s: %s", plugin.name, res.get("reason"))
                    except Exception as e:
                        logger.warning("Plugin %s error: %s", plugin.name, e)
                        results[plugin.name] = {"ok": False, "error": str(e)}
                return results
        except Exception as e:
            logger.error("PluginRegistry error: %s", e)
        
        # Fallback to legacy Facebook singleton
        success = facebook.post_to_page(caption, image_bytes=image_bytes)
        return {"facebook": {"ok": bool(success)}}

    def _publish_content(self):
        """Generate and post via PostPublishWorkflow (or legacy fallback)."""
        if not self._publish_lock.acquire(blocking=True, timeout=300):
            logger.warning("_publish_content: could not acquire publish lock — skipping")
            return
        try:
            self._do_publish_content()
        finally:
            self._publish_lock.release()

    def _do_publish_content(self):
        """Inner publish logic — must only be called while _publish_lock is held."""
        if self.workflow_factory is not None:
            try:
                workflow = self.workflow_factory()
                wf_result = workflow.run()
                publish_ok = any(
                    s.step_name == "publish" and s.ok for s in wf_result.steps
                )
                if publish_ok:
                    self.last_post_time = datetime.now(timezone.utc)
                    self._save_state('last_post_time', self.last_post_time.isoformat())
                    logger.info("PostPublishWorkflow completed successfully (%.0fms)", wf_result.total_ms)
                    return
                failed = [s.step_name for s in wf_result.steps if not s.ok]
                logger.warning(
                    "PostPublishWorkflow publish step failed %s — falling back to legacy path",
                    failed,
                )
            except Exception as exc:
                logger.error("PostPublishWorkflow raised unexpectedly: %s — falling back to legacy path", exc)

        result = content_engine.generate_post()
        caption = result.get("caption", "") if isinstance(result, dict) else (result[0] if result else "")
        image_bytes = result.get("image_bytes") if isinstance(result, dict) else (result[1] if len(result) > 1 else None)

        if not caption:
            logger.warning("ContentEngine returned empty caption — skipping post")
            return

        results = self._publish_to_all_platforms(caption, image_bytes=image_bytes)
        success = any(v.get("ok") for v in results.values())
        if success:
            self.last_post_time = datetime.now(timezone.utc)
            self._save_state('last_post_time', self.last_post_time.isoformat())
            category = result.get("category", "") if isinstance(result, dict) else ""
            platforms = [k for k, v in results.items() if v.get("ok")]
            logger.info(
                "Post published successfully — platforms=%s category='%s' image=%s",
                platforms, category,
                f"{len(image_bytes)} bytes" if image_bytes else "none",
            )
        else:
            logger.warning("Post failed on all platforms: %s", results)

    def _publish_news(self, category: str = None):
        if (
            self.last_news_post_time
            and datetime.now(timezone.utc) - self.last_news_post_time < timedelta(hours=3)
        ):
            return
        if news_scraper.post_news(category):
            self.last_post_time = datetime.now(timezone.utc)
            self.last_news_post_time = datetime.now(timezone.utc)
            self._save_state('last_post_time', self.last_post_time.isoformat())
            self._save_state('last_news_post_time', self.last_news_post_time.isoformat())

    # ── Manual controls ────────────────────────────────────────────────────────

    def force_post(self) -> bool:
        """Trigger an immediate content post, bypassing the schedule."""
        try:
            self._job_queue.put(("content", {}), timeout=5)
            return True
        except queue.Full:
            return False

    def post_now(self) -> str:
        """Alias for force_post() — used by OwnerCommands /post slash command."""
        queued = self.force_post()
        return "content post queued" if queued else "queue full — try again shortly"

    def run_content_now(self) -> dict:
        """Synchronously run one content publish cycle and return the result."""
        import time as _time
        from services.gemini import gemini as _gemini
        from services.gemini import RATE_LIMIT_BACKOFF_SECONDS
        elapsed = _time.time() - _gemini._last_rate_limit_time
        if elapsed < RATE_LIMIT_BACKOFF_SECONDS:
            wait_s = int(RATE_LIMIT_BACKOFF_SECONDS - elapsed) + 1
            return {"ok": False, "error": f"Gemini AI is rate-limited — try again in {wait_s}s"}

        if not self._publish_lock.acquire(blocking=False):
            return {"ok": False, "error": "a publish is already in progress — try again shortly"}

        try:
            if self.workflow_factory is not None:
                try:
                    workflow = self.workflow_factory()
                    wf_result = workflow.run()

                    topic = ""
                    publish_step_ok = False
                    publish_results = {}
                    for step in wf_result.steps:
                        if step.step_name == "select_topic" and step.ok:
                            topic = (step.output or {}).get("topic", "")
                        elif step.step_name == "publish":
                            publish_step_ok = step.ok
                            if step.ok:
                                publish_results = step.output if isinstance(step.output, dict) else {}
                            else:
                                logger.warning("run_content_now: publish step failed — %s", step.error)

                    if publish_step_ok:
                        self.last_post_time = datetime.now(timezone.utc)
                        self._save_state("last_post_time", self.last_post_time.isoformat())
                        successful_platforms = [k for k, v in publish_results.items() if v.get("ok")]
                        return {
                            "ok": True, "topic": topic, "platforms": successful_platforms,
                            "results": publish_results, "duration_ms": round(wf_result.total_ms),
                        }

                except Exception as wf_exc:
                    logger.error("run_content_now: workflow raised unexpectedly (%s) — trying legacy path", wf_exc)

            logger.info("run_content_now: running legacy direct-publish path")
            result = content_engine.generate_post()
            caption = result.get("caption", "") if isinstance(result, dict) else (result[0] if result else "")
            image_bytes = result.get("image_bytes") if isinstance(result, dict) else None
            if not caption:
                return {"ok": False, "error": "ContentEngine returned empty caption"}

            results = self._publish_to_all_platforms(caption, image_bytes=image_bytes)
            success = any(v.get("ok") for v in results.values())
            if success:
                self.last_post_time = datetime.now(timezone.utc)
                self._save_state("last_post_time", self.last_post_time.isoformat())
                platforms = [k for k, v in results.items() if v.get("ok")]
                return {"ok": True, "platforms": platforms, "topic": ""}
            return {"ok": False, "error": str(results)}

        except Exception as exc:
            logger.error("run_content_now failed: %s", exc, exc_info=True)
            return {"ok": False, "error": str(exc)}
        finally:
            self._publish_lock.release()

    def post_news_now(self) -> str:
        """Alias for force_news() — used by OwnerCommands /news slash command."""
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

    def get_config(self) -> dict:
        """Return current scheduling configuration."""
        return {
            "content_hours_utc": list(self.content_hours_utc),
            "news_hours_utc": list(self.news_hours_utc),
            "post_interval_hours": getattr(self, "post_interval_hours", None),
            "news_interval_hours": getattr(self, "news_interval_hours", None),
            "enabled": getattr(self, "enabled", True),
        }

    def update_config(self, **kwargs) -> dict:
        """Update scheduling configuration. Returns the complete new config."""
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
                1 for d, h, k in self._fired_slots
                if d == today and k == "content"
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
        }


scheduler = PostScheduler()
