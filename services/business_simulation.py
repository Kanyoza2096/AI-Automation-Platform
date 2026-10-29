"""Phase 5 — scenario/simulation projection over KDO's existing DreamEngine.

This service does not replace the existing offline DreamEngine. It translates
its hypothetical predictions into a business-level scenario comparison model.
No connector, Action Kernel, ledger, provider or production state is touched.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

from models.business_simulation import (
    BusinessSimulation,
    Scenario,
    ScenarioDisposition,
    ScenarioOutcome,
    ScenarioResult,
    SCHEMA,
)


class BusinessSimulationProjector:
    """Project already-computed offline dreams into a canonical scenario model."""

    def project(
        self,
        *,
        workspace_id: str,
        scenarios: Iterable[Scenario],
        dreams: Mapping[str, Any] | None = None,
        evidence_refs: Mapping[str, Iterable[str]] | None = None,
        blocked_scenario_ids: Iterable[str] = (),
    ) -> BusinessSimulation:
        ws = str(workspace_id or "").strip()
        if not ws:
            raise ValueError("workspace_id is required")

        dreams = dreams or {}
        evidence_refs = evidence_refs or {}
        blocked = {str(x) for x in blocked_scenario_ids}
        accepted: list[Scenario] = []
        warnings: list[str] = []
        seen: set[str] = set()

        for scenario in scenarios:
            if scenario.workspace_id != ws:
                warnings.append(f"scenario_workspace_mismatch:{scenario.scenario_id}")
                continue
            if scenario.scenario_id in seen:
                warnings.append(f"duplicate_scenario:{scenario.scenario_id}")
                continue
            seen.add(scenario.scenario_id)
            accepted.append(scenario)

        results: list[ScenarioResult] = []
        for scenario in sorted(accepted, key=lambda x: x.scenario_id):
            if scenario.scenario_id in blocked:
                results.append(self._blocked(scenario))
                continue
            dream = dreams.get(scenario.scenario_id)
            if dream is None:
                warnings.append(f"scenario_missing_simulation:{scenario.scenario_id}")
                results.append(self._inconclusive(scenario, "no offline simulation result supplied"))
                continue
            if getattr(dream, "workspace_id", ws) != ws:
                warnings.append(f"dream_workspace_mismatch:{scenario.scenario_id}")
                results.append(self._inconclusive(scenario, "simulation workspace mismatch"))
                continue
            results.append(self._from_dream(scenario, dream, tuple(str(x) for x in evidence_refs.get(scenario.scenario_id, ()))))

        ranked = sorted(results, key=lambda r: (r.disposition is ScenarioDisposition.BLOCKED, -r.score, r.risk, -r.confidence, r.scenario_id))
        best = next((r.scenario_id for r in ranked if r.disposition in {ScenarioDisposition.FAVORABLE, ScenarioDisposition.CONDITIONAL}), None)
        compared = tuple(r.scenario_id for r in ranked)
        results = [
            ScenarioResult(
                scenario_id=r.scenario_id,
                workspace_id=r.workspace_id,
                disposition=r.disposition,
                score=r.score,
                risk=r.risk,
                confidence=r.confidence,
                outcomes=r.outcomes,
                assumptions=r.assumptions,
                evidence_refs=r.evidence_refs,
                compared_to=compared,
                fingerprint=r.fingerprint,
                production_mutated=r.production_mutated,
                authority_granted=r.authority_granted,
                execution_started=r.execution_started,
            ) for r in sorted(results, key=lambda x: x.scenario_id)
        ]
        if not results:
            warnings.append("no_scenarios")
        return BusinessSimulation(
            schema=SCHEMA,
            workspace_id=ws,
            scenarios=tuple(results),
            best_scenario_id=best,
            warnings=tuple(sorted(set(warnings))),
            coverage={
                "scenarios": len(results),
                "simulated": sum(1 for r in results if r.disposition != ScenarioDisposition.INCONCLUSIVE),
                "blocked": sum(1 for r in results if r.disposition == ScenarioDisposition.BLOCKED),
                "inconclusive": sum(1 for r in results if r.disposition == ScenarioDisposition.INCONCLUSIVE),
            },
        )

    def _from_dream(self, scenario: Scenario, dream: Any, evidence: tuple[str, ...]) -> ScenarioResult:
        outcomes = tuple(
            ScenarioOutcome(
                action=str(getattr(step, "action", "")),
                predicted_outcome=getattr(getattr(step, "predicted_outcome", ""), "value", str(getattr(step, "predicted_outcome", ""))),
                probability=float(getattr(step, "probability", 0.0)),
                confidence=float(getattr(step, "confidence", 0.0)),
                expected_value=float(getattr(step, "expected_value", 0.0)),
            )
            for step in getattr(dream, "steps", ())
        )
        score = float(getattr(dream, "score", 0.0))
        risk = float(getattr(dream, "risk", 1.0))
        confidence = float(getattr(dream, "confidence", 0.0))
        disposition = self._disposition(score, risk, confidence)
        assumptions = tuple(str(x) for x in getattr(dream, "assumptions", ())) + tuple(scenario.assumptions)
        fingerprint = self._fingerprint(scenario, outcomes, score, risk, confidence)
        return ScenarioResult(scenario.scenario_id, scenario.workspace_id, disposition, score, risk, confidence, outcomes, assumptions, evidence_refs=evidence, fingerprint=fingerprint)

    @staticmethod
    def _disposition(score: float, risk: float, confidence: float) -> ScenarioDisposition:
        if confidence < 0.25:
            return ScenarioDisposition.INCONCLUSIVE
        if risk >= 0.8 or score < -0.25:
            return ScenarioDisposition.UNFAVORABLE
        if risk >= 0.55 or confidence < 0.5:
            return ScenarioDisposition.CONDITIONAL
        return ScenarioDisposition.FAVORABLE

    @staticmethod
    def _fingerprint(scenario: Scenario, outcomes: tuple[ScenarioOutcome, ...], score: float, risk: float, confidence: float) -> str:
        payload = {
            "scenario_id": scenario.scenario_id,
            "workspace_id": scenario.workspace_id,
            "actions": [{"action": a.action, "parameters": dict(a.parameters)} for a in scenario.actions],
            "outcomes": [o.__dict__ for o in outcomes],
            "score": score,
            "risk": risk,
            "confidence": confidence,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()

    @staticmethod
    def _blocked(scenario: Scenario) -> ScenarioResult:
        return ScenarioResult(scenario.scenario_id, scenario.workspace_id, ScenarioDisposition.BLOCKED, -1.0, 1.0, 0.0, (), ("scenario blocked by upstream governance",), fingerprint=BusinessSimulationProjector._simple_fingerprint(scenario))

    @staticmethod
    def _inconclusive(scenario: Scenario, reason: str) -> ScenarioResult:
        return ScenarioResult(scenario.scenario_id, scenario.workspace_id, ScenarioDisposition.INCONCLUSIVE, 0.0, 1.0, 0.0, (), (reason,), fingerprint=BusinessSimulationProjector._simple_fingerprint(scenario))

    @staticmethod
    def _simple_fingerprint(scenario: Scenario) -> str:
        raw = json.dumps({"id": scenario.scenario_id, "workspace": scenario.workspace_id, "actions": [a.action for a in scenario.actions]}, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()


__all__ = ["BusinessSimulationProjector", "SCHEMA"]
