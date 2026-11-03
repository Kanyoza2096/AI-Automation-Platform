"""Phase 16.x: scoped polling service for durable engineering build jobs.

This is an injectable worker service, not an application bootstrap or a new
authorization boundary. The host supplies the canonical queue, governed
workflow, actor identity, and bounded executor. Jobs are claimed only inside
the worker's configured tenant/workspace and are run through the existing
lease-heartbeat supervisor. Uncertain work is never automatically retried.
"""
from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from services.kdo_engineering_phase16_3 import EngineeringBuildJob, Phase163Error
from services.kdo_engineering_worker_runtime import (
    run_claimed_build_job_with_heartbeat,
)

logger = logging.getLogger("kdo.engineering.worker")


@dataclass(frozen=True)
class EngineeringWorkerSummary:
    claimed: int = 0
    completed: int = 0
    execution_errors: int = 0
    claim_errors: int = 0
    stopped: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "claimed": self.claimed,
            "completed": self.completed,
            "execution_errors": self.execution_errors,
            "claim_errors": self.claim_errors,
            "stopped": self.stopped,
        }


def run_engineering_build_worker(
    *,
    queue: Any,
    workflow: Any,
    worker_id: str,
    tenant_id: str,
    workspace_id: str,
    actor_id: str,
    executor: Callable[..., Mapping[str, Any]],
    stopping: threading.Event,
    lease_seconds: int = 300,
    heartbeat_interval_seconds: float | None = None,
    poll_interval_seconds: float = 2.0,
    max_poll_interval_seconds: float = 30.0,
    max_jobs: int | None = None,
    on_result: Callable[[EngineeringBuildJob, Mapping[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Poll and execute scoped jobs until stopped or max_jobs is reached.

    The queue must implement claim_next with tenant/workspace filters. A claim
    error backs off exponentially; an execution error is recorded and is not
    automatically retried. The executor must itself enforce bounded,
    cancellable, isolated execution. Setting the stop event prevents new claims
    but does not pretend to cancel a running child process.
    """
    required = (worker_id, tenant_id, workspace_id, actor_id)
    if any(not isinstance(value, str) or not value.strip() for value in required):
        raise Phase163Error("engineering_worker_identity_and_scope_required")
    if not callable(getattr(queue, "claim_next", None)):
        raise Phase163Error("engineering_build_queue_claim_api_required")
    if not callable(executor):
        raise Phase163Error("engineering_worker_executor_required")
    if not isinstance(stopping, threading.Event):
        raise Phase163Error("engineering_worker_stop_event_required")
    if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or not 30 <= lease_seconds <= 3600:
        raise Phase163Error("lease_seconds_must_be_between_30_and_3600")
    if (
        isinstance(poll_interval_seconds, bool)
        or not isinstance(poll_interval_seconds, (int, float))
        or not math.isfinite(poll_interval_seconds)
        or poll_interval_seconds <= 0
    ):
        raise Phase163Error("poll_interval_must_be_positive_and_finite")
    if (
        isinstance(max_poll_interval_seconds, bool)
        or not isinstance(max_poll_interval_seconds, (int, float))
        or not math.isfinite(max_poll_interval_seconds)
        or max_poll_interval_seconds < poll_interval_seconds
    ):
        raise Phase163Error("max_poll_interval_must_be_finite_and_at_least_poll_interval")
    if max_jobs is not None and (
        isinstance(max_jobs, bool) or not isinstance(max_jobs, int) or max_jobs < 1
    ):
        raise Phase163Error("max_jobs_must_be_positive_integer")

    claimed = completed = execution_errors = claim_errors = 0
    consecutive_claim_errors = 0
    while not stopping.is_set() and (max_jobs is None or claimed < max_jobs):
        try:
            job = queue.claim_next(
                worker_id=worker_id,
                lease_seconds=lease_seconds,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
            )
            consecutive_claim_errors = 0
        except Exception:
            claim_errors += 1
            consecutive_claim_errors += 1
            delay = min(
                float(max_poll_interval_seconds),
                float(poll_interval_seconds) * (2 ** min(consecutive_claim_errors - 1, 10)),
            )
            logger.exception(
                "Engineering build claim failed; backing off %.2fs (worker=%s)",
                delay,
                worker_id,
            )
            stopping.wait(delay)
            continue

        if job is None:
            stopping.wait(float(poll_interval_seconds))
            continue
        if (
            job.tenant_id != tenant_id
            or job.workspace_id != workspace_id
            or job.lease_owner != worker_id
            or not job.lease_token
        ):
            # A malformed or unexpectedly scoped claim must never reach execution.
            claim_errors += 1
            raise Phase163Error("queue_returned_out_of_scope_claim")

        claimed += 1
        try:
            result = run_claimed_build_job_with_heartbeat(
                workflow=workflow,
                queue=queue,
                job=job,
                worker_id=worker_id,
                lease_token=job.lease_token,
                actor_id=actor_id,
                executor=executor,
                lease_seconds=lease_seconds,
                heartbeat_interval_seconds=heartbeat_interval_seconds,
                cancellation_event=stopping,
            )
            completed += 1
            if on_result is not None:
                try:
                    on_result(job, result)
                except Exception:
                    # Observability callbacks must not rewrite a completed job
                    # into an execution failure or trigger a retry.
                    logger.exception("Engineering worker result callback failed (job=%s)", job.job_id)
        except Exception:
            # No blind retry: the governed path may have modified a worktree or
            # reached an uncertain state. Queue reconciliation owns recovery.
            execution_errors += 1
            logger.exception(
                "Engineering build execution failed; no automatic retry (job=%s)",
                job.job_id,
            )

    return EngineeringWorkerSummary(
        claimed=claimed,
        completed=completed,
        execution_errors=execution_errors,
        claim_errors=claim_errors,
        stopped=stopping.is_set(),
    ).to_dict()
