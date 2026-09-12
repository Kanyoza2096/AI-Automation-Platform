"""Bounded prediction and causal-hypothesis plane for the organism.

Predictions are derived evidence, never execution authority.  Causal outputs are
explicitly hypotheses: observational experience alone cannot establish causality.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import exp
from typing import Any, Mapping, Optional

from organism.memory import MemoryKind, MemoryStore, Outcome
from organism.world_model import BeliefStatus, WorldFact, WorldModel


@dataclass(frozen=True)
class Prediction:
    workspace_id: str
    action: str
    predicted_outcome: Outcome
    probability: float
    confidence: float
    sample_size: int
    basis: str
    generated_at: datetime
    context_key: str = "default"
    contextual_sample_size: int = 0
    outcome_probabilities: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.workspace_id.strip() or not self.action.strip():
            raise ValueError("workspace_id and action are required")
        for field_name in ("probability", "confidence"):
            value = float(getattr(self, field_name))
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{field_name} must be between 0 and 1")
        if self.sample_size < 0 or self.contextual_sample_size < 0:
            raise ValueError("sample sizes must be non-negative")
        if not str(self.context_key).strip():
            raise ValueError("context_key is required")
        if self.outcome_probabilities:
            total_probability = sum(float(v) for v in self.outcome_probabilities.values())
            if any(not 0.0 <= float(v) <= 1.0 for v in self.outcome_probabilities.values()):
                raise ValueError("outcome probabilities must be between 0 and 1")
            if abs(total_probability - 1.0) > 1e-6:
                raise ValueError("outcome probabilities must sum to 1")
        if self.generated_at.tzinfo is None:
            raise ValueError("generated_at must be timezone-aware")


@dataclass(frozen=True)
class CausalHypothesis:
    """A bounded action/outcome association, not proof of causation."""

    workspace_id: str
    cause: str
    effect: str
    direction: str
    strength: float
    confidence: float
    sample_size: int
    caveat: str = "Observational association; causality is not established."

    def __post_init__(self) -> None:
        if not self.workspace_id.strip() or not self.cause.strip() or not self.effect.strip():
            raise ValueError("workspace_id, cause and effect are required")
        if self.direction not in {"positive", "negative", "neutral"}:
            raise ValueError("direction must be positive, negative, or neutral")
        for field_name in ("strength", "confidence"):
            value = float(getattr(self, field_name))
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{field_name} must be between 0 and 1")
        if self.sample_size < 0:
            raise ValueError("sample_size must be non-negative")



@dataclass(frozen=True)
class CalibrationSample:
    """A realized prediction/outcome pair used only to measure forecast quality."""

    workspace_id: str
    action: str
    context_key: str
    predicted_outcome: Outcome
    realized_outcome: Outcome
    probability: float
    confidence: float
    occurred_at: datetime

    def __post_init__(self) -> None:
        if not self.workspace_id.strip() or not self.action.strip():
            raise ValueError("workspace_id and action are required")
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        for name in ("probability", "confidence"):
            value = float(getattr(self, name))
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")


@dataclass(frozen=True)
class CalibrationReport:
    """Empirical forecast-quality metrics; never used as execution authority."""

    sample_size: int
    brier_score: Optional[float]
    expected_calibration_error: Optional[float]
    accuracy: Optional[float]
    context_sample_sizes: Mapping[str, int] = field(default_factory=dict)
    stale_or_unresolved_samples: int = 0


class PredictionCalibrator:
    """Bounded empirical calibration ledger for predictions and realized outcomes."""

    def __init__(self, *, history_limit: int = 1000) -> None:
        if history_limit < 1:
            raise ValueError("history_limit must be positive")
        self.history_limit = history_limit
        self._samples: list[CalibrationSample] = []

    def record(self, prediction: Prediction, realized_outcome: Outcome) -> CalibrationSample:
        if realized_outcome is Outcome.UNCERTAIN:
            raise ValueError("uncertain outcomes cannot calibrate a prediction")
        probability = float(prediction.probability)
        sample = CalibrationSample(
            workspace_id=prediction.workspace_id,
            action=prediction.action,
            context_key=prediction.context_key,
            predicted_outcome=prediction.predicted_outcome,
            realized_outcome=realized_outcome,
            probability=probability,
            confidence=prediction.confidence,
            occurred_at=datetime.now(timezone.utc),
        )
        self._samples.append(sample)
        if len(self._samples) > self.history_limit:
            del self._samples[:len(self._samples) - self.history_limit]
        return sample

    def evaluate(self, *, workspace_id: Optional[str] = None, action: Optional[str] = None, context_key: Optional[str] = None) -> CalibrationReport:
        samples = [s for s in self._samples if (workspace_id is None or s.workspace_id == workspace_id) and (action is None or s.action == action) and (context_key is None or s.context_key == context_key)]
        if not samples:
            return CalibrationReport(0, None, None, None, {})
        brier = sum((s.probability - (1.0 if s.predicted_outcome is s.realized_outcome else 0.0)) ** 2 for s in samples) / len(samples)
        accuracy = sum(s.predicted_outcome is s.realized_outcome for s in samples) / len(samples)
        bins: dict[int, list[CalibrationSample]] = {}
        for sample in samples:
            bucket = min(9, int(sample.probability * 10))
            bins.setdefault(bucket, []).append(sample)
        ece = 0.0
        for bucket_samples in bins.values():
            mean_probability = sum(s.probability for s in bucket_samples) / len(bucket_samples)
            observed_accuracy = sum(s.predicted_outcome is s.realized_outcome for s in bucket_samples) / len(bucket_samples)
            ece += (len(bucket_samples) / len(samples)) * abs(mean_probability - observed_accuracy)
        context_sizes: dict[str, int] = {}
        for sample in samples:
            context_sizes[sample.context_key] = context_sizes.get(sample.context_key, 0) + 1
        return CalibrationReport(len(samples), brier, ece, accuracy, context_sizes)

    def samples(self) -> tuple[CalibrationSample, ...]:
        return tuple(self._samples)


class PredictiveCausalEngine:
    """Infer bounded forecasts and causal hypotheses from episodic experience."""

    def __init__(self, memory: MemoryStore, world_model: WorldModel, *, history_limit: int = 200) -> None:
        if history_limit < 1:
            raise ValueError("history_limit must be positive")
        self.memory = memory
        self.world_model = world_model
        self.history_limit = history_limit

    @staticmethod
    def context_key(context: Mapping[str, Any] | None) -> str:
        """Return a deterministic, bounded context fingerprint.

        Only declarative scalar fields are used so prediction cannot accidentally
        depend on secrets, credentials, or arbitrary nested runtime objects.
        """
        if not context:
            return "default"
        allowed = ("platform", "content_type", "audience", "time_bucket", "goal_id", "system_state")
        values: list[str] = []
        for key in allowed:
            value = context.get(key)
            if value is None:
                continue
            text = str(value).strip().lower()[:80]
            if text:
                values.append(f"{key}={text}")
        return "|".join(values)[:512] or "default"

    def predict_action(
        self, workspace_id: str, action: str, *,
        context: Mapping[str, Any] | None = None, persist: bool = True
    ) -> Prediction:
        memories = self.memory.recent(workspace_id, kind=MemoryKind.EPISODIC, limit=self.history_limit)
        outcomes: list[Outcome] = []
        key = self.context_key(context)
        contextual = 0
        for memory in memories:
            content = memory.content
            if content.get("action") != action:
                continue
            memory_result = content.get("result") or {}
            memory_context = None
            if isinstance(memory_result, Mapping):
                memory_context = memory_result.get("context")
                if not isinstance(memory_context, Mapping):
                    evidence = memory_result.get("evidence")
                    if isinstance(evidence, Mapping):
                        memory_context = evidence.get("context")
            memory_key = self.context_key(memory_context if isinstance(memory_context, Mapping) else None)
            if memory_key != key:
                continue
            try:
                outcome = Outcome(str(content.get("outcome")))
            except ValueError:
                continue
            if outcome in {Outcome.SUCCESS, Outcome.FAILURE, Outcome.PARTIAL}:
                outcomes.append(outcome)
                contextual += 1

        # If the requested context has no observations, use the tenant/action
        # history rather than inventing a context-specific estimate.
        if not outcomes and key != "default":
            key = "default"
            for memory in memories:
                content = memory.content
                if content.get("action") != action:
                    continue
                try:
                    outcome = Outcome(str(content.get("outcome")))
                except ValueError:
                    continue
                if outcome in {Outcome.SUCCESS, Outcome.FAILURE, Outcome.PARTIAL}:
                    outcomes.append(outcome)

        # Laplace smoothing avoids false certainty with small samples.
        successes = sum(o is Outcome.SUCCESS for o in outcomes)
        failures = sum(o is Outcome.FAILURE for o in outcomes)
        partials = sum(o is Outcome.PARTIAL for o in outcomes)
        total = len(outcomes)
        success_probability = (successes + 1.0) / (total + 3.0)
        failure_probability = (failures + 1.0) / (total + 3.0)
        partial_probability = (partials + 1.0) / (total + 3.0)
        probabilities = {
            Outcome.SUCCESS: success_probability,
            Outcome.FAILURE: failure_probability,
            Outcome.PARTIAL: partial_probability,
        }
        predicted = max(probabilities, key=probabilities.get)
        probability = probabilities[predicted]
        confidence = 0.1 if not total else min(1.0, (1.0 - exp(-total / 8.0)) * 0.9 + 0.1)
        generated = datetime.now(timezone.utc)
        prediction = Prediction(
            workspace_id=workspace_id,
            action=action,
            predicted_outcome=predicted,
            probability=probability,
            confidence=confidence,
            sample_size=total,
            basis="tenant-scoped episodic execution experience",
            generated_at=generated,
            context_key=key,
            contextual_sample_size=contextual,
            outcome_probabilities={outcome.value: probabilities[outcome] for outcome in probabilities},
        )
        if persist:
            self.world_model.observe(WorldFact(
                workspace_id=workspace_id,
                subject=f"action:{action}",
                predicate="predicted_outcome",
                value=predicted.value,
                status=BeliefStatus.PREDICTED,
                confidence=confidence,
                source="organism_prediction",
                observed_at=generated,
            ))
        return prediction

    def causal_hypothesis(self, workspace_id: str, action: str) -> CausalHypothesis:
        memories = self.memory.recent(workspace_id, kind=MemoryKind.EPISODIC, limit=self.history_limit)
        outcomes: list[Outcome] = []
        for memory in memories:
            if memory.content.get("action") != action:
                continue
            try:
                outcome = Outcome(str(memory.content.get("outcome")))
            except ValueError:
                continue
            if outcome is not Outcome.UNCERTAIN:
                outcomes.append(outcome)

        total = len(outcomes)
        if not total:
            return CausalHypothesis(workspace_id, action, "successful_execution", "neutral", 0.0, 0.0, 0)
        success_rate = sum(o is Outcome.SUCCESS for o in outcomes) / total
        strength = abs(success_rate - 0.5) * 2.0
        direction = "positive" if success_rate > 0.5 else "negative" if success_rate < 0.5 else "neutral"
        confidence = min(1.0, total / 20.0) * strength
        return CausalHypothesis(
            workspace_id=workspace_id,
            cause=action,
            effect="successful_execution",
            direction=direction,
            strength=strength,
            confidence=confidence,
            sample_size=total,
        )
