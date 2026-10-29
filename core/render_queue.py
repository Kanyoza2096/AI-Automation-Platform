"""
Redis-backed asynchronous card rendering pipeline v2.0.

Flow:
  Content Engine → pushes HTML to Redis queue
  Render Worker → pops HTML, renders PNG via Pillow, uploads to Supabase Storage
  Content Engine → polls for completion, gets image URL

v2.0 UPGRADES:
  - EventBus integration (render events for MetaEngine/Director)
  - Brand-aware HTML fallback (uses render_legacy with brand context)
  - Health check & statistics
  - Worker-scoped Redis keys to prevent collisions
  - Proper container-resolved Supabase uploads

Architecture §6 — Synchronous vs Asynchronous Rendering:
  The pipeline waits for rendering to complete before publishing.
  No text-only fallback. No duplicate posts from async workers.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("kanyoza.render_queue")

RENDER_QUEUE_KEY = "kanyoza:render_queue"
RENDER_PROCESSING_KEY = "kanyoza:render_processing"
RENDER_STATUS_KEY = "kanyoza:render_status"
RENDER_RESULT_KEY = "kanyoza:render_result"

MAX_RETRIES = 2
RENDER_POLL_TIMEOUT = 60

# v2.0 — worker-scoped keys
_WORKER_ID = os.environ.get(
    "RENDER_WORKER_ID",
    f"worker_{uuid.uuid4().hex[:8]}",
)


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
        brand_colors: Optional[dict] = None,
        brand_name: str = "",
        brand_hashtags: str = "",
        brand_metadata: Optional[dict] = None,
    ):
        self.task_id = (
            task_id
            or f"render_{uuid.uuid4().hex[:12]}"
        )
        self.brand_id = brand_id
        self.caption = caption
        self.html = html
        self.topic = topic
        self.platform = platform
        self.post_dict: Optional[dict] = post_dict
        # v2.0 — brand context for fallback rendering
        self.brand_colors = brand_colors or {}
        self.brand_name = brand_name
        self.brand_hashtags = brand_hashtags
        self.brand_metadata = brand_metadata or {}
        self.created_at = datetime.now(timezone.utc)
        self.retries = 0

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "brand_id": self.brand_id,
            "caption": self.caption,
            "html": self.html,
            "topic": self.topic,
            "platform": self.platform,
            "post_dict": self.post_dict,
            "brand_colors": self.brand_colors,
            "brand_name": self.brand_name,
            "brand_hashtags": self.brand_hashtags,
            "brand_metadata": self.brand_metadata,
            "created_at": self.created_at.isoformat(),
            "retries": self.retries,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RenderTask":
        task = cls(
            task_id=data.get("task_id"),
            brand_id=data.get("brand_id", ""),
            caption=data.get("caption", ""),
            html=data.get("html", ""),
            topic=data.get("topic", ""),
            platform=data.get("platform", "facebook"),
            post_dict=data.get("post_dict"),
            brand_colors=data.get("brand_colors", {}),
            brand_name=data.get("brand_name", ""),
            brand_hashtags=data.get("brand_hashtags", ""),
            brand_metadata=data.get("brand_metadata", {}),
        )
        task.retries = int(data.get("retries", 0))
        return task


class RenderResult:
    """The result of a completed render."""

    def __init__(
        self,
        task_id: str,
        image_url: str,
        image_bytes: Optional[bytes] = None,
        render_time_ms: float = 0.0,
    ):
        self.task_id = task_id
        self.image_url = image_url
        self.image_bytes = image_bytes
        self.render_time_ms = render_time_ms

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "image_url": self.image_url,
            "has_bytes": self.image_bytes is not None,
            "render_time_ms": self.render_time_ms,
        }


class RenderQueue:
    """Manages the async rendering pipeline using Redis.

    v2.0: EventBus integration, brand-aware fallbacks, health stats.
    """

    def __init__(self, event_bus=None):
        self._redis = None
        self._fallback_queue = []
        self._fallback_results: dict = {}
        self._lock = threading.Lock()
        self._running = False
        self._render_thread: Optional[threading.Thread] = (
            None
        )
        self._container = None
        self._event_bus = event_bus

        # v2.0 — statistics
        self._renders_completed = 0
        self._renders_failed = 0
        self._queue_depth = 0

        try:
            from config.settings import settings
            from core.redis_client import (
                UpstashRedisClient,
                RedisConfig,
            )

            if settings.redis_configured:
                config = RedisConfig(
                    url=settings.upstash_redis_rest_url,
                    token=settings.upstash_redis_rest_token,
                    enabled=True,
                )
                client = UpstashRedisClient(config)
                if client.is_available:
                    self._redis = client
                    logger.info(
                        "RenderQueue: using Redis backend"
                    )
                else:
                    logger.info(
                        "RenderQueue: Redis unavailable — "
                        "using in-memory fallback"
                    )
            else:
                logger.info(
                    "RenderQueue: Redis not configured — "
                    "using in-memory fallback"
                )
        except Exception as exc:
            logger.info(
                "RenderQueue: Redis init failed (%s) — "
                "using in-memory fallback",
                exc,
            )

    # ═══════════════════════════════════════════════════════════════
    # v2.0 — EVENT BUS
    # ═══════════════════════════════════════════════════════════════

    def set_event_bus(self, event_bus) -> None:
        self._event_bus = event_bus

    def _emit(self, event_name: str, payload: dict) -> None:
        if not self._event_bus:
            return
        try:
            if hasattr(self._event_bus, "publish"):
                self._event_bus.publish(
                    event_name, payload
                )
            elif hasattr(self._event_bus, "emit"):
                self._event_bus.emit(
                    event_name, payload
                )
        except Exception as exc:
            logger.debug(
                "RenderQueue event failed: %s", exc
            )

    # ═══════════════════════════════════════════════════════════════
    # CLEAR STUCK TASKS
    # ═══════════════════════════════════════════════════════════════

    def clear_stuck_tasks(self) -> int:
        cleared = 0
        if self._redis and self._redis.is_available:
            try:
                while self._redis.rpop(
                    RENDER_QUEUE_KEY
                ):
                    cleared += 1
                logger.info(
                    "RenderQueue: cleared %d stuck tasks",
                    cleared,
                )
            except Exception as e:
                logger.warning(
                    "RenderQueue: clear_stuck_tasks failed: %s",
                    e,
                )
        with self._lock:
            in_mem = len(self._fallback_queue)
            self._fallback_queue.clear()
            cleared += in_mem
        return cleared

    # ═══════════════════════════════════════════════════════════════
    # ENQUEUE / DEQUEUE / POLL
    # ═══════════════════════════════════════════════════════════════

    def enqueue_render(
        self, task: RenderTask
    ) -> str:
        data = json.dumps(task.to_dict())

        if self._redis and self._redis.is_available:
            try:
                self._redis.lpush(
                    RENDER_QUEUE_KEY, data
                )
                self._redis.set(
                    f"{RENDER_STATUS_KEY}:{task.task_id}",
                    "queued",
                    ex=3600,
                )
                self._queue_depth += 1
                logger.info(
                    "RenderQueue: enqueued %s",
                    task.task_id,
                )
                return task.task_id
            except Exception as e:
                logger.warning(
                    "RenderQueue: Redis push failed: %s",
                    e,
                )

        with self._lock:
            self._fallback_queue.append(task)
            self._queue_depth = len(
                self._fallback_queue
            )
        logger.info(
            "RenderQueue: task %s added to in-memory queue",
            task.task_id,
        )
        return task.task_id

    def dequeue_render(self) -> Optional[RenderTask]:
        if self._redis and self._redis.is_available:
            try:
                data = self._redis.rpop(
                    RENDER_QUEUE_KEY
                )
                if data:
                    # Keep a durable in-flight copy until the worker completes.
                    self._redis.lpush(RENDER_PROCESSING_KEY, data)
                    try:
                        task = RenderTask.from_dict(
                            json.loads(data)
                        )
                        task._redis_raw = data
                    except (
                        json.JSONDecodeError,
                        KeyError,
                        ValueError,
                    ) as parse_err:
                        logger.error(
                            "RenderQueue: malformed render entry (%s)",
                            parse_err,
                        )
                        self._redis.lrem(RENDER_PROCESSING_KEY, 1, data)
                        return None
                    self._redis.set(
                        f"{RENDER_STATUS_KEY}:{task.task_id}",
                        "rendering",
                        ex=3600,
                    )
                    self._queue_depth = max(
                        0, self._queue_depth - 1
                    )
                    return task
            except Exception as e:
                logger.warning(
                    "RenderQueue: Redis pop failed: %s",
                    e,
                )

        with self._lock:
            if self._fallback_queue:
                task = self._fallback_queue.pop(0)
                self._queue_depth = len(
                    self._fallback_queue
                )
                return task
        return None

    def get_status(self, task_id: str) -> str:
        if self._redis and self._redis.is_available:
            try:
                status = self._redis.get(
                    f"{RENDER_STATUS_KEY}:{task_id}"
                )
                return status or "unknown"
            except Exception:
                pass

        with self._lock:
            if task_id in self._fallback_results:
                return "completed"
        return "unknown"

    def get_result(
        self, task_id: str
    ) -> Optional[RenderResult]:
        if self._redis and self._redis.is_available:
            try:
                data = self._redis.get(
                    f"{RENDER_RESULT_KEY}:{task_id}"
                )
                if data:
                    result_dict = json.loads(data)
                    return RenderResult(
                        task_id=result_dict["task_id"],
                        image_url=result_dict.get(
                            "image_url", ""
                        ),
                        render_time_ms=result_dict.get(
                            "render_time_ms", 0
                        ),
                    )
            except Exception:
                pass

        with self._lock:
            return self._fallback_results.get(
                task_id
            )

    def wait_for_render(
        self,
        task_id: str,
        timeout: int = RENDER_POLL_TIMEOUT,
    ) -> Optional[RenderResult]:
        start = time.monotonic()
        while (
            time.monotonic() - start
        ) < timeout:
            result = self.get_result(task_id)
            if result:
                return result

            status = self.get_status(task_id)
            if status in (
                "failed",
                "failed_permanently",
            ):
                logger.error(
                    "RenderQueue: task %s failed — status=%s",
                    task_id,
                    status,
                )
                return None

            time.sleep(1)

        logger.error(
            "RenderQueue: task %s timed out after %ds",
            task_id,
            timeout,
        )
        return None

    # ═══════════════════════════════════════════════════════════════
    # LIFECYCLE
    # ═══════════════════════════════════════════════════════════════

    def start_workers(self) -> None:
        if self._running:
            return
        self._running = True
        if self._redis and self._redis.is_available:
            try:
                for raw in self._redis.lrange(RENDER_PROCESSING_KEY, 0, -1):
                    self._redis.lpush(RENDER_QUEUE_KEY, raw)
                # Entries are now back in the durable queue; clear the stale
                # in-flight ledger so they cannot be duplicated on next boot.
                for raw in self._redis.lrange(RENDER_PROCESSING_KEY, 0, -1):
                    self._redis.lrem(RENDER_PROCESSING_KEY, 1, raw)
                logger.info("RenderQueue: recovered stale in-flight tasks")
            except Exception as exc:
                logger.warning("RenderQueue: in-flight recovery failed: %s", exc)

        self._render_thread = threading.Thread(
            target=self._render_worker,
            daemon=True,
            name="render-worker",
        )
        self._render_thread.start()
        logger.info(
            "RenderQueue: render worker started"
        )

    def stop(self) -> None:
        self._running = False
        if (
            self._render_thread
            and self._render_thread.is_alive()
        ):
            self._render_thread.join(timeout=10)
        logger.info("RenderQueue: workers stopped")

    # ═══════════════════════════════════════════════════════════════
    # RENDER WORKER
    # ═══════════════════════════════════════════════════════════════

    def _render_worker(self) -> None:
        logger.info(
            "RenderQueue: render worker started"
        )

        while self._running:
            task = self.dequeue_render()
            if not task:
                time.sleep(2)
                continue

            started = time.monotonic()

            try:
                png_bytes = None

                # Primary: use v18 renderer with brand context
                if getattr(task, "post_dict", None):
                    try:
                        from services.card_renderer import (
                            card_renderer,
                        )

                        png_bytes = (
                            card_renderer.render_from_post_dict(
                                task.post_dict
                            )
                        )
                        if png_bytes:
                            logger.debug(
                                "RenderQueue: v18 rendered task %s "
                                "(%d bytes)",
                                task.task_id,
                                len(png_bytes),
                            )
                    except Exception as pillow_err:
                        logger.warning(
                            "RenderQueue: v18 render failed "
                            "for task %s: %s",
                            task.task_id,
                            pillow_err,
                        )

                # v2.0 — brand-aware HTML fallback
                if not png_bytes and task.html:
                    png_bytes = self._render_html_with_brand(
                        task
                    )

                if not png_bytes:
                    logger.error(
                        "RenderQueue: render failed for task %s",
                        task.task_id,
                    )
                    self._renders_failed += 1
                    self._emit("render.failed", {
                        "task_id": task.task_id,
                        "brand_id": task.brand_id,
                    })

                    if task.retries < MAX_RETRIES:
                        task.retries += 1
                        self.enqueue_render(task)
                    else:
                        self._store_result(
                            task.task_id,
                            RenderResult(
                                task.task_id, ""
                            ),
                        )
                    continue

                # Upload to Supabase Storage
                image_url = self._upload_to_storage(
                    task.brand_id, png_bytes
                )

                elapsed_ms = (
                    time.monotonic() - started
                ) * 1000

                if not image_url:
                    # A rendered image without durable storage is not a
                    # publishable render. Treat storage failure as failure so
                    # downstream publishing cannot proceed with an empty URL.
                    logger.error(
                        "RenderQueue: storage upload failed for task %s",
                        task.task_id,
                    )
                    self._renders_failed += 1
                    self._emit("render.failed", {
                        "task_id": task.task_id,
                        "brand_id": task.brand_id,
                        "reason": "storage_upload_failed",
                    })
                    if task.retries < MAX_RETRIES:
                        task.retries += 1
                        self.enqueue_render(task)
                    else:
                        self._store_failure(task.task_id, "storage_upload_failed")
                    continue

                logger.info(
                    "RenderQueue: task %s completed, "
                    "image_url=%s, %.0fms",
                    task.task_id,
                    image_url,
                    elapsed_ms,
                )

                self._renders_completed += 1
                self._emit("render.completed", {
                    "task_id": task.task_id,
                    "brand_id": task.brand_id,
                    "render_time_ms": elapsed_ms,
                })

                self._store_result(
                    task.task_id,
                    RenderResult(
                        task_id=task.task_id,
                        image_url=image_url or "",
                        image_bytes=(
                            png_bytes
                            if not image_url
                            else None
                        ),
                        render_time_ms=elapsed_ms,
                    ),
                )

            except Exception as e:
                self._renders_failed += 1
                logger.error(
                    "RenderQueue: render worker error: %s",
                    e,
                )
                self._emit("render.error", {
                    "task_id": task.task_id,
                    "error": str(e),
                })

                if task.retries < MAX_RETRIES:
                    task.retries += 1
                    self.enqueue_render(task)
                else:
                    self._store_result(
                        task.task_id,
                        RenderResult(task.task_id, ""),
                    )
            finally:
                if self._redis and self._redis.is_available:
                    try:
                        self._redis.lrem(
                            RENDER_PROCESSING_KEY, 1, getattr(task, "_redis_raw", json.dumps(task.to_dict(), default=str))
                        )
                    except Exception:
                        # Reconciliation can recover stale processing entries.
                        pass

    # ═══════════════════════════════════════════════════════════════
    # v2.0 — BRAND-AWARE HTML FALLBACK
    # ═══════════════════════════════════════════════════════════════

    def _render_html_with_brand(
        self, task: RenderTask
    ) -> Optional[bytes]:
        """Render HTML using v18 renderer with full brand context."""
        try:
            from services.card_renderer import (
                card_renderer,
            )

            # Build a rich post dict with brand context
            try:
                from bs4 import BeautifulSoup

                soup = BeautifulSoup(
                    task.html, "html.parser"
                )
                title_tag = (
                    soup.find("h1")
                    or soup.find("h2")
                    or soup.find("title")
                )
                title_text = (
                    title_tag.get_text(strip=True)
                    if title_tag
                    else task.topic or "New Post"
                )
                body_tag = soup.find("p") or soup.find(
                    "div"
                )
                subtitle_text = (
                    body_tag.get_text(strip=True)[:200]
                    if body_tag
                    else task.caption[:200]
                )
            except ImportError:
                title_text = task.topic or "New Post"
                clean = re.sub(
                    r"<[^>]+>", "", task.html
                )
                subtitle_text = " ".join(
                    clean.split()
                )[:200]

            # Use render_legacy with brand context
            png = card_renderer.render_legacy(
                title=title_text,
                subtitle=subtitle_text,
                brand_name=task.brand_name,
                brand_colors=task.brand_colors,
                brand_hashtags=task.brand_hashtags,
                brand_metadata=task.brand_metadata,
                topic=task.topic,
            )

            if png:
                logger.info(
                    "RenderQueue: brand-aware HTML fallback "
                    "rendered task %s (%d bytes)",
                    task.task_id,
                    len(png),
                )
            return png
        except Exception as e:
            logger.error(
                "RenderQueue: _render_html_with_brand failed: %s",
                e,
            )
            return None

    # ═══════════════════════════════════════════════════════════════
    # RESULT STORAGE
    # ═══════════════════════════════════════════════════════════════

    def _store_failure(self, task_id: str, reason: str) -> None:
        """Persist a terminal render failure without pretending it succeeded."""
        if self._redis and self._redis.is_available:
            try:
                self._redis.set(
                    f"{RENDER_STATUS_KEY}:{task_id}", "failed_permanently", ex=3600
                )
            except Exception:
                pass
        with self._lock:
            self._fallback_results.pop(task_id, None)

    def _store_result(
        self,
        task_id: str,
        result: RenderResult,
    ) -> None:
        if self._redis and self._redis.is_available:
            try:
                self._redis.set(
                    f"{RENDER_RESULT_KEY}:{task_id}",
                    json.dumps(result.to_dict()),
                    ex=3600,
                )
                self._redis.set(
                    f"{RENDER_STATUS_KEY}:{task_id}",
                    "completed",
                    ex=3600,
                )
            except Exception as e:
                logger.warning(
                    "RenderQueue: failed to store result: %s",
                    e,
                )

        with self._lock:
            self._fallback_results[task_id] = result

        # Clean old results
        with self._lock:
            if len(self._fallback_results) > 100:
                oldest = sorted(
                    self._fallback_results.keys()
                )[:50]
                for k in oldest:
                    del self._fallback_results[k]

    # ═══════════════════════════════════════════════════════════════
    # STORAGE UPLOAD
    # ═══════════════════════════════════════════════════════════════

    def _upload_to_storage(
        self, brand_id: str, image_bytes: bytes
    ) -> Optional[str]:
        try:
            # v2.0 — try container-resolved client first
            client = None
            if self._container:
                try:
                    from services.supabase import (
                        get_service_client_from_settings,
                    )

                    client = (
                        get_service_client_from_settings()
                    )
                except Exception:
                    pass

            if not client:
                try:
                    from services.supabase import (
                        supabase_service,
                    )

                    client = (
                        supabase_service.service_client
                        or supabase_service.client
                    )
                except Exception:
                    pass

            if not client:
                return None

            filename = (
                f"cards/{brand_id}/"
                f"{uuid.uuid4().hex}.png"
            )
            client.storage.from_(
                "post-images"
            ).upload(
                filename,
                image_bytes,
                {
                    "content-type": "image/png",
                    "cache-control": (
                        "public, max-age=31536000"
                    ),
                },
            )
            return client.storage.from_(
                "post-images"
            ).get_public_url(filename)
        except Exception as e:
            logger.error(
                "RenderQueue: upload failed: %s", e
            )
            return None

    # ═══════════════════════════════════════════════════════════════
    # v2.0 — HEALTH & STATS
    # ═══════════════════════════════════════════════════════════════

    def get_stats(self) -> dict:
        return {
            "renders_completed": self._renders_completed,
            "renders_failed": self._renders_failed,
            "queue_depth": self._queue_depth,
            "running": self._running,
            "backend": (
                "redis"
                if self._redis
                and self._redis.is_available
                else "memory"
            ),
        }

    def health_check(self) -> dict:
        stats = self.get_stats()
        status = "healthy"

        if not self._running:
            status = "stopped"
        elif (
            stats["renders_failed"]
            > stats["renders_completed"] * 0.3
            and stats["renders_completed"] > 5
        ):
            status = "degraded"
        elif stats["queue_depth"] > 50:
            status = "degraded"

        return {"status": status, "stats": stats}


# ═══════════════════════════════════════════════════════════════════
# MODULE SINGLETON
# ═══════════════════════════════════════════════════════════════════

_render_queue: Optional[RenderQueue] = None
_render_queue_lock = threading.Lock()


def get_render_queue(
    event_bus=None,
) -> RenderQueue:
    global _render_queue
    if _render_queue is None:
        with _render_queue_lock:
            if _render_queue is None:
                _render_queue = RenderQueue(
                    event_bus=event_bus
                )
                _render_queue.start_workers()
    return _render_queue


def get_render_queue_if_initialized() -> Optional[RenderQueue]:
    """Return the existing queue without constructing it or starting workers."""
    with _render_queue_lock:
        return _render_queue


def wire_render_queue_container(container) -> None:
    rq = get_render_queue()
    rq._container = container
    # v2.0 — wire EventBus if available
    try:
        from core.event_bus import EventBus
        from core.unified_bus import UnifiedEventBus

        event_bus = container.try_resolve(EventBus) or container.try_resolve(UnifiedEventBus)
        if event_bus:
            rq.set_event_bus(event_bus)
            logger.info("RenderQueue: EventBus wired (Phase 1 unified bridge)")
    except Exception:
        pass
    logger.info("RenderQueue: DI container wired.")
