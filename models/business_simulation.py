"""Phase 5 — canonical Business Simulation & Scenario contracts.

Simulation is an offline projection. It cannot mutate production state, grant
authority, create ActionIntents, or bypass governance.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

SCHEMA = "KDO.BusinessSimulation/v1"


class ScenarioDisposition(str, Enum):
    FAVORABLE = "FAVORABLE"
    CONDITIONAL = "CONDITIONAL"
    UNFAVORABLE = "UNFAVORABLE"
    INCONCLUSIVE = "INCONCLUSIVE"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class ScenarioAction:
    action: str
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.action.strip():
            raise ValueError("scenario action is required")


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    workspace_id: str
    name: str
    objective: str = ""
    actions: tuple[ScenarioAction, ...] = ()
    assumptions: tuple[str, ...] = ()
    constraints: Mapping[str, Any] = field(default_factory=dict)
    source_decision_id: str | None = None
    graph_node_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.scenario_id.strip() or not self.workspace_id.strip() or not self.name.strip():
            raise ValueError("scenario_id, workspace_id and name are required")
        if not self.actions:
            raise ValueError("scenario requires at least one action")


@dataclass(frozen=True)
class ScenarioOutcome:
    action: str
    predicted_outcome: str
    probability: float
    confidence: float
    expected_value: float


@dataclass(frozen=True)
class ScenarioResult:
    scenario_id: str
    workspace_id: str
    disposition: ScenarioDisposition
    score: float
    risk: float
    confidence: float
    outcomes: tuple[ScenarioOutcome, ...]
    assumptions: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    compared_to: tuple[str, ...] = ()
    fingerprint: str = ""
    production_mutated: bool = False
    authority_granted: bool = False
    execution_started: bool = False

    def __post_init__(self) -> None:
        if not self.workspace_id.strip() or not self.scenario_id.strip():
            raise ValueError("scenario_id and workspace_id are required")
        if not 0.0 <= self.confidence <= 1.0 or not 0.0 <= self.risk <= 1.0:
            raise ValueError("risk and confidence must be between 0 and 1")
        if self.production_mutated or self.authority_granted or self.execution_started:
            raise ValueError("simulation result cannot carry production authority or execution state")


@dataclass(frozen=True)
class BusinessSimulation:
    schema: str
    workspace_id: str
    scenarios: tuple[ScenarioResult, ...]
    best_scenario_id: str | None = None
    warnings: tuple[str, ...] = ()
    coverage: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.schema != SCHEMA or not self.workspace_id.strip():
            raise ValueError("invalid simulation snapshot")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "workspace_id": self.workspace_id,
            "scenarios": [
                {
                    "scenario_id": r.scenario_id,
                    "disposition": r.disposition.value,
                    "score": r.score,
                    "risk": r.risk,
                    "confidence": r.confidence,
                    "outcomes": [o.__dict__ for o in r.outcomes],
                    "assumptions": list(r.assumptions),
                    "evidence_refs": list(r.evidence_refs),
                    "compared_to": list(r.compared_to),
                    "fingerprint": r.fingerprint,
                    "production_mutated": r.production_mutated,
                    "authority_granted": r.authority_granted,
                    "execution_started": r.execution_started,
                }
                for r in self.scenarios
            ],
            "best_scenario_id": self.best_scenario_id,
            "warnings": list(self.warnings),
            "coverage": dict(self.coverage),
            "authority": {
                "read_only": True,
                "offline": True,
                "does_not_execute": True,
                "does_not_authorize": True,
                "does_not_mutate_production": True,
            },
        }
