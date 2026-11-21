"""Governed automatic retries for narrowly allowlisted transient AI-provider failures.

The provider probe is an actual, bounded gateway request. Job state changes are
performed only by the transactional service-role RPC; this module never publishes.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Iterable

logger = logging.getLogger(__name__)

MAX_AUTOMATIC_PROVIDER_RETRIES_PER_SWEEP = 8
PROBE_EXPECTED_TOKEN = "KDO_PROVIDER_HEALTH_OK"


async def _probe_provider_recovery() -> dict[str, Any] | None:
    """Require a fresh successful real gateway completion, not config/circuit state."""
    try:
        from ai.gateway import get_llm_gateway

        gateway = get_llm_gateway()
        result = await asyncio.to_thread(
            gateway.evaluate,
            prompt=(
                "KDO automated provider recovery health probe. "
                f"Reply with exactly {PROBE_EXPECTED_TOKEN} and no other text."
            ),
            expected_keywords=[PROBE_EXPECTED_TOKEN],
            workspace_id="platform",
        )
        if (
            not isinstance(result, dict)
            or result.get("ok") is not True
            or result.get("keyword_hits") != 1
            or not str(result.get("provider") or "").strip()
            or not str(result.get("request_id") or "").strip()
        ):
            return None
        return {
            "provider": str(result["provider"])[:100],
            "request_id": str(result["request_id"])[:160],
            "probed_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception:
        logger.exception("governed_provider_recovery_probe_failed")
        return None


async def recover_provider_availability_jobs(
    *,
    job_ids: Iterable[str],
    recovery_key_prefix: str,
    job_service: Any,
    max_jobs: int = MAX_AUTOMATIC_PROVIDER_RETRIES_PER_SWEEP,
) -> dict[str, Any]:
    """Probe once, then atomically queue only eligible FAILED provider jobs.

    The SQL RPC independently rechecks current job status, attempt budget,
    generation-stage failure evidence, and absence of publishing evidence under
    a row lock. Any uncertain condition fails closed.
    """
    bounded_ids = list(dict.fromkeys(
        str(value).strip() for value in job_ids
        if isinstance(value, (str, int)) and str(value).strip()
    ))[: max(0, min(int(max_jobs), MAX_AUTOMATIC_PROVIDER_RETRIES_PER_SWEEP))]
    if not bounded_ids:
        return {"status": "no_candidates", "queued": [], "rejected": []}

    probe = await _probe_provider_recovery()
    if probe is None:
        logger.warning("governed_provider_recovery_deferred reason=provider_probe_not_healthy")
        return {"status": "deferred", "reason": "provider_probe_not_healthy", "queued": [], "rejected": bounded_ids}

    client = job_service._require_client()
    queued: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    for job_id in bounded_ids:
        recovery_key = hashlib.sha256(
            f"{recovery_key_prefix}|{job_id}|provider-retry-v1".encode("utf-8")
        ).hexdigest()
        try:
            params = {
                "p_job_id": job_id,
                "p_recovery_key": recovery_key,
                "p_provider": probe["provider"],
                "p_probe_request_id": probe["request_id"],
                "p_probe_at": probe["probed_at"],
                "p_probe_ok": True,
            }
            response = await asyncio.to_thread(
                lambda: client.rpc("stage_governed_provider_retry", params).execute()
            )
            data = getattr(response, "data", None)
            if isinstance(data, list) and data:
                data = data[0]
            if not isinstance(data, dict) or data.get("status") not in {"queued", "replayed"}:
                raise RuntimeError("governed_retry_rpc_returned_unexpected_result")
            queued.append(data)
        except Exception as exc:
            rejected.append({"job_id": job_id, "reason": str(exc)[:180]})
            logger.info(
                "governed_provider_retry_not_staged job_id=%s reason=%s",
                job_id, str(exc)[:180],
            )

    return {
        "status": "completed",
        "provider": probe["provider"],
        "probe_request_id": probe["request_id"],
        "queued": queued,
        "rejected": rejected,
    }
