from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from uuid import uuid4

from core.events import DomainEvent, EventType
from core.event_bus import get_event_bus

logger = logging.getLogger(__name__)


@dataclass
class Task:
    fn: Callable
    args: tuple = field(default_factory=tuple)
    kwargs: dict = field(default_factory=dict)
    task_id: str = field(default_factory=lambda: str(uuid4())[:8])
    name: str = "unnamed"
    submitted_at: float = field(default_factory=time.monotonic)


class TaskQueue:
    """Thread-pool–based background task queue.

    Expensive operations (rendering, AI generation, GitHub scanning) are
    submitted here so web-request threads remain lightweight.
    """

    def __init__(self, workers: int = 3, maxsize: int = 200) -> None:
        self._q: queue.Queue[Optional[Task]] = queue.Queue(maxsize=maxsize)
        self._workers = workers
        self._threads: list[threading.Thread] = []
        self._running = False
        self._lock = threading.Lock()
        self._completed = 0
        self._failed = 0

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
        logger.info("TaskQueue: started %d worker threads", self._workers)

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            if not self._running:
                return
            self._running = False

        for _ in self._threads:
            self._q.put(None)

        for t in self._threads:
            t.join(timeout=timeout)
        self._threads.clear()
        logger.info("TaskQueue: stopped")

    def submit(self, fn: Callable, *args, name: str = "", **kwargs) -> str:
        task = Task(fn=fn, args=args, kwargs=kwargs, name=name or fn.__name__)
        try:
            self._q.put_nowait(task)
            logger.debug("TaskQueue: submitted '%s' (%s)", task.name, task.task_id)
        except queue.Full:
            logger.error("TaskQueue: queue full — dropping task '%s'", task.name)
        return task.task_id

    def _worker_loop(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                break
            start = time.monotonic()
            try:
                item.fn(*item.args, **item.kwargs)
                elapsed_ms = (time.monotonic() - start) * 1000
                with self._lock:
                    self._completed += 1
                logger.debug(
                    "TaskQueue: '%s' done in %.0fms", item.name, elapsed_ms
                )
            except Exception as exc:
                with self._lock:
                    self._failed += 1
                logger.error(
                    "TaskQueue: '%s' failed: %s", item.name, exc, exc_info=True
                )
                get_event_bus().publish(
                    DomainEvent(
                        event_type=EventType.WORKER_ERROR,
                        payload={"task": item.name, "error": str(exc)},
                    )
                )
            finally:
                self._q.task_done()

    @property
    def stats(self) -> dict:
        with self._lock:
            return {
                "queued": self._q.qsize(),
                "completed": self._completed,
                "failed": self._failed,
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
