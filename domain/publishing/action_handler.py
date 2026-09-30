"""Governed Action Kernel adapter for the publishing domain.

This module is the domain-owned boundary between the generic Action Kernel and
KDO's publishing application.  The kernel owns governance and execution
semantics; this adapter owns publishing-specific parameter translation.
"""
from __future__ import annotations

import base64
from typing import Any, Dict


async def publish_content(parameters: Dict[str, Any], intent: Any) -> Dict[str, Any]:
    """Execute a governed publishing intent through the canonical pipeline.

    The Action Kernel deliberately does not know about publishing parameters.
    This adapter translates the generic intent into the publishing domain's
    canonical execution contract and returns its receipt unchanged.
    """
    from services.publish_orchestration import run_publish

    decision_id = str(parameters.get("decision_id") or intent.decision_id or "").strip()
    if not decision_id:
        return {
            "ok": False,
            "outcome": "failed",
            "error": "publish requires an explicit decision_id",
        }

    raw_image = parameters.get("image_bytes_b64")
    image_bytes = None
    if raw_image:
        try:
            image_bytes = base64.b64decode(raw_image, validate=True)
        except Exception:
            return {
                "ok": False,
                "outcome": "failed",
                "error": "invalid image_bytes_b64",
            }

    result = await run_publish(
        container=parameters.get("container"),
        workspace_id=intent.workspace_id,
        brand_id=parameters.get("brand_id"),
        platform=parameters.get("platform"),
        topic=parameters.get("topic", ""),
        caption=parameters.get("caption", ""),
        source=parameters.get("source") or "kcpg_action_kernel",
        risk_level=intent.risk_level,
        decision_id=decision_id,
        durable=False,
        idempotency_key=intent.idempotency_key,
        execution_managed=True,
        image_url=parameters.get("image_url"),
        image_bytes=image_bytes,
        scheduled_post_id=parameters.get("scheduled_post_id"),
        automation_job_id=parameters.get("automation_job_id"),
        dry_run=bool(parameters.get("dry_run", False)),
    )

    if isinstance(result, dict):
        return result
    return {
        "ok": False,
        "outcome": "failed",
        "error": "publish returned invalid result",
    }
