# core/render_queue.py — NEW FILE
"""
Redis-backed asynchronous card rendering pipeline.

Flow:
  Content Engine → pushes HTML to Redis queue
  Browser Worker → pops HTML, renders PNG, uploads to Supabase Storage
  Publisher Worker → pops completed render, posts to Facebook

This decouples rendering from content generation so slow Playwright
renders never block the scheduler or timeout the pipeline.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("kanyoza.render_queue")

RENDER_QUEUE_KEY = "kanyoza:render_queue"
PUBLISH_QUEUE_KEY = "kanyoza:publish_queue"
RENDER_STATUS_KEY = "kanyoza:render_status"  # Hash: task_id → status

MAX_RETRIES = 2


class RenderTask:
    """A single card rendering job."""
    
    def __init__(self, brand_id: str, caption: str, html: str, topic: str, 
                 platform: str = "facebook", task_id: Optional[str] = None):
        self.task_id = task_id or f"render_{uuid.uuid4().hex[:12]}"
        self.brand_id = brand_id
        self.caption = caption
        self.html = html
        self.topic = topic
        self.platform = platform
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.retries = 0
    
    def to_dict(self) -> dict:
        return {
            'task_id': self.task_id,
            'brand_id': self.brand_id,
            'caption': self.caption,
            'html': self.html,
            'topic': self.topic,
            'platform': self.platform,
            'created_at': self.created_at.isoformat(),
            'retries': self.retries,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "RenderTask":
        return cls(
            task_id=data.get('task_id'),
            brand_id=data.get('brand_id', ''),
            caption=data.get('caption', ''),
            html=data.get('html', ''),
            topic=data.get('topic', ''),
            platform=data.get('platform', 'facebook'),
        )


class PublishTask:
    """A completed render ready for publishing."""
    
    def __init__(self, task_id: str, brand_id: str, caption: str, 
                 image_url: str, topic: str, platform: str = "facebook"):
        self.task_id = task_id
        self.brand_id = brand_id
        self.caption = caption
        self.image_url = image_url
        self.topic = topic
        self.platform = platform
    
    def to_dict(self) -> dict:
        return {
            'task_id': self.task_id,
            'brand_id': self.brand_id,
            'caption': self.caption,
            'image_url': self.image_url,
            'topic': self.topic,
            'platform': self.platform,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "PublishTask":
        return cls(
            task_id=data.get('task_id', ''),
            brand_id=data.get('brand_id', ''),
            caption=data.get('caption', ''),
            image_url=data.get('image_url', ''),
            topic=data.get('topic', ''),
            platform=data.get('platform', 'facebook'),
        )


class RenderQueue:
    """
    Manages the async rendering pipeline using Redis.
    
    If Redis is unavailable, falls back to in-memory rendering
    (synchronous, no queuing — same as current behavior).
    """
    
    def __init__(self):
        self._redis = None
        self._fallback_queue = []  # In-memory fallback
        self._lock = threading.Lock()
        self._running = False
        self._render_thread: Optional[threading.Thread] = None
        self._publish_thread: Optional[threading.Thread] = None
        
        # Try to get Redis
        try:
            from flask import current_app
            container = current_app.extensions.get("service_container")
            if container:
                from core.redis_client import UpstashRedisClient
                self._redis = container.try_resolve(UpstashRedisClient)
                if self._redis and self._redis.is_available:
                    logger.info("RenderQueue: using Redis backend")
                else:
                    logger.info("RenderQueue: Redis unavailable — using in-memory fallback")
                    self._redis = None
        except Exception:
            logger.info("RenderQueue: Redis unavailable — using in-memory fallback")
    
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
        
        # Fallback: render synchronously
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
                    task = RenderTask.from_dict(json.loads(data))
                    self._redis.set(f"{RENDER_STATUS_KEY}:{task.task_id}", "rendering", ex=3600)
                    return task
            except Exception as e:
                logger.warning("RenderQueue: Redis pop failed: %s", e)
        
        # Fallback
        with self._lock:
            if self._fallback_queue:
                return self._fallback_queue.pop(0)
        return None
    
    def enqueue_publish(self, task: PublishTask) -> None:
        """Add a completed render to the publish queue."""
        data = json.dumps(task.to_dict())
        
        if self._redis and self._redis.is_available:
            try:
                self._redis.lpush(PUBLISH_QUEUE_KEY, data)
                self._redis.set(f"{RENDER_STATUS_KEY}:{task.task_id}", "ready_to_publish", ex=3600)
                logger.info("RenderQueue: enqueued publish task %s", task.task_id)
                return
            except Exception:
                pass
        
        # Fallback: publish immediately
        self._publish_immediate(task)
    
    def dequeue_publish(self) -> Optional[PublishTask]:
        """Pop a publish task from the queue."""
        if self._redis and self._redis.is_available:
            try:
                data = self._redis.rpop(PUBLISH_QUEUE_KEY)
                if data:
                    return PublishTask.from_dict(json.loads(data))
            except Exception:
                pass
        
        return None
    
    def get_status(self, task_id: str) -> str:
        """Get the status of a render task."""
        if self._redis and self._redis.is_available:
            try:
                status = self._redis.get(f"{RENDER_STATUS_KEY}:{task_id}")
                return status or "unknown"
            except Exception:
                pass
        return "unknown"
    
    def _publish_immediate(self, task: PublishTask) -> None:
        """Publish immediately (fallback when Redis is down)."""
        try:
            from services.facebook import facebook
            facebook.post_to_page(
                message=task.caption,
                image_url=task.image_url,
            )
            logger.info("RenderQueue: immediate publish for task %s", task.task_id)
        except Exception as e:
            logger.error("RenderQueue: immediate publish failed: %s", e)
    
    def start_workers(self) -> None:
        """Start the render and publish worker threads."""
        if self._running:
            return
        self._running = True
        
        self._render_thread = threading.Thread(
            target=self._render_worker, daemon=True, name="render-worker"
        )
        self._publish_thread = threading.Thread(
            target=self._publish_worker, daemon=True, name="publish-worker"
        )
        self._render_thread.start()
        self._publish_thread.start()
        logger.info("RenderQueue: workers started")
    
    def stop(self) -> None:
        """Stop all workers."""
        self._running = False
        for t in [self._render_thread, self._publish_thread]:
            if t and t.is_alive():
                t.join(timeout=10)
        logger.info("RenderQueue: workers stopped")
    
    def _render_worker(self) -> None:
        """Worker that renders HTML to PNG and uploads to Supabase Storage."""
        logger.info("RenderQueue: render worker started")
        
        while self._running:
            task = self.dequeue_render()
            if not task:
                time.sleep(2)
                continue
            
            try:
                # Render HTML to PNG
                png_bytes = self._render_html(task.html)
                if not png_bytes:
                    logger.error("RenderQueue: render failed for task %s", task.task_id)
                    if task.retries < MAX_RETRIES:
                        task.retries += 1
                        self.enqueue_render(task)
                    continue
                
                # Upload to Supabase Storage
                image_url = self._upload_to_storage(task.brand_id, png_bytes)
                if not image_url:
                    # If upload fails, try base64 data URL as fallback
                    import base64
                    image_url = f"data:image/png;base64,{base64.b64encode(png_bytes).decode()}"
                
                logger.info("RenderQueue: task %s rendered and uploaded (%d bytes)", 
                           task.task_id, len(png_bytes))
                
                # Queue for publishing
                self.enqueue_publish(PublishTask(
                    task_id=task.task_id,
                    brand_id=task.brand_id,
                    caption=task.caption,
                    image_url=image_url,
                    topic=task.topic,
                    platform=task.platform,
                ))
                
            except Exception as e:
                logger.error("RenderQueue: render worker error: %s", e)
                if task.retries < MAX_RETRIES:
                    task.retries += 1
                    self.enqueue_render(task)
    
    def _publish_worker(self) -> None:
        """Worker that publishes completed renders."""
        logger.info("RenderQueue: publish worker started")
        
        while self._running:
            task = self.dequeue_publish()
            if not task:
                time.sleep(2)
                continue
            
            try:
                from services.facebook import facebook
                success = facebook.post_to_page(
                    message=task.caption,
                    image_url=task.image_url,
                )
                if success:
                    logger.info("RenderQueue: published task %s", task.task_id)
                    if self._redis and self._redis.is_available:
                        self._redis.set(f"{RENDER_STATUS_KEY}:{task.task_id}", "published", ex=3600)
                else:
                    logger.warning("RenderQueue: publish failed for task %s — requeuing", task.task_id)
                    self.enqueue_publish(task)  # Requeue
                    time.sleep(10)
                    
            except Exception as e:
                logger.error("RenderQueue: publish worker error: %s", e)
                self.enqueue_publish(task)  # Requeue on error
                time.sleep(10)
    
    def _render_html(self, html: str) -> Optional[bytes]:
        """Render HTML string to PNG bytes using Playwright."""
        try:
            from services.card_renderer import card_renderer
            # Use the card renderer's Playwright instance
            png = card_renderer.render_html_string(html)
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
            
            result = supabase_service.client.storage \
                .from_('post-images') \
                .upload(filename, image_bytes, {
                    'content-type': 'image/png',
                    'cache-control': 'public, max-age=31536000',
                })
            
            # Get public URL
            url = supabase_service.client.storage \
                .from_('post-images') \
                .get_public_url(filename)
            
            return url
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
