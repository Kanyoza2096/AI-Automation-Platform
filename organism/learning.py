"""Bounded learning plane: convert execution receipts into experience and beliefs.

Learning is downstream of durable execution truth. It can enrich memory and the
world model, but it cannot change execution authority, grant permissions, or
turn uncertain outcomes into success.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from organism.goals import Goal
from organism.memory import Experience, Memory, MemoryStore, Outcome
from organism.world_model import BeliefStatus, WorldFact, WorldModel
from services.execution_ledger import ExecutionRecord


@dataclass(frozen=True)
class ExecutionFeedback:
    """Normalized feedback derived from an authoritative execution receipt."""

    workspace_id: str
    execution_id: str
    action: str
    outcome: Outcome
    provider: Optional[str]
    provider_object_id: Optional[str]
    provider_request_id: Optional[str]
    evidence: Mapping[str, Any]
    error: Optional[str]
    occurred_at: datetime
    confidence: float
    goal_id: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.workspace_id or not self.execution_id or not self.action:
            raise ValueError("workspace_id, execution_id and action are required")
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("confidence must be between 0 and 1")

    @classmethod
    def from_record(cls, record: ExecutionRecord) -> "ExecutionFeedback":
        outcome = {
            "succeeded": Outcome.SUCCESS,
            "failed": Outcome.FAILURE,
            "uncertain": Outcome.UNCERTAIN,
            "cancelled": Outcome.FAILURE,
            "started": Outcome.UNCERTAIN,
        }.get(record.status, Outcome.UNCERTAIN)
        confidence = 1.0 if outcome in {Outcome.SUCCESS, Outcome.FAILURE} else 0.35
        return cls(
            workspace_id=record.workspace_id,
            execution_id=record.execution_id,
            action=record.action,
            outcome=outcome,
            provider=record.provider,
            provider_object_id=record.provider_object_id,
            provider_request_id=record.provider_request_id,
            evidence=record.evidence or {},
            error=record.error,
            goal_id=(record.evidence or {}).get("goal_id"),
            occurred_at=datetime.now(timezone.utc),
            confidence=confidence,
        )


@dataclass(frozen=True)
class LearningResult:
    """What the learner derived; no field represents execution authority."""

    workspace_id: str
    experience: Experience
    memory: Memory
    facts: tuple[WorldFact, ...]
    lesson: Optional[str]
    goal_progress: Optional[float]


class LearningEngine:
    """Translate durable execution feedback into bounded organism learning."""

    def __init__(self, memory: MemoryStore, world_model: WorldModel) -> None:
        self.memory = memory
        self.world_model = world_model

    def learn(
        self,
        feedback: ExecutionFeedback,
        *,
        goal: Optional[Goal] = None,
        result: Optional[Mapping[str, Any]] = None,
    ) -> LearningResult:
        if goal is not None and goal.workspace_id != feedback.workspace_id:
            raise ValueError("goal workspace_id does not match execution feedback")
        if feedback.goal_id is not None and goal is not None and feedback.goal_id != goal.goal_id:
            raise ValueError("goal_id does not match execution feedback")
        payload = dict(result or {})
        lesson = self._lesson(feedback)
        experience = Experience(
            workspace_id=feedback.workspace_id,
            goal_id=goal.goal_id if goal else feedback.goal_id,
            action=feedback.action,
            outcome=feedback.outcome,
            result={
                **payload,
                "execution_id": feedback.execution_id,
                "provider": feedback.provider,
                "provider_object_id": feedback.provider_object_id,
                "provider_request_id": feedback.provider_request_id,
                "evidence": dict(feedback.evidence),
                "error": feedback.error,
            },
            lesson=lesson,
            confidence=feedback.confidence,
            occurred_at=feedback.occurred_at,
        )
        memory = self.memory.record_experience(experience)
        facts = self._observe_feedback(feedback, goal=goal)
        progress = self._goal_progress(feedback, goal, payload)
        return LearningResult(
            workspace_id=feedback.workspace_id,
            experience=experience,
            memory=memory,
            facts=facts,
            lesson=lesson,
            goal_progress=progress,
        )

    def learn_from_record(
        self,
        record: ExecutionRecord,
        *,
        goal: Optional[Goal] = None,
        result: Optional[Mapping[str, Any]] = None,
    ) -> LearningResult:
        return self.learn(ExecutionFeedback.from_record(record), goal=goal, result=result)

    @staticmethod
    def _lesson(feedback: ExecutionFeedback) -> Optional[str]:
        if feedback.outcome is Outcome.SUCCESS:
            return f"Action '{feedback.action}' completed successfully; retain its evidence as a proven path."
        if feedback.outcome is Outcome.FAILURE:
            return f"Action '{feedback.action}' failed; future planning should account for the recorded failure evidence."
        return f"Action '{feedback.action}' has an uncertain external outcome; reconcile before treating it as successful."

    def _observe_feedback(self, feedback: ExecutionFeedback, *, goal: Optional[Goal]) -> tuple[WorldFact, ...]:
        facts = [
            WorldFact(
                workspace_id=feedback.workspace_id,
                subject=f"execution:{feedback.execution_id}",
                predicate="status",
                value=feedback.outcome.value,
                status=BeliefStatus.KNOWN if feedback.outcome is not Outcome.UNCERTAIN else BeliefStatus.UNCERTAIN,
                confidence=feedback.confidence,
                source="execution_ledger",
                observed_at=feedback.occurred_at,
            ),
            WorldFact(
                workspace_id=feedback.workspace_id,
                subject=f"action:{feedback.action}",
                predicate="last_outcome",
                value=feedback.outcome.value,
                status=BeliefStatus.KNOWN if feedback.outcome is not Outcome.UNCERTAIN else BeliefStatus.UNCERTAIN,
                confidence=feedback.confidence,
                source="execution_ledger",
                observed_at=feedback.occurred_at,
            ),
        ]
        if goal:
            facts.append(WorldFact(
                workspace_id=feedback.workspace_id,
                subject=f"goal:{goal.goal_id}",
                predicate="last_action_outcome",
                value={"action": feedback.action, "outcome": feedback.outcome.value},
                status=BeliefStatus.KNOWN if feedback.outcome is not Outcome.UNCERTAIN else BeliefStatus.UNCERTAIN,
                confidence=feedback.confidence,
                source="execution_ledger",
                observed_at=feedback.occurred_at,
            ))
        return tuple(self.world_model.observe(fact) for fact in facts)

    @staticmethod
    def _goal_progress(
        feedback: ExecutionFeedback,
        goal: Optional[Goal],
        result: Mapping[str, Any],
    ) -> Optional[float]:
        if goal is None or feedback.outcome is Outcome.UNCERTAIN:
            return None
        proposed = result.get("goal_progress")
        if proposed is None:
            return 1.0 if feedback.outcome is Outcome.SUCCESS and result.get("goal_complete") is True else None
        try:
            value = max(0.0, min(1.0, float(proposed)))
        except (TypeError, ValueError):
            return None
        # Learning may propose progress; the immutable Goal remains the caller's authority.
        return max(float(goal.progress), value)
