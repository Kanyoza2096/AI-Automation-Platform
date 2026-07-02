"""
Automated Facebook post scheduler v7.0 — Database-Backed
- All state persisted to Supabase
- Producer-consumer architecture
- Rate limiting enforced
- Analytics tracking
"""

import time
import random
import logging
import threading
import queue
from datetime import datetime, timedelta, timezone
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


class PostScheduler:
    """Database-backed post scheduler."""

    def __init__(self, supabase_client=None):
        self.supabase = supabase_client
        self.last_post_time: Optional[datetime] = None
        self.min_interval = timedelta(minutes=45)
        self.last_news_post_time: Optional[datetime] = None
        self.last_engagement_check: Optional[datetime] = None
        self.replied_comments: set = set()
        self.content_hours_utc = settings.content_posting_hours_utc
        self.news_hours_utc = settings.news_posting_hours_utc
        self._job_queue: queue.Queue = queue.Queue(maxsize=MAX_JOB_QUEUE_SIZE)
        self._running = False
        self._lock = threading.Lock()
        self._scheduler_thread: Optional[threading.Thread] = None
        self._worker_thread: Optional[threading.Thread] = None
        self._engagement_thread: Optional[threading.Thread] = None
        self._load_state()

    def _load_state(self):
        if not self.supabase:
            return
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
        except Exception as e:
            logger.error(f"Failed to load scheduler state: {e}")

    def _save_state(self, key: str, value: str):
        if not self.supabase:
            return
        try:
            self.supabase.table('scheduler_state').upsert({
                'key': key, 'value': value,
                'updated_at': datetime.now(timezone.utc).isoformat(),
            }).execute()
        except Exception as e:
            logger.error(f"Failed to save scheduler state: {e}")

    def start(self):
        if self._running:
            return
        self._running = True
        self._scheduler_thread = threading.Thread(target=self._run, daemon=False, name="scheduler-producer")
        self._worker_thread = threading.Thread(target=self._worker, daemon=False, name="scheduler-worker")
        self._engagement_thread = threading.Thread(target=self._engagement_loop, daemon=False, name="engagement-loop")
        self._scheduler_thread.start()
        self._worker_thread.start()
        self._engagement_thread.start()
        logger.info(f"Scheduler started. Content: {self.content_hours_utc} UTC")

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

    def _run(self):
        time.sleep(30)
        while self._running:
            try:
                self._check_and_enqueue()
            except Exception as e:
                logger.error(f"Scheduler error: {e}")
            for _ in range(60):
                if not self._running:
                    break
                time.sleep(5)

    def _check_and_enqueue(self):
        now = datetime.now(timezone.utc)
        if self.last_post_time and (now - self.last_post_time) < self.min_interval:
            return
        if now.hour in self.content_hours_utc and 20 <= now.minute <= 40:
            if random.random() < 0.7 and rate_limiter.is_allowed('scheduler', 'post_per_hour'):
                self._enqueue_job("content")
        if now.hour in self.news_hours_utc and 20 <= now.minute <= 40:
            if random.random() < 0.5:
                category = "technology" if now.hour == 6 else ("malawi" if now.hour == 13 else None)
                self._enqueue_job("news", category=category)

    def _enqueue_job(self, job_type: str, **kwargs):
        try:
            self._job_queue.put((job_type, kwargs), timeout=10)
        except queue.Full:
            logger.warning(f"Job queue full, dropping: {job_type}")

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
                logger.error(f"Worker error: {e}")
            finally:
                self._job_queue.task_done()

    def _engagement_loop(self):
        time.sleep(120)
        while self._running:
            try:
                self._process_engagement()
            except Exception as e:
                logger.error(f"Engagement error: {e}")
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
                result = self.supabase.table('replied_comments').select('id').eq('comment_id', comment_id).maybe_single().execute()
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
                logger.error(f"Failed to save replied comment: {e}")

    def _generate_comment_reply(self, comment: str, username: str) -> Optional[str]:
        comment_lower = comment.lower()
        if "?" in comment and len(comment) > 20:
            return gemini.generate_comment_reply(comment=comment, username=username)
        if any(w in comment_lower for w in ["thanks", "thank you", "helpful", "great"]):
            return random.choice([f"Glad it helped, {username}!", f"Thanks {username}!"])
        if len(comment.split()) <= 3:
            return random.choice(["👍", f"Thanks {username}!"])
        return random.choice([f"Good point, {username}!", f"Interesting take, {username}!"])

    def _publish_content(self):
        caption, image_bytes = content_engine.generate_post()
        if caption:
            success = facebook.post_to_page(caption, image_bytes=image_bytes)
            if success:
                self.last_post_time = datetime.now(timezone.utc)
                self._save_state('last_post_time', self.last_post_time.isoformat())

    def _publish_news(self, category: str = None):
        if self.last_news_post_time and datetime.now(timezone.utc) - self.last_news_post_time < timedelta(hours=3):
            return
        if news_scraper.post_news(category):
            self.last_post_time = datetime.now(timezone.utc)
            self.last_news_post_time = datetime.now(timezone.utc)
            self._save_state('last_post_time', self.last_post_time.isoformat())
            self._save_state('last_news_post_time', self.last_news_post_time.isoformat())

    def force_post(self) -> bool:
        try:
            self._job_queue.put(("content", {}), timeout=5)
            return True
        except queue.Full:
            return False

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

    def get_status(self) -> dict:
        return {
            "last_post": str(self.last_post_time) if self.last_post_time else None,
            "last_news_post": str(self.last_news_post_time) if self.last_news_post_time else None,
            "last_engagement": str(self.last_engagement_check) if self.last_engagement_check else None,
            "content_hours_utc": self.content_hours_utc,
            "news_hours_utc": self.news_hours_utc,
            "queue_size": self._job_queue.qsize(),
            "running": self._running,
        }


scheduler = PostScheduler()
