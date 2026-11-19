"""Read-only native adapter for Meta Engine's live health snapshot.

The adapter consumes the Meta Engine's existing observation state. It does not
start monitoring, run recovery, trigger probes, or mutate platform services.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from .contracts import EngineContribution, MissionTaskContext


class MetaEngineMissionAdapter:
    engine_id = "meta_engine"
    capabilities = frozenset({"platform.health.snapshot"})
    max_concurrency = 1
    timeout_seconds = 10.0

    def __init__(self, engine: Any | None = None) -> None:
        if engine is None:
            from services.meta_engine import meta_engine
            engine = meta_engine
        if not callable(getattr(engine, "get_status", None)):
            raise TypeError("meta_engine_status_api_unavailable")
        self.engine = engine

    async def invoke(self, context: MissionTaskContext) -> EngineContribution:
        started = datetime.now(timezone.utc)
        snapshot = self.engine.get_status()
        if not isinstance(snapshot, Mapping):
            raise TypeError("meta_engine_status_snapshot_invalid")

        engines = snapshot.get("engines")
        engines = engines if isinstance(engines, Mapping) else {}
        findings = []
        for name, health in list(engines.items())[:20]:
            if not isinstance(health, Mapping):
                continue
            status = str(health.get("status") or "unknown")
            score = health.get("health_score")
            findings.append(
                f"{str(name)[:100]}: {status}"
                + (f", health score {float(score):.1f}/100" if isinstance(score, (int, float)) else "")
            )

        timestamp = str(snapshot.get("timestamp") or "").strip()
        overall = str(snapshot.get("overall") or "unknown")
        health_score = snapshot.get("health_score")
        risk_score = snapshot.get("risk_score")
        summary = (
            f"Meta Engine's current platform snapshot is {overall}; "
            f"{len(engines)} engines are represented."
        )
        evidence = (
            {"ref": f"meta_engine:snapshot:{timestamp}", "observed_at": timestamp},
        ) if timestamp else ()

        return EngineContribution(
            engine_id=self.engine_id,
            capability=context.capability,
            task_id=context.task_id,
            mission_id=context.mission_id,
            workspace_id=context.workspace_id,
            status="completed" if timestamp and engines else "unavailable",
            summary=summary,
            findings=tuple(findings[:12]),
            recommendations=(
                "Use this as a current operational observation; independently verify critical findings before action.",
            ),
            evidence=evidence,
            limitations=(
                "This is Meta Engine's existing in-process snapshot, not an independent external verification.",
                "This adapter does not initiate health probes, diagnose new incidents, or run recovery actions.",
            ),
            confidence=0.6 if timestamp and engines else 0.0,
            source_metadata={
                "observed_at": timestamp or None,
                "overall": overall,
                "engine_count": len(engines),
                "health_score": health_score,
                "risk_score": risk_score,
                "active_incidents": snapshot.get("active_incidents"),
                "monitoring_running": (snapshot.get("monitoring") or {}).get("running")
                if isinstance(snapshot.get("monitoring"), Mapping) else None,
                "independent_verification": False,
                "recovery_invoked": False,
            },
            correlation_id=context.correlation_id,
            started_at=started,
            finished_at=datetime.now(timezone.utc),
            authority="none",
            side_effect_class="internal_observation",
        )
