"""Offline dreaming and bounded simulation for the organism.

Dreams are hypothetical evaluations only. They never call connectors, mutate the
execution ledger, grant authority, or mutate the production world model/memory.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Mapping, Sequence

from organism.goals import Goal
from organism.memory import MemoryKind, MemoryStore, Outcome
from organism.prediction import PredictiveCausalEngine
from organism.world_model import WorldModel


@dataclass(frozen=True)
class DreamStep:
    action: str
    predicted_outcome: Outcome
    probability: float
    confidence: float
    expected_value: float


@dataclass(frozen=True)
class Dream:
    workspace_id: str
    goal_id: str | None
    steps: tuple[DreamStep, ...]
    score: float
    risk: float
    confidence: float
    assumptions: tuple[str, ...]
    simulated_at: datetime
    production_mutated: bool = False


class DreamEngine:
    """Evaluate hypothetical action sequences using a read-only knowledge snapshot."""

    MAX_STEPS = 20

    def __init__(self, memory: MemoryStore, world_model: WorldModel, predictor: PredictiveCausalEngine | None = None) -> None:
        self.memory = memory
        self.world_model = world_model
        self.predictor = predictor or PredictiveCausalEngine(memory, world_model)

    def simulate(
        self,
        workspace_id: str,
        actions: Sequence[str],
        *,
        goal: Goal | None = None,
        constraints: Mapping[str, object] | None = None,
    ) -> Dream:
        if goal is not None and goal.workspace_id != workspace_id:
            raise ValueError("goal workspace_id does not match workspace_id")
        if not actions or len(actions) > self.MAX_STEPS:
            raise ValueError(f"actions must contain 1-{self.MAX_STEPS} items")
        if any(not str(action).strip() for action in actions):
            raise ValueError("actions must be non-empty")

        # Snapshot before simulation: prediction itself is observationally derived.
        # We intentionally do not call world_model.observe() from the dream path.
        steps: list[DreamStep] = []
        previous_action: str | None = None
        assumptions: list[str] = ["simulation uses historical tenant-scoped experience"]
        total_score = 0.0
        risks: list[float] = []
        confidences: list[float] = []
        for action in actions:
            prediction = self.predictor.predict_action(workspace_id, str(action), persist=False)
            value = {
                Outcome.SUCCESS: 1.0,
                Outcome.PARTIAL: 0.35,
                Outcome.FAILURE: -1.0,
                Outcome.UNCERTAIN: -0.25,
            }[prediction.predicted_outcome]
            expected_value = value * prediction.probability
            # Sequential actions are not independent in the real world. We do
            # not pretend to know transition dynamics, so correlated sequences
            # receive a bounded uncertainty penalty instead of fabricated causality.
            if previous_action is not None and previous_action == str(action):
                expected_value *= 0.9
                assumptions.append(f"repeated action:{action} receives bounded sequence-uncertainty penalty")
            previous_action = str(action)
            steps.append(DreamStep(str(action), prediction.predicted_outcome, prediction.probability, prediction.confidence, expected_value))
            total_score += expected_value
            risks.append(1.0 - prediction.probability if prediction.predicted_outcome is Outcome.SUCCESS else prediction.probability)
            confidences.append(prediction.confidence)

        if constraints and constraints.get("require_success") is True:
            assumptions.append("goal constraint requires successful predicted outcomes")
            if any(step.predicted_outcome is not Outcome.SUCCESS for step in steps):
                total_score -= 0.5
        if goal is not None:
            assumptions.append(f"goal:{goal.goal_id} is evaluated but not modified")

        risk = max(0.0, min(1.0, sum(risks) / len(risks)))
        confidence = min(confidences) if confidences else 0.0
        return Dream(
            workspace_id=workspace_id,
            goal_id=goal.goal_id if goal else None,
            steps=tuple(steps),
            score=total_score / len(steps),
            risk=risk,
            confidence=confidence,
            assumptions=tuple(assumptions),
            simulated_at=datetime.now(timezone.utc),
        )

    def compare(self, workspace_id: str, candidates: Iterable[Sequence[str]], *, goal: Goal | None = None) -> tuple[Dream, ...]:
        dreams = tuple(self.simulate(workspace_id, actions, goal=goal) for actions in candidates)
        return tuple(sorted(dreams, key=lambda dream: (dream.score, dream.confidence, -dream.risk), reverse=True))
