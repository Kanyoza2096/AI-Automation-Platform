"""Bounded recovery sweep for failed automation jobs."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

async def sweep_failed_automation_jobs(*, job_service: Any, batch_size: int = 25) -> dict[str, Any]:
    """Requeue safe failed jobs below three failed executions; archive and remove at three.

    The transactional service-role RPC locks each row and preserves uncertain
    publication outcomes for reconciliation rather than replaying them.
    """
    client = job_service._require_client()
    response = await asyncio.to_thread(
        lambda: client.rpc(
            "sweep_failed_automation_jobs",
            {"p_batch_size": max(1, min(int(batch_size), 100))},
        ).execute()
    )
    data = getattr(response, "data", None)
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict) or data.get("status") != "completed":
        raise RuntimeError("failed_job_sweep_rpc_returned_unexpected_result")
    logger.warning(
        "automation_job_sweep_completed requeued=%d archived=%d protected=%d",
        int(data.get("requeued_count") or 0),
        int(data.get("archived_count") or 0),
        int(data.get("protected_count") or 0),
    )
    return data
