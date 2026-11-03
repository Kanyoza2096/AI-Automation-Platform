"""Lease-heartbeating supervisor for governed engineering build workers.

The supervisor renews a claimed job while the existing governed execution path
runs. A heartbeat failure is fail-closed: the worker result is not completed
under a lease it may no longer own. It does not retry execution or authorize
release. The queue's expiry reconciler remains responsible for uncertain jobs.
"""
from __future__ import annotations

import math
import threading
from typing import Any, Callable, Mapping

from services.kdo_engineering_phase16_3 import (
    EngineeringBuildJob,
    Phase163Error,
    run_claimed_build_job,
)


class WorkerLeaseHeartbeatError(Phase163Error):
    """Raised when lease renewal fails or the heartbeat cannot stop safely."""


class _HeartbeatQueueProxy:
    """Queue facade that stops heartbeats immediately before terminal completion."""

    def __init__(
        self,
        *,
        queue: Any,
        job: EngineeringBuildJob,
        worker_id: str,
        lease_token: str,
        lease_seconds: int,
        heartbeat_interval_seconds: float,
    ) -> None:
        self._queue = queue
        self._job = job
        self._worker_id = worker_id
        self._lease_token = lease_token
        self._lease_seconds = lease_seconds
        self._interval = heartbeat_interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._error: Exception | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._heartbeat_loop,
            name=f"kdo-lease-heartbeat-{self._job.job_id}",
            daemon=True,
        )
        self._thread.start()

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                renewed = self._queue.renew(
                    self._job,
                    worker_id=self._worker_id,
                    lease_token=self._lease_token,
                    lease_seconds=self._lease_seconds,
                )
                with self._lock:
                    self._job = renewed
            except Exception as exc:
                with self._lock:
                    self._error = exc
                self._stop.set()
                return

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=max(2.0, self._interval + 1.0))
            if thread.is_alive():
                raise WorkerLeaseHeartbeatError("lease_heartbeat_shutdown_timeout")

    def raise_if_failed(self) -> None:
        with self._lock:
            error = self._error
        if error is not None:
            raise WorkerLeaseHeartbeatError("worker_lease_heartbeat_failed") from error

    def get(self, *args: Any, **kwargs: Any) -> Any:
        return self._queue.get(*args, **kwargs)

    def renew(self, *args: Any, **kwargs: Any) -> Any:
        return self._queue.renew(*args, **kwargs)

    def complete(self, job: EngineeringBuildJob, *, worker_id: str,
                 lease_token: str, status: str) -> EngineeringBuildJob:
        self.stop()
        self.raise_if_failed()
        return self._queue.complete(
            job, worker_id=worker_id, lease_token=lease_token, status=status
        )


def run_claimed_build_job_with_heartbeat(
    *,
    workflow: Any,
    queue: Any,
    job: EngineeringBuildJob,
    worker_id: str,
    lease_token: str,
    actor_id: str,
    executor: Callable[..., Mapping[str, Any]],
    lease_seconds: int = 300,
    heartbeat_interval_seconds: float | None = None,
) -> dict[str, Any]:
    """Run the governed build path while renewing its lease in the background.

    Both SQLite and PostgreSQL queue adapters implement the required get/renew/
    complete contract. A failed renewal prevents terminal completion; it does
    not kill an executor process, automatically retry work, or mark the task
    successful. The host must use bounded/cancellable execution and reconcile
    the resulting uncertain job after worker liveness is established.
    """
    if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or not 30 <= lease_seconds <= 3600:
        raise Phase163Error("lease_seconds_must_be_between_30_and_3600")
    interval = heartbeat_interval_seconds
    if interval is None:
        interval = max(1.0, lease_seconds / 3)
    if (
        isinstance(interval, bool)
        or not isinstance(interval, (int, float))
        or not math.isfinite(interval)
        or interval <= 0
        or interval >= lease_seconds
    ):
        raise Phase163Error("heartbeat_interval_must_be_positive_and_less_than_lease")

    proxy = _HeartbeatQueueProxy(
        queue=queue,
        job=job,
        worker_id=worker_id,
        lease_token=lease_token,
        lease_seconds=lease_seconds,
        heartbeat_interval_seconds=float(interval),
    )
    proxy.start()
    try:
        result = run_claimed_build_job(
            workflow=workflow,
            queue=proxy,
            job=job,
            worker_id=worker_id,
            lease_token=lease_token,
            actor_id=actor_id,
            executor=executor,
        )
        proxy.stop()
        proxy.raise_if_failed()
        return result
    except Exception:
        proxy.stop()
        proxy.raise_if_failed()
        raise
