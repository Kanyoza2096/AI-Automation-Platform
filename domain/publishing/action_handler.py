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

    # ── Source resolution ─────────────────────────────────────────────
    # `source` determines whether the canonical pipeline convenes Council.
    # Sources in MANUAL_AUTHORIZED_SOURCES (manual, scheduled, outbox,
    # dead_letter_retry) are authorized by their entry point and skip
    # Council; any other source triggers Council deliberation.
    #
    # Precedence:
    #   1. parameters["source"] — set by execute_governed_publish from the
    #      caller's declared source
    #   2. intent.source        — set by the Action Kernel from the same
    #      value; present when the caller passes source to kernel.execute()
    #   3. Raise ValueError     — never default. A missing source is a bug
    #      in the caller, not a valid state. Silently defaulting to a
    #      system string (previously "kcpg_action_kernel") reclassifies a
    #      human-initiated publish as an autonomous kernel action, which
    #      convenes Council on content it cannot evaluate, which returns
    #      needs_revision when personas abstain, which halts the pipeline
    #      on a publish that was already authorized upstream.
    source = parameters.get("source") or getattr(intent, "source", None)
    if not source:
        return {
            "ok": False,
            "outcome": "failed",
            "error": (
                "publish requires an explicit source; neither "
                "parameters.source nor intent.source is set"
            ),
            "decision_id": decision_id,
            "workspace_id": getattr(intent, "workspace_id", None),
            "action_intent_id": getattr(intent, "intent_id", None),
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
                "decision_id": decision_id,
            }

    result = await run_publish(
        container=parameters.get("container"),
        workspace_id=intent.workspace_id,
        brand_id=parameters.get("brand_id"),
        platform=parameters.get("platform"),
        topic=parameters.get("topic", ""),
        caption=parameters.get("caption", ""),
        source=source,
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
