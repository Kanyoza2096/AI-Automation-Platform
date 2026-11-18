"""Native specialist-engine adapters registered by Director orchestration."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from .contracts import EngineContribution, MissionTaskContext


class ContentIntelligenceMissionAdapter:
    """Read-only research adapter over the existing Content Intelligence Engine."""

    engine_id = "content_intelligence"
    capabilities = frozenset({"research.analyze"})
    max_concurrency = 2
    timeout_seconds = 75.0

    def __init__(self, engine: Any = None) -> None:
        if engine is None:
            from services.content_intelligence import content_intelligence_engine
            engine = content_intelligence_engine
        self._engine = engine

    async def invoke(self, context: MissionTaskContext) -> EngineContribution:
        started = datetime.now(timezone.utc)
        topic = str(context.inputs.get("topic") or context.objective).strip()
        if not topic:
            raise ValueError("research_topic_required")

        max_results = context.inputs.get("max_results", 8)
        fetch_top = context.inputs.get("fetch_top", 4)
        try:
            max_results = max(1, min(int(max_results), 12))
            fetch_top = max(0, min(int(fetch_top), 6))
        except (TypeError, ValueError) as exc:
            raise ValueError("research_limits_must_be_integers") from exc

        result = await self._engine.analyze(
            topic,
            context=str(context.inputs.get("context") or "")[:4000],
            max_results=max_results,
            fetch_top=fetch_top,
            workspace_id=context.workspace_id,
        )
        payload = result.to_dict() if hasattr(result, "to_dict") else asdict(result)
        evidence = []
        for item in payload.get("evidence", []) or []:
            if not isinstance(item, dict):
                continue
            evidence.append({
                "ref": str(item.get("fingerprint") or item.get("url") or ""),
                "title": str(item.get("title") or "")[:300],
                "url": str(item.get("url") or "")[:2000],
                "source": str(item.get("source") or "unknown")[:120],
                "snippet": str(item.get("snippet") or "")[:1200],
            })

        mode = str(payload.get("mode") or "unavailable").lower()
        summary = str(payload.get("summary") or "").strip()[:5000]
        if mode == "unavailable" or not evidence:
            status = "unavailable"
        elif mode == "full" and summary:
            status = "completed"
        else:
            status = "partial"

        claims = [str(value)[:1000] for value in (payload.get("claims") or [])[:12]]
        limitations = [str(value)[:500] for value in (payload.get("warnings") or [])[:12]]
        limitations.extend(str(value)[:500] for value in (payload.get("risks") or [])[:12])

        return EngineContribution(
            engine_id=self.engine_id,
            capability=context.capability,
            task_id=context.task_id,
            mission_id=context.mission_id,
            workspace_id=context.workspace_id,
            status=status,
            summary=summary or "No evidence-backed synthesis was available.",
            findings=claims,
            recommendations=[str(value)[:1000] for value in (payload.get("recommendations") or [])[:12]],
            evidence=evidence,
            limitations=limitations,
            confidence=float(payload.get("confidence") or 0.0),
            source_metadata={
                "mode": mode,
                "sources_count": int(payload.get("sources_count") or len(evidence)),
                "providers": [str(value)[:120] for value in (payload.get("providers") or [])[:12]],
                "engine_version": "1.0.0",
            },
            correlation_id=context.correlation_id,
            started_at=started,
            finished_at=datetime.now(timezone.utc),
            authority="none",
            side_effect_class="external_read_only",
        )
