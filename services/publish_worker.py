"""
Background publish worker — loads job via JobService, requires DecisionGate, runs orchestration.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("kanyoza.services.publish_worker")


async def publish_job_task(job_id: str, container=None) -> Dict[str, Any]:
    """
    Process a queued AutomationJob for publish.

    Loads job exclusively through JobService (no JobRepository).
    If decision_id is missing, explicitly issues DIRECTOR_AUTO *before*
    calling assert_publish_allowed (strict validation never mints authority).
    """
    from core.publish_authorization import assert_publish_allowed
    from core.decision_gate import DecisionGateError, decision_gate, DecisionSource
    from services.publish_orchestration import run_publish

    workspace_id = ""
    decision_id: Optional[str] = None
    brand_id: Optional[str] = None
    caption = ""
    topic = ""
    dry_run = False
    platform = "facebook"

    # Resolve container if caller omitted it
    if container is None:
        try:
            from service_container import get_container
            container = get_container()
        except Exception:
            container = None

    # ── Load job via JobService (canonical) ───────────────────────────
    job = None
    if container is not None:
        try:
            from services.job_service import JobService
            job_service = container.try_resolve(JobService) if hasattr(container, "try_resolve") else None
            if job_service is None and hasattr(container, "resolve"):
                try:
                    job_service = container.resolve(JobService)
                except Exception:
                    job_service = None
            if job_service is not None:
                job = await job_service.find_by_id(job_id)
        except Exception as exc:
            logger.warning("JobService load failed job_id=%s: %s", job_id, exc)

    if job is not None:
        workspace_id = getattr(job, "workspace_id", None) or ""
        brand_id = getattr(job, "brand_id", None)
        decision_id = getattr(job, "decision_id", None)
        topic = getattr(job, "topic", None) or ""
        platform = getattr(job, "platform", None) or "facebook"
        result = getattr(job, "result", None) or {}
        if isinstance(result, dict):
            caption = str(result.get("caption") or "")
            topic = topic or str(result.get("topic") or "")
            dry_run = bool(result.get("dry_run", False))
            if not decision_id:
                decision_id = result.get("decision_id")
    else:
        logger.error(
            "publish_job_task: job %s could not be loaded; refusing to publish",
            job_id,
        )
        return {"ok": False, "job_id": job_id, "error": "publish_job_not_found_or_unreadable"}

    if not workspace_id:
        logger.error("publish_job_task: job %s has no workspace_id; refusing to publish", job_id)
        return {"ok": False, "job_id": job_id, "error": "workspace_required"}
    if not decision_id:
        # Jobs created by trusted scheduler/manual workflow entry points may be
        # missing a persisted decision for backward compatibility. Convert that
        # trusted trigger into a bounded DecisionGate authorization rather than
        # bypassing governance. Higher-risk work still requires an explicit
        # decision and is rejected by the helper.
        try:
            from core.publish_authorization import issue_internal_publish_decision
            decision = issue_internal_publish_decision(
                workspace_id=workspace_id, source="scheduled" if getattr(job, "workflow_type", None) and "SCHEDULE" in str(getattr(job, "workflow_type", "")).upper() else "manual",
                risk_level="low", brand_id=brand_id, platform=platform,
            )
            decision_id = decision.decision_id
        except Exception as exc:
            logger.error("publish_job_task: decision authorization failed job=%s: %s", job_id, exc)
            return {"ok": False, "job_id": job_id, "error": "decision_required", "detail": str(exc)}

    try:
        # strict validation only — does not mint decisions
        assert_publish_allowed(decision_id, workspace_id, consume=False, strict=True)
    except DecisionGateError as exc:
        logger.error("Worker publish denied job=%s: %s", job_id, exc)
        return {"ok": False, "job_id": job_id, "error": str(exc)}

    result = await run_publish(
        container=container,
        workspace_id=workspace_id,
        brand_id=brand_id,
        platform=platform,
        caption=caption,
        topic=topic,
        decision_id=decision_id,
        source="publish_worker",
        dry_run=dry_run,
        durable=False,
    )
    if isinstance(result, dict):
        result["job_id"] = job_id
    return result
