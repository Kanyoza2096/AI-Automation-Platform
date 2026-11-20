"""Advisory-only adapter for the existing Autonomous Condition Engine.

This adapter reads already-ingested, workspace-owned brand observations. It
never calls the Condition Engine action executor or mutates content.
"""
from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from .contracts import EngineContribution, MissionTaskContext


class ConditionEngineMissionAdapter:
    engine_id = "condition_engine"
    capabilities = frozenset({"calculate_brand_health"})
    max_concurrency = 1
    timeout_seconds = 20.0

    def __init__(
        self,
        *,
        brand_ownership_verifier: Callable[[str, str], Any],
        engine: Any | None = None,
        max_observation_age_seconds: int = 30 * 24 * 60 * 60,
    ) -> None:
        if not callable(brand_ownership_verifier):
            raise ValueError("condition_engine_brand_ownership_verifier_required")
        if max_observation_age_seconds < 60:
            raise ValueError("condition_engine_max_observation_age_too_small")
        if engine is None:
            from services.condition_engine import autonomous_engine
            engine = autonomous_engine
        if not callable(getattr(engine, "calculate_brand_health", None)):
            raise TypeError("condition_engine_brand_health_api_unavailable")
        self.engine = engine
        self.brand_ownership_verifier = brand_ownership_verifier
        self.max_observation_age_seconds = int(max_observation_age_seconds)

    @staticmethod
    def _age_seconds(value: Any) -> float | None:
        if not value:
            return None
        try:
            stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            return max(0.0, (datetime.now(timezone.utc) - stamp.astimezone(timezone.utc)).total_seconds())
        except (TypeError, ValueError):
            return None

    async def invoke(self, context: MissionTaskContext) -> EngineContribution:
        brand_id = str(context.inputs.get("brand_id") or "").strip()
        if not brand_id:
            raise ValueError("condition_engine_brand_id_required")

        ownership = self.brand_ownership_verifier(brand_id, context.workspace_id)
        if inspect.isawaitable(ownership):
            ownership = await ownership
        if not ownership:
            raise PermissionError("condition_engine_brand_workspace_ownership_failed")

        health = await asyncio.to_thread(
            self.engine.calculate_brand_health,
            brand_id,
            emit_event=False,
            workspace_id=context.workspace_id,
        )
        if not isinstance(health, Mapping):
            raise TypeError("condition_engine_health_result_invalid")

        observation_count = max(0, int(health.get("observation_count") or 0))
        latest = health.get("latest_observed_at")
        age = self._age_seconds(latest)
        no_observations = observation_count == 0 or health.get("status") == "unknown"
        stale = age is None or age > self.max_observation_age_seconds
        status = "unavailable" if no_observations else "partial" if stale else "completed"

        score = float(health.get("score") or 0.0)
        health_status = str(health.get("status") or "unknown")
        summary = (
            f"Condition Engine reports brand health {score:.1f}/100 ({health_status}) "
            f"from {observation_count} existing observations."
            if not no_observations
            else "Condition Engine has no existing observations for this workspace-owned brand."
        )

        findings = [
            f"Brand health score: {score:.1f}/100 ({health_status}).",
            f"Observation sample count: {observation_count}.",
        ]
        components = health.get("components") or {}
        if isinstance(components, Mapping):
            for metric, values in list(components.items())[:6]:
                if isinstance(values, Mapping):
                    findings.append(
                        f"{str(metric)[:80]} combined score: {float(values.get('combined') or 0):.1f}."
                    )

        if no_observations:
            recommendations = ("Collect verified engagement observations before making a brand-health decision.",)
        elif health_status in {"weak", "critical", "watch"}:
            recommendations = ("Escalate the evidence and review the content strategy; no content action was executed.",)
        else:
            recommendations = ("Continue monitoring with fresh engagement observations; no content action was executed.",)

        limitations = [
            "Analysis uses the Condition Engine's existing in-memory observation history.",
            "This adapter does not invoke the Condition Engine action executor.",
            "Persistence readiness for the Condition Engine is not certified by this analysis.",
        ]
        if stale:
            limitations.append("The latest observation is missing or older than the configured freshness window.")

        refs = tuple(
            dict(item)
            for item in (health.get("observation_refs") or [])[:10]
            if isinstance(item, Mapping) and item.get("ref")
        )
        confidence = 0.0 if no_observations else min(0.9, 0.35 + min(observation_count, 11) * 0.05)
        return EngineContribution(
            engine_id=self.engine_id,
            capability=context.capability,
            task_id=context.task_id,
            mission_id=context.mission_id,
            workspace_id=context.workspace_id,
            status=status,
            summary=summary,
            findings=tuple(findings[:8]),
            recommendations=recommendations,
            evidence=refs,
            limitations=tuple(limitations),
            confidence=confidence,
            source_metadata={
                "brand_id": brand_id,
                "observation_count": observation_count,
                "latest_observed_at": latest,
                "observation_age_seconds": age,
                "max_observation_age_seconds": self.max_observation_age_seconds,
                "workspace_id": context.workspace_id,
                "action_execution_performed": False,
                "persistence_status": "unverified",
            },
            correlation_id=context.correlation_id,
            side_effect_class="internal_record",
        )
