"""Platform-scoped, read-only automation-job recovery specialist.

The adapter refreshes failure intelligence from the canonical job stats reader.
It never retries, quarantines, edits, or republishes an automation job.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import datetime, timezone
from typing import Any

from .contracts import EngineContribution, MissionTaskContext


class AutomationJobDiagnosticsMissionAdapter:
    """Inspect one failure category using the existing read-only stats service."""

    engine_id = "automation_job_diagnostics"
    capabilities = frozenset({"automation.jobs.diagnose"})
    max_concurrency = 1
    timeout_seconds = 20.0

    def __init__(self, *, job_stats_service: Any) -> None:
        if not callable(getattr(job_stats_service, "get_failure_intelligence", None)):
            raise ValueError("job_stats_read_only_intelligence_required")
        self.job_stats_service = job_stats_service

    async def invoke(self, context: MissionTaskContext) -> EngineContribution:
        if context.scope_type != "platform" or context.workspace_id is not None:
            raise ValueError("automation_job_diagnostics_requires_platform_scope")

        category = context.inputs.get("failure_category")
        requested_ids = context.inputs.get("job_ids", [])
        allowed_categories = {
            "caption_quality", "voice_compliance", "evidence_grounding",
            "semantic_validation", "gateway_caller_policy",
            "provider_availability", "unclassified",
        }
        if not isinstance(category, str) or category not in allowed_categories:
            raise ValueError("automation_job_failure_category_required")
        if not isinstance(requested_ids, list) or len(requested_ids) > 20:
            raise ValueError("automation_job_diagnostic_job_ids_invalid")
        requested_ids = {
            str(value).strip() for value in requested_ids
            if isinstance(value, (str, int)) and str(value).strip()
        }

        # The Supabase-backed reader is synchronous; keep blocking I/O off the
        # Director event loop. This method is strictly read-only.
        intelligence = await asyncio.to_thread(
            self.job_stats_service.get_failure_intelligence, limit=500
        )
        if not isinstance(intelligence, dict) or intelligence.get("status") != "available":
            return EngineContribution(
                engine_id=self.engine_id,
                capability=context.capability,
                task_id=context.task_id,
                mission_id=context.mission_id,
                workspace_id=None,
                scope_type="platform",
                status="unavailable",
                summary="Fresh automation-job failure intelligence is unavailable.",
                limitations=["No retry, quarantine, or publication action was attempted."],
                confidence=0.0,
                correlation_id=context.correlation_id,
            )

        categories = intelligence.get("categories")
        details = categories.get(category) if isinstance(categories, dict) else None
        if not isinstance(details, dict):
            return EngineContribution(
                engine_id=self.engine_id,
                capability=context.capability,
                task_id=context.task_id,
                mission_id=context.mission_id,
                workspace_id=None,
                scope_type="platform",
                status="partial",
                summary=f"No current failed jobs remain classified as {category}.",
                findings=["The original failure category is no longer present in the fresh snapshot."],
                recommendations=["Reconcile the original job IDs against canonical job state before considering any recovery."],
                limitations=["This specialist has read-only authority and cannot retry or quarantine jobs."],
                confidence=0.65,
                correlation_id=context.correlation_id,
            )

        current_ids = [
            str(value) for value in (details.get("job_ids") or [])
            if isinstance(value, (str, int)) and str(value).strip()
        ]
        if requested_ids:
            current_ids = [value for value in current_ids if value in requested_ids]
        proposals = [
            item for item in (details.get("recovery_proposals") or [])
            if isinstance(item, dict)
            and (
                not requested_ids
                or str(item.get("job_id") or "") in requested_ids
            )
        ][:20]
        samples = [
            item for item in (details.get("failure_samples") or [])
            if isinstance(item, dict)
            and (
                not requested_ids
                or str(item.get("job_id") or "") in requested_ids
            )
        ][:8]
        digest = hashlib.sha256(
            (category + "|" + "|".join(sorted(current_ids))).encode("utf-8")
        ).hexdigest()[:24]
        count = min(max(int(details.get("count") or 0), 0), 500)
        findings = [
            f"Fresh snapshot reports {count} failed jobs in category '{category}'.",
            f"{len(current_ids)} current job identifiers are available for bounded reconciliation.",
        ]
        for sample in samples:
            job_id = re.sub(r"[^A-Za-z0-9_.:-]", "_", str(sample.get("job_id") or ""))[:128]
            error_code = re.sub(
                r"[^A-Za-z0-9_.:-]", "_",
                str(sample.get("last_error_code") or "not_recorded"),
            )[:80]
            steps = [
                re.sub(r"[^A-Za-z0-9_.:-]", "_", value)[:60]
                for value in (sample.get("failed_steps") or [])[:5]
                if isinstance(value, str) and value.strip()
            ]
            findings.append(
                f"Job {job_id}: error_code={error_code}; failed_steps={', '.join(steps) or 'not_recorded'}."
            )
        recommendations = [
            "Re-read each candidate's canonical status and attempt history before any retry decision.",
            "Keep permanent failures quarantined and preserve the original failure evidence.",
        ]
        recommended_action = details.get("recommended_action")
        if isinstance(recommended_action, str) and recommended_action.strip():
            recommendations.append(
                "Read-only category guidance: " + recommended_action.strip()[:300]
            )
        if proposals:
            recommendations.append(
                "Stored recovery proposals are advisory hints only; they do not authorize a retry."
            )

        return EngineContribution(
            engine_id=self.engine_id,
            capability=context.capability,
            task_id=context.task_id,
            mission_id=context.mission_id,
            workspace_id=None,
            scope_type="platform",
            status="completed",
            summary=f"Read-only diagnosis refreshed for failure category '{category}'.",
            findings=findings,
            recommendations=recommendations,
            evidence=[{
                "ref": f"automation-job-failure-snapshot:{digest}",
                "title": f"Fresh failed-job snapshot: {category}",
                "url": "",
            }],
            limitations=[
                "No job state was changed.",
                "No retry, quarantine, publish, or publication replay was performed.",
            ],
            confidence=0.8 if current_ids else 0.6,
            source_metadata={
                "failed_job_count": count,
                "inspected_job_ids": current_ids[:20],
                "recovery_proposal_count": len(proposals),
                "automatic_retry": False,
                "publication_replay_allowed": False,
                "read_only": True,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            },
            correlation_id=context.correlation_id,
            authority="none",
            side_effect_class="read_only",
        )
