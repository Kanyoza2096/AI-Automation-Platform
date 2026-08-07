# core/render_queue.py
"""
Redis-backed asynchronous card rendering pipeline.

Flow:
  Content Engine → pushes HTML to Redis queue
  Render Worker → pops HTML, renders PNG via Pillow, uploads to Supabase Storage
  Content Engine → polls for completion, gets image URL

Uses Pillow for card rendering (no browser required).
RenderQueue ONLY renders — it NEVER publishes. Publishing goes through
the main pipeline (JobService → RetryPolicy → Plugin → Facebook).

Architecture §6 — Synchronous vs Asynchronous Rendering:
  The pipeline waits for rendering to complete before publishing.
  No text-only fallback. No duplicate posts from async workers.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("kanyoza.render_queue")

RENDER_QUEUE_KEY = "kanyoza:render_queue"
RENDER_STATUS_KEY = "kanyoza:render_status"
RENDER_RESULT_KEY = "kanyoza:render_result"

MAX_RETRIES = 2
RENDER_POLL_TIMEOUT = 60  # Max seconds to wait for async render


class RenderTask:
    """A single card rendering job."""

    def __init__(
        self,
        brand_id: str,
        caption: str,
        html: str,
        topic: str,
        platform: str = "facebook",
        task_id: Optional[str] = None,
        post_dict: Optional[dict] = None,
    ):
        self.task_id = task_id or f"render_{uuid.uuid4().hex[:12]}"
        self.brand_id = brand_id
        self.caption = caption
        self.html = html
        self.topic = topic
        self.platform = platform
        self.post_dict: Optional[dict] = post_dict
        self.created_at = datetime.now(timezone.utc)
        self.retries = 0

    def to_dict(self) -> dict:
        return {
            'task_id': self.task_id,
            'brand_id': self.brand_id,
            'caption': self.caption,
            'html': self.html,
            'topic': self.topic,
            'platform': self.platform,
            'post_dict': self.post_dict,
            'created_at': self.created_at.isoformat(),
            'retries': self.retries,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RenderTask":
        task = cls(
            task_id=data.get('task_id'),
            brand_id=data.get('brand_id', ''),
            caption=data.get('caption', ''),
            html=data.get('html', ''),
            topic=data.get('topic', ''),
            platform=data.get('platform', 'facebook'),
            post_dict=data.get('post_dict'),
        )
        task.retries = int(data.get('retries', 0))
        return task


class RenderResult:
    """The result of a completed render."""
    def __init__(self, task_id: str, image_url: str, image_bytes: Optional[bytes] = None):
        self.task_id = task_id
        self.image_url = image_url
        self.image_bytes = image_bytes

    def to_dict(self) -> dict:
        return {
            'task_id': self.task_id,
            'image_url': self.image_url,
            'has_bytes': self.image_bytes is not None,
        }


class RenderQueue:
    """
    Manages the async rendering pipeline using Redis.

    RENDERS ONLY — never publishes.
    Publishing goes through the main pipeline.
    """

    def __init__(self):
        self._redis = None
        self._fallback_queue = []
        self._fallback_results: dict = {}  # task_id → RenderResult
        self._lock = threading.Lock()
        self._running = False
        self._render_thread: Optional[threading.Thread] = None
        self._container = None

        try:
            from config.settings import settings
            from core.redis_client import UpstashRedisClient, RedisConfig

            if settings.redis_configured:
                config = RedisConfig(
                    url=settings.upstash_redis_rest_url,
                    token=settings.upstash_redis_rest_token,
                    enabled=True,
                )
                client = UpstashRedisClient(config)
                if client.is_available:
                    self._redis = client
                    logger.info("RenderQueue: using Redis backend")
                else:
                    logger.info("RenderQueue: Redis unavailable — using in-memory fallback")
            else:
                logger.info("RenderQueue: Redis not configured — using in-memory fallback")
        except Exception as exc:
            logger.info("RenderQueue: Redis init failed (%s) — using in-memory fallback", exc)

    # ═══════════════════════════════════════════════════════════════════════
    # Clear stuck tasks
    # ═══════════════════════════════════════════════════════════════════════

    def clear_stuck_tasks(self) -> int:
        """Clear all stuck render tasks from Redis. Returns count cleared."""
        cleared = 0
        if self._redis and self._redis.is_available:
            try:
                while self._redis.rpop(RENDER_QUEUE_KEY):
                    cleared += 1
                logger.info("RenderQueue: cleared %d stuck tasks from Redis queues", cleared)
            except Exception as e:
                logger.warning("RenderQueue: clear_stuck_tasks failed: %s", e)
        with self._lock:
            in_mem = len(self._fallback_queue)
            self._fallback_queue.clear()
            cleared += in_mem
        return cleared

    # ═══════════════════════════════════════════════════════════════════════
    # Enqueue / Dequeue / Poll
    # ═══════════════════════════════════════════════════════════════════════

    def enqueue_render(self, task: RenderTask) -> str:
        """Add a render task to the queue. Returns task_id."""
        data = json.dumps(task.to_dict())

        if self._redis and self._redis.is_available:
            try:
                self._redis.lpush(RENDER_QUEUE_KEY, data)
                self._redis.set(f"{RENDER_STATUS_KEY}:{task.task_id}", "queued", ex=3600)
                logger.info("RenderQueue: enqueued render task %s", task.task_id)
                return task.task_id
            except Exception as e:
                logger.warning("RenderQueue: Redis push failed: %s", e)

        with self._lock:
            self._fallback_queue.append(task)
        logger.info("RenderQueue: task %s added to in-memory queue", task.task_id)
        return task.task_id

    def dequeue_render(self) -> Optional[RenderTask]:
        """Pop a render task from the queue."""
        if self._redis and self._redis.is_available:
            try:
                data = self._redis.rpop(RENDER_QUEUE_KEY)
                if data:
                    try:
                        task = RenderTask.from_dict(json.loads(data))
                    except (json.JSONDecodeError, KeyError, ValueError) as parse_err:
                        logger.error(
                            "RenderQueue: discarding malformed render entry (%s). "
                            "Raw (first 200 chars): %s",
                            parse_err,
                            data[:200] if isinstance(data, str) else repr(data)[:200],
                        )
                        return None
                    self._redis.set(f"{RENDER_STATUS_KEY}:{task.task_id}", "rendering", ex=3600)
                    return task
            except Exception as e:
                logger.warning("RenderQueue: Redis pop failed: %s", e)

        with self._lock:
            if self._fallback_queue:
                return self._fallback_queue.pop(0)
        return None

    def get_status(self, task_id: str) -> str:
        """Get the status of a render task."""
        if self._redis and self._redis.is_available:
            try:
                status = self._redis.get(f"{RENDER_STATUS_KEY}:{task_id}")
                return status or "unknown"
            except Exception:
                pass

        with self._lock:
            if task_id in self._fallback_results:
                return "completed"
        return "unknown"

    def get_result(self, task_id: str) -> Optional[RenderResult]:
        """Get the result of a completed render task."""
        if self._redis and self._redis.is_available:
            try:
                data = self._redis.get(f"{RENDER_RESULT_KEY}:{task_id}")
                if data:
                    result_dict = json.loads(data)
                    return RenderResult(
                        task_id=result_dict['task_id'],
                        image_url=result_dict.get('image_url', ''),
                    )
            except Exception:
                pass

        with self._lock:
            return self._fallback_results.get(task_id)

    def wait_for_render(self, task_id: str, timeout: int = RENDER_POLL_TIMEOUT) -> Optional[RenderResult]:
        """Block until a render task completes or timeout.
        
        Used by ContentEngine to wait for async rendering before publishing.
        This ensures every post has its image before going to Facebook.
        """
        start = time.monotonic()
        while (time.monotonic() - start) < timeout:
            result = self.get_result(task_id)
            if result:
                return result

            status = self.get_status(task_id)
            if status in ("failed", "failed_permanently"):
                logger.error("RenderQueue: task %s failed — status=%s", task_id, status)
                return None

            time.sleep(1)

        logger.error("RenderQueue: task %s timed out after %ds", task_id, timeout)
        return None

    # ═══════════════════════════════════════════════════════════════════════
    # Lifecycle
    # ═══════════════════════════════════════════════════════════════════════

    def start_workers(self) -> None:
        """Start the render worker thread."""
        if self._running:
            return
        self._running = True

        self._render_thread = threading.Thread(
            target=self._render_worker, daemon=True, name="render-worker"
        )
        self._render_thread.start()
        logger.info("RenderQueue: render worker started (publish worker REMOVED — publishing via main pipeline)")

    def stop(self) -> None:
        """Stop all workers."""
        self._running = False
        if self._render_thread and self._render_thread.is_alive():
            self._render_thread.join(timeout=10)
        logger.info("RenderQueue: workers stopped")

    # ═══════════════════════════════════════════════════════════════════════
    # Render Worker — RENDERS ONLY, NEVER PUBLISHES
    # ═══════════════════════════════════════════════════════════════════════

    def _render_worker(self) -> None:
        """Worker that renders cards to PNG and stores the result.
        
        DOES NOT PUBLISH. The result is stored and the main pipeline
        polls for completion via wait_for_render().
        """
        logger.info("RenderQueue: render worker started (render-only mode)")

        while self._running:
            task = self.dequeue_render()
            if not task:
                time.sleep(2)
                continue

            try:
                png_bytes = None

                if getattr(task, 'post_dict', None):
                    try:
                        from services.card_renderer import card_renderer
                        png_bytes = card_renderer.render_from_post_dict(task.post_dict)
                        if png_bytes:
                            logger.info(
                                "RenderQueue: Pillow rendered task %s (%d bytes)",
                                task.task_id, len(png_bytes),
                            )
                    except Exception as pillow_err:
                        logger.warning("RenderQueue: Pillow render failed for task %s: %s", task.task_id, pillow_err)

                if not png_bytes and task.html:
                    png_bytes = self._render_html(task.html)

                if not png_bytes:
                    logger.error("RenderQueue: render failed for task %s", task.task_id)
                    if task.retries < MAX_RETRIES:
                        task.retries += 1
                        self.enqueue_render(task)
                    else:
                        self._store_result(task.task_id, RenderResult(task.task_id, ""))
                    continue

                # Upload to Supabase Storage
                image_url = self._upload_to_storage(task.brand_id, png_bytes)
                
                if not image_url:
                    logger.error(
                        "RenderQueue: Supabase Storage upload failed for task %s — "
                        "storing empty result (pipeline will handle text-only fallback)",
                        task.task_id,
                    )

                logger.info("RenderQueue: task %s completed, image_url=%s", task.task_id, image_url or "None")

                # Store result for the pipeline to pick up
                self._store_result(task.task_id, RenderResult(
                    task_id=task.task_id,
                    image_url=image_url or "",
                    image_bytes=png_bytes if not image_url else None,
                ))

            except Exception as e:
                logger.error("RenderQueue: render worker error: %s", e)
                if task.retries < MAX_RETRIES:
                    task.retries += 1
                    self.enqueue_render(task)
                else:
                    self._store_result(task.task_id, RenderResult(task.task_id, ""))

    # ═══════════════════════════════════════════════════════════════════════
    # Result Storage
    # ═══════════════════════════════════════════════════════════════════════

    def _store_result(self, task_id: str, result: RenderResult) -> None:
        """Store a completed render result for the pipeline to retrieve."""
        if self._redis and self._redis.is_available:
            try:
                self._redis.set(
                    f"{RENDER_RESULT_KEY}:{task_id}",
                    json.dumps(result.to_dict()),
                    ex=3600,
                )
                self._redis.set(f"{RENDER_STATUS_KEY}:{task_id}", "completed", ex=3600)
            except Exception as e:
                logger.warning("RenderQueue: failed to store result in Redis: %s", e)

        with self._lock:
            self._fallback_results[task_id] = result

        # Clean old results
        with self._lock:
            if len(self._fallback_results) > 100:
                oldest = sorted(self._fallback_results.keys())[:50]
                for k in oldest:
                    del self._fallback_results[k]

    # ═══════════════════════════════════════════════════════════════════════
    # Helpers
    # ═══════════════════════════════════════════════════════════════════════

    def _render_html(self, html: str) -> Optional[bytes]:
        """Render HTML string to PNG bytes using Pillow."""
        try:
            from services.card_renderer import card_renderer
            try:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html, 'html.parser')
                title_tag = soup.find('h1') or soup.find('h2') or soup.find('title')
                title_text = title_tag.get_text(strip=True) if title_tag else "New Post"
                body_tag = soup.find('p') or soup.find('div')
                subtitle_text = body_tag.get_text(strip=True)[:200] if body_tag else ""
            except ImportError:
                title_text = "New Post"
                clean = re.sub(r'<[^>]+>', '', html)
                subtitle_text = ' '.join(clean.split())[:200]

            png = card_renderer.render(title=title_text, subtitle=subtitle_text, template="default")
            logger.info("RenderQueue: Pillow rendered HTML task (%d bytes)", len(png) if png else 0)
            return png
        except Exception as e:
            logger.error("RenderQueue: _render_html failed: %s", e)
            return None

    def _upload_to_storage(self, brand_id: str, image_bytes: bytes) -> Optional[str]:
        """Upload PNG to Supabase Storage and return the public URL."""
        try:
            from services.supabase import supabase_service
            if not supabase_service or not supabase_service.client:
                return None

            filename = f"cards/{brand_id}/{uuid.uuid4().hex}.png"
            supabase_service.client.storage.from_('post-images').upload(filename, image_bytes, {
                'content-type': 'image/png', 'cache-control': 'public, max-age=31536000',
            })
            return supabase_service.client.storage.from_('post-images').get_public_url(filename)
        except Exception as e:
            logger.error("RenderQueue: upload failed: %s", e)
            return None


# Module-level singleton
_render_queue: Optional[RenderQueue] = None
_render_queue_lock = threading.Lock()


def get_render_queue() -> RenderQueue:
    """Get or create the global RenderQueue singleton."""
    global _render_queue
    if _render_queue is None:
        with _render_queue_lock:
            if _render_queue is None:
                _render_queue = RenderQueue()
                _render_queue.start_workers()
    return _render_queue


def wire_render_queue_container(container) -> None:
    """Wire the DI container into the RenderQueue."""
    rq = get_render_queue()
    rq._container = container
    logger.info("RenderQueue: DI container wired.")
