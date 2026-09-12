"""
Background TaskQueue — truthful acceptance (Phase HARDEN).

Contract (architecture report §20):
  submit
    → durably accepted? YES → task_id
                        NO  → raise QueueFullError (or return None via try_submit)
  Never: queue full → drop → still return task_id (fake success).
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)


class QueueFullError(RuntimeError):
    """Queue rejected the task — caller must treat as failure / 503."""

    def __init__(self, name: str, maxsize: int):
        self.task_name = name
        self.maxsize = maxsize
        super().__init__(f"TaskQueue full (maxsize={maxsize}) — rejected task '{name}'")


@dataclass
class Task:
    fn: Callable
    args: tuple = field(default_factory=tuple)
    kwargs: dict = field(default_factory=dict)
    task_id: str = field(default_factory=lambda: str(uuid4())[:8])
    name: str = "unnamed"
    submitted_at: float = field(default_factory=time.monotonic)


class TaskQueue:
    """Thread-pool background task queue with honest backpressure."""

    def __init__(self, workers: int = 3, maxsize: int = 200) -> None:
        self._q: queue.Queue[Optional[Task]] = queue.Queue(maxsize=maxsize)
        self._workers = workers
        self._maxsize = maxsize
        self._threads: list[threading.Thread] = []
        self._running = False
        self._lock = threading.Lock()
        self._completed = 0
        self._failed = 0
        self._rejected = 0

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True

        for i in range(self._workers):
            t = threading.Thread(
                target=self._worker_loop,
                name=f"task-worker-{i}",
                daemon=True,
            )
            t.start()
            self._threads.append(t)
        logger.info("TaskQueue: started %d worker threads (maxsize=%d)", self._workers, self._maxsize)

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            if not self._running:
                return
            self._running = False
            threads = list(self._threads)

        # Never silently drop shutdown sentinels. A full queue is temporary: as
        # workers drain queued work, make room for every sentinel.
        for _ in threads:
            while True:
                try:
                    self._q.put(None, timeout=0.25)
                    break
                except queue.Full:
                    if not any(t.is_alive() for t in threads):
                        break

        for t in threads:
            t.join(timeout=timeout)

        for t in self._threads:
            t.join(timeout=timeout)
        self._threads.clear()
        logger.info("TaskQueue: stopped")

    def try_submit(self, fn: Callable, *args, name: str = "", **kwargs) -> Optional[str]:
        """
        Submit if capacity allows. Returns task_id or None if rejected.
        Prefer this when caller can handle soft failure.
        """
        task = Task(fn=fn, args=args, kwargs=kwargs, name=name or getattr(fn, "__name__", "unnamed"))
        try:
            # Serialize the running-state check with shutdown so a submit cannot
            # race a stop and enqueue after shutdown has been committed.
            with self._lock:
                if not self._running:
                    logger.error("TaskQueue: not running — rejecting '%s'", task.name)
                    self._rejected += 1
                    return None
                self._q.put_nowait(task)
            logger.debug("TaskQueue: accepted '%s' (%s)", task.name, task.task_id)
            return task.task_id
        except queue.Full:
            self._rejected += 1
            logger.error(
                "TaskQueue: REJECTED '%s' — queue full (maxsize=%d, rejected_total=%d)",
                task.name,
                self._maxsize,
                self._rejected,
            )
            return None

    def submit(self, fn: Callable, *args, name: str = "", **kwargs) -> str:
        """
        Submit or raise QueueFullError.
        Never returns a task_id for a task that was not accepted.
        """
        task_id = self.try_submit(fn, *args, name=name, **kwargs)
        if task_id is None:
            raise QueueFullError(name or getattr(fn, "__name__", "unnamed"), self._maxsize)
        return task_id

    def _worker_loop(self) -> None:
        while True:
            task = self._q.get()
            try:
                if task is None:
                    break
                try:
                    task.fn(*task.args, **task.kwargs)
                    with self._lock:
                        self._completed += 1
                except Exception as exc:
                    with self._lock:
                        self._failed += 1
                    logger.exception("TaskQueue: task '%s' (%s) failed: %s", task.name, task.task_id, exc)
            finally:
                self._q.task_done()


    async def enqueue(self, fn, *args, name: str = "", **kwargs):
        """Async-friendly enqueue for coroutine or sync callables."""
        import asyncio
        import inspect

        if inspect.iscoroutinefunction(fn):
            def _runner():
                loop = asyncio.new_event_loop()
                try:
                    return loop.run_until_complete(fn(*args, **kwargs))
                finally:
                    loop.close()
            return self.submit(_runner, name=name or getattr(fn, "__name__", "coro"))
        return self.submit(fn, *args, name=name, **kwargs)

    def stats(self) -> dict:
        return {
            "queued": self._q.qsize(),
            "maxsize": self._maxsize,
            "completed": self._completed,
            "failed": self._failed,
            "rejected": self._rejected,
            "workers": self._workers,
            "running": self._running,
        }


_queue: Optional[TaskQueue] = None
_queue_lock = threading.Lock()


def get_task_queue() -> TaskQueue:
    global _queue
    if _queue is None:
        with _queue_lock:
            if _queue is None:
                _queue = TaskQueue(workers=3)
                _queue.start()
    return _queue
