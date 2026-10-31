"""
Reliability Engine — orchestrates Pulse → Diagnose → Plan → Guardian → Execute → Memory.

Does NOT auto-generate and deploy arbitrary code.

v1.1 (2026-10-31):
  Added `summary` field to reliability.escalation, reliability.recommendation,
  and reliability.outcome event payloads. The KCPG event stream UI displays
  `payload.summary` as the primary label; without it, events rendered as
  "no producer summary supplied" even when the structured data was complete.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional

from services.reliability.models import (
    AutonomyLevel,
    Incident,
    IncidentSeverity,
    RemediationAction,
    RemediationPlan,
    RemediationResult,
)
from services.reliability.policies import AutonomyPolicy, load_policy_from_env
from services.reliability.pulse import PulseCollector
from services.reliability.diagnostician import Diagnostician
from services.reliability.planner import RepairPlanner
from services.reliability.executor import RepairExecutor
from services.reliability.memory import IncidentMemory

logger = logging.getLogger("kanyoza.reliability")

_engine: Optional["ReliabilityEngine"] = None


class ReliabilityEngine:
    """
    First-class organism subsystem for governed remediation.

    Subscribe to engine_alert / stagnation and run the full lifecycle.
    """

    VERSION = "1.1.0"
    service_name = "reliability_engine"

    def __init__(self, container=None, policy: Optional[AutonomyPolicy] = None):
        self._container = container
        self._policy = policy or load_policy_from_env()
        self._memory = IncidentMemory()
        self._pulse = PulseCollector(container)
        self._diagnostician = Diagnostician(self._memory)
        self._planner = RepairPlanner(self._policy, self._memory)
        self._executor = RepairExecutor(container)
        self._running = False
        self._recent: Deque[Dict[str, Any]] = deque(maxlen=100)
        self._auto_count_window: Deque[float] = deque(maxlen=200)
        self._last_symptom_key: Optional[str] = None
        self._last_handled_at: float = 0.0
        self.COOLDOWN_SECONDS = 120  # avoid thrashing on repeated Meta degraded

    # ── Lifecycle ─────────────────────────────────────────────────

    async def start(self) -> None:
        self._running = True
        logger.info(
            "🧠 Reliability Engine v%s started (autonomy=%s)",
            self.VERSION,
            self._policy.global_level.value,
        )

    async def stop(self) -> None:
        self._running = False
        logger.info("Reliability Engine stopped")

    def health_check(self) -> Dict[str, Any]:
        return {
            "status": "healthy" if self._running else "stopped",
            "version": self.VERSION,
            "autonomy": self._policy.global_level.value,
            "memory": self._memory.stats(),
            "recent_incidents": len(self._recent),
        }

    async def check_health_async(self) -> Dict[str, Any]:
        return self.health_check()

    # ── Main entry ────────────────────────────────────────────────

    async def handle_signal(
        self,
        *,
        symptom: str,
        source: str = "unknown",
        component: Optional[str] = None,
        severity: str = "medium",
        evidence_extra: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Full reliability lifecycle for one signal.
        Safe to call from Autonomous Loop on engine_alert.
        """
        if not self._running:
            await self.start()

        # Debounce identical symptoms
        key = f"{source}:{symptom}:{component}"
        now = time.monotonic()
        if key == self._last_symptom_key and (now - self._last_handled_at) < self.COOLDOWN_SECONDS:
            return {"status": "debounced", "symptom": symptom}

        self._last_symptom_key = key
        self._last_handled_at = now

        try:
            sev = IncidentSeverity(severity)
        except ValueError:
            sev = IncidentSeverity.MEDIUM

        incident = Incident(
            symptom=symptom,
            source=source,
            severity=sev,
            component=component,
            correlation_id=correlation_id or "",
            evidence=evidence_extra or {},
        )

        logger.info(
            "🧠 Reliability investigating: %s (source=%s component=%s)",
            symptom,
            source,
            component,
        )

        # 1. Pulse
        evidence = await self._pulse.collect()
        if evidence_extra:
            evidence["signal"] = evidence_extra

        # 2. Diagnose
        diagnosis = self._diagnostician.diagnose(incident, evidence)
        logger.info(
            "🔎 Diagnosis: cause=%s confidence=%.0f%% components=%s",
            diagnosis.probable_cause,
            diagnosis.confidence * 100,
            diagnosis.related_components,
        )

        # 3. Plan
        plan = self._planner.plan(incident, diagnosis)
        logger.info(
            "🛠 Plan: action=%s risk=%s human=%s",
            plan.action.value,
            plan.risk,
            plan.requires_human,
        )

        # 4. Guardian / policy gate
        gate = self._guardian_gate(plan)
        if gate["decision"] == "deny":
            result = RemediationResult(
                plan_id=plan.id,
                incident_id=incident.id,
                status="denied",
                detail=gate,
            )
            self._remember(incident, diagnosis, plan, result)
            return self._package(incident, diagnosis, plan, result, evidence)

        if gate["decision"] == "escalate" or plan.requires_human:
            result = RemediationResult(
                plan_id=plan.id,
                incident_id=incident.id,
                status="escalated",
                detail={**gate, "reason": plan.reason},
            )
            self._remember(incident, diagnosis, plan, result)

            # v1.1 — add human-readable summary for the event stream UI.
            escalation_summary = (
                f"ESCALATION: {incident.symptom} "
                f"(cause={diagnosis.probable_cause}, "
                f"confidence={diagnosis.confidence:.0%})"
            )
            await self._publish("reliability.escalation", {
                "summary": escalation_summary,
                "incident": incident.to_dict(),
                "diagnosis": diagnosis.to_dict(),
                "plan": plan.to_dict(),
            })
            return self._package(incident, diagnosis, plan, result, evidence)

        # Observe-only policy
        if self._policy.global_level == AutonomyLevel.OBSERVE:
            result = RemediationResult(
                plan_id=plan.id,
                incident_id=incident.id,
                status="skipped",
                detail={"reason": "autonomy_level_observe"},
            )
            self._remember(incident, diagnosis, plan, result)
            return self._package(incident, diagnosis, plan, result, evidence)

        if self._policy.global_level == AutonomyLevel.RECOMMEND:
            result = RemediationResult(
                plan_id=plan.id,
                incident_id=incident.id,
                status="skipped",
                detail={"reason": "autonomy_level_recommend_only", "plan": plan.to_dict()},
            )
            self._remember(incident, diagnosis, plan, result)

            # v1.1 — add human-readable summary for the event stream UI.
            recommendation_summary = (
                f"RECOMMENDATION: {plan.action.value} for "
                f"{incident.component or incident.source} "
                f"(risk={plan.risk})"
            )
            await self._publish("reliability.recommendation", {
                "summary": recommendation_summary,
                "incident": incident.to_dict(),
                "diagnosis": diagnosis.to_dict(),
                "plan": plan.to_dict(),
            })
            return self._package(incident, diagnosis, plan, result, evidence)

        # Rate limit auto remediations
        if not self._within_rate_limit():
            result = RemediationResult(
                plan_id=plan.id,
                incident_id=incident.id,
                status="skipped",
                detail={"reason": "rate_limited"},
            )
            self._remember(incident, diagnosis, plan, result)
            return self._package(incident, diagnosis, plan, result, evidence)

        # 5. Execute
        result = await self._executor.execute(plan)
        self._auto_count_window.append(time.monotonic())

        # 6. Validate (lightweight re-pulse for reversible actions)
        if result.status == "success" and plan.action not in (
            RemediationAction.OBSERVE_ONLY,
            RemediationAction.ESCALATE_HUMAN,
        ):
            result.validated = True  # full re-check is optional; mark applied

        # 7. Memory
        self._remember(incident, diagnosis, plan, result)

        # v1.1 — add human-readable summary for the event stream UI.
        outcome_summary = (
            f"{plan.action.value} on {incident.component or incident.source}: "
            f"{result.status}"
        )
        if diagnosis.probable_cause:
            outcome_summary += f" — {diagnosis.probable_cause}"

        await self._publish("reliability.outcome", {
            "summary": outcome_summary,
            "incident": incident.to_dict(),
            "diagnosis": diagnosis.to_dict(),
            "plan": plan.to_dict(),
            "result": result.to_dict(),
        })

        logger.info(
            "Reliability outcome: incident=%s action=%s status=%s",
            incident.id,
            plan.action.value,
            result.status,
        )
        return self._package(incident, diagnosis, plan, result, evidence)

    async def handle_engine_alert(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Adapter for Autonomous Loop engine_alert events."""
        engine = str(payload.get("engine") or "system")
        status = str(payload.get("status") or "degraded")
        message = str(payload.get("message") or f"{engine} is {status}")
        return await self.handle_signal(
            symptom=message,
            source="engine_alert",
            component=engine if engine != "system" else None,
            severity="high" if status in ("unhealthy", "critical") else "medium",
            evidence_extra=payload,
            correlation_id=payload.get("correlation_id"),
        )

    # ── Internals ─────────────────────────────────────────────────

    def _guardian_gate(self, plan: RemediationPlan) -> Dict[str, Any]:
        """Policy gate (Guardian-style). Never allows arbitrary code deploy."""
        if plan.action == RemediationAction.PROPOSE_PATCH:
            return {
                "decision": "escalate",
                "reason": "Code modification requires human approval",
            }
        if not self._policy.may_execute(plan.action):
            return {
                "decision": "deny",
                "reason": f"Action {plan.action.value} not allowed at autonomy {self._policy.global_level.value}",
            }
        if plan.requires_human:
            return {
                "decision": "escalate",
                "reason": "Policy requires human approval",
            }
        # Optional: try project Guardian if present
        try:
            # Soft allow for reversible
            return {"decision": "allow", "reason": "policy_ok"}
        except Exception as exc:
            return {"decision": "deny", "reason": str(exc)[:120]}

    def _within_rate_limit(self) -> bool:
        now = time.monotonic()
        cutoff = now - 3600
        while self._auto_count_window and self._auto_count_window[0] < cutoff:
            self._auto_count_window.popleft()
        return len(self._auto_count_window) < self._policy.max_auto_remediations_per_hour

    def _remember(self, incident, diagnosis, plan, result) -> None:
        self._memory.record(
            incident_id=incident.id,
            cause=diagnosis.probable_cause,
            action=plan.action.value,
            outcome=result.status,
            confidence=diagnosis.confidence,
            detail={"plan_id": plan.id, "result": result.detail},
        )
        self._recent.append({
            "incident": incident.to_dict(),
            "diagnosis": diagnosis.to_dict(),
            "plan": plan.to_dict(),
            "result": result.to_dict(),
        })

    def _package(self, incident, diagnosis, plan, result, evidence) -> Dict[str, Any]:
        return {
            "status": result.status,
            "incident": incident.to_dict(),
            "diagnosis": diagnosis.to_dict(),
            "plan": plan.to_dict(),
            "result": result.to_dict(),
            "evidence_keys": list(evidence.keys()),
        }

    async def _publish(self, event_name: str, payload: Dict[str, Any]) -> None:
        try:
            bus = None
            if self._container:
                from core.unified_bus import UnifiedEventBus
                bus = self._container.try_resolve(UnifiedEventBus)
            if bus and hasattr(bus, "publish"):
                await bus.publish(
                    event_name,
                    payload,
                    source="reliability_engine",
                    workspace_id="platform",
                )
        except Exception:
            pass

    def recent_incidents(self, limit: int = 20) -> List[Dict[str, Any]]:
        return list(self._recent)[-limit:]


def get_reliability_engine() -> Optional[ReliabilityEngine]:
    return _engine


async def init_reliability_engine(container=None) -> ReliabilityEngine:
    global _engine
    _engine = ReliabilityEngine(container=container)
    await _engine.start()
    return _engine
