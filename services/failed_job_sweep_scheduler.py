"""Autonomous periodic recovery for failed automation jobs.

Runs in the existing application process so it reuses the configured backend
service-role credentials and does not require a separately provisioned worker.
The transactional database RPC remains authoritative for row locking,
eligibility, retry limits, archival, and publication-evidence protection.
"""
from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger("kanyoza.failed_job_sweep_scheduler")

DEFAULT_INTERVAL_SECONDS = 300
MIN_INTERVAL_SECONDS = 60
MAX_INTERVAL_SECONDS = 3600
BATCH_SIZE = 25

_scheduler_task: asyncio.Task | None = None


def _interval_seconds() -> int:
    raw = os.getenv("KDO_FAILED_JOB_SWEEP_INTERVAL_SECONDS", str(DEFAULT_INTERVAL_SECONDS))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning("invalid_failed_job_sweep_interval; using default=%d", DEFAULT_INTERVAL_SECONDS)
        return DEFAULT_INTERVAL_SECONDS
    return max(MIN_INTERVAL_SECONDS, min(value, MAX_INTERVAL_SECONDS))


async def run_failed_job_sweep_once() -> dict:
    """Run one bounded sweep using only the configured service-role client."""
    from services.supabase import get_service_client_from_settings
    from services.job_service import JobService
    from services.failed_job_sweep import sweep_failed_automation_jobs

    service_client = get_service_client_from_settings()
    if service_client is None:
        raise RuntimeError("failed_job_sweep_service_role_client_unavailable")

    result = await sweep_failed_automation_jobs(
        job_service=JobService(supabase_client=service_client),
        batch_size=BATCH_SIZE,
    )
    logger.info(
        "autonomous_failed_job_sweep_completed",
        extra={
            "event": "autonomous_failed_job_sweep_completed",
            "requeued_count": int(result.get("requeued_count") or 0),
            "archived_count": int(result.get("archived_count") or 0),
            "protected_count": int(result.get("protected_count") or 0),
            "batch_size": BATCH_SIZE,
        },
    )
    return result


async def _periodic_failed_job_sweep() -> None:
    interval = _interval_seconds()
    logger.info(
        "autonomous_failed_job_sweep_loop_started",
        extra={"event": "autonomous_failed_job_sweep_loop_started", "interval_seconds": interval},
    )
    # Allow the rest of the application to finish its boot-time initialization.
    await asyncio.sleep(10)
    while True:
        try:
            await run_failed_job_sweep_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception(
                "autonomous_failed_job_sweep_failed",
                extra={"event": "autonomous_failed_job_sweep_failed", "error_class": type(exc).__name__},
            )
        await asyncio.sleep(interval)


async def start_failed_job_sweep_scheduler() -> None:
    """Start one recurring sweep loop; safe to call repeatedly."""
    global _scheduler_task
    enabled = os.getenv("KDO_FAILED_JOB_SWEEP_ENABLED", "true").strip().lower()
    if enabled in {"0", "false", "no", "off"}:
        logger.info("autonomous_failed_job_sweep_disabled_by_configuration")
        return
    if _scheduler_task is None or _scheduler_task.done():
        _scheduler_task = asyncio.create_task(
            _periodic_failed_job_sweep(),
            name="autonomous-failed-job-sweep",
        )
        logger.info(
            "autonomous_failed_job_sweep_scheduler_started",
            extra={"event": "autonomous_failed_job_sweep_scheduler_started", "interval_seconds": _interval_seconds()},
        )


async def stop_failed_job_sweep_scheduler() -> None:
    """Stop and await the recurring loop during application shutdown."""
    global _scheduler_task
    task = _scheduler_task
    _scheduler_task = None
    if task is None:
        return
    if not task.done():
        task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("autonomous_failed_job_sweep_scheduler_shutdown_failed")
