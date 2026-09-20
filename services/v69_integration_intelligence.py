"""V69.19 — governed integration intelligence and optimization.

Optimization is advisory only. It ranks already-authorized alternatives and
surfaces anomalies; it never grants authority, changes policy, calls providers,
or executes actions.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from math import isfinite
from statistics import mean
from typing import Mapping, Sequence


class OptimizationBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    INVALID_METRIC = "invalid_metric"
    NO_CANDIDATES = "no_candidates"
    FORBIDDEN_AUTHORITY = "optimizer_cannot_grant_authority"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


@dataclass(frozen=True)
class IntegrationMetric:
    workspace_id: str
    connector_key: str
    action: str
    samples: int
    success_rate: float
    ambiguous_rate: float = 0.0
    p95_latency_ms: float = 0.0
    cost_per_action: float = 0.0
    outcome_rate: float = 0.0
    observed_at: str | None = None

    def __post_init__(self) -> None:
        if not self.workspace_id.strip() or not self.connector_key.strip() or not self.action.strip():
            raise ValueError(OptimizationBlock.INVALID_METRIC.value)
        if self.samples < 0:
            raise ValueError(OptimizationBlock.INVALID_METRIC.value)
        for value in (self.success_rate, self.ambiguous_rate, self.outcome_rate):
            if not isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(OptimizationBlock.INVALID_METRIC.value)
        for value in (self.p95_latency_ms, self.cost_per_action):
            if not isfinite(value) or value < 0:
                raise ValueError(OptimizationBlock.INVALID_METRIC.value)


@dataclass(frozen=True)
class OptimizationWeights:
    reliability: float = 0.40
    outcome: float = 0.30
    latency: float = 0.15
    cost: float = 0.15

    def __post_init__(self) -> None:
        values = (self.reliability, self.outcome, self.latency, self.cost)
        if any(v < 0 or not isfinite(v) for v in values) or sum(values) <= 0:
            raise ValueError(OptimizationBlock.INVALID_METRIC.value)


@dataclass(frozen=True)
class ConnectorRecommendation:
    workspace_id: str
    action: str
    connector_key: str
    score: float
    confidence: float
    reasons: tuple[str, ...]
    advisory_only: bool = True
    authority_granted: bool = False
    execution_effect: str = "none"

    def __post_init__(self) -> None:
        if self.authority_granted or self.execution_effect != "none":
            raise ValueError(OptimizationBlock.FORBIDDEN_AUTHORITY.value)


@dataclass(frozen=True)
class OptimizationReport:
    workspace_id: str
    action: str
    recommendations: tuple[ConnectorRecommendation, ...]
    anomalies: tuple[str, ...] = ()
    evidence_count: int = 0
    advisory_only: bool = True


class IntegrationIntelligence:
    """Deterministic, tenant-bound optimizer over supplied observations."""
    def __init__(self, workspace_id: str, *, weights: OptimizationWeights | None = None):
        if not workspace_id.strip():
            raise ValueError(OptimizationBlock.TENANT_MISMATCH.value)
        self.workspace_id = workspace_id
        self.weights = weights or OptimizationWeights()

    def recommend(self, action: str, metrics: Sequence[IntegrationMetric], *, top_k: int = 3,
                  minimum_samples: int = 5) -> OptimizationReport:
        action = action.strip().lower()
        if not action or top_k < 1 or minimum_samples < 0:
            raise ValueError(OptimizationBlock.INVALID_METRIC.value)
        scoped = [m for m in metrics if m.workspace_id == self.workspace_id and m.action.lower() == action]
        foreign = [m for m in metrics if m.workspace_id != self.workspace_id]
        if foreign and not scoped:
            raise ValueError(OptimizationBlock.TENANT_MISMATCH.value)
        eligible = [m for m in scoped if m.samples >= minimum_samples]
        if not eligible:
            return OptimizationReport(self.workspace_id, action, (), (OptimizationBlock.INSUFFICIENT_EVIDENCE.value,), len(scoped))
        max_latency = max((m.p95_latency_ms for m in eligible), default=1.0) or 1.0
        max_cost = max((m.cost_per_action for m in eligible), default=1.0) or 1.0
        ranked = []
        for m in eligible:
            reliability = max(0.0, m.success_rate - m.ambiguous_rate)
            latency = 1.0 - min(1.0, m.p95_latency_ms / max_latency)
            cost = 1.0 - min(1.0, m.cost_per_action / max_cost)
            score = (self.weights.reliability * reliability + self.weights.outcome * m.outcome_rate +
                     self.weights.latency * latency + self.weights.cost * cost)
            confidence = min(1.0, m.samples / 100.0)
            reasons = []
            if reliability >= 0.99: reasons.append("high_reliability")
            if m.ambiguous_rate > 0.05: reasons.append("ambiguous_execution_risk")
            if m.outcome_rate >= 0.8: reasons.append("strong_business_outcome")
            if m.p95_latency_ms == min(x.p95_latency_ms for x in eligible): reasons.append("lowest_latency")
            if m.cost_per_action == min(x.cost_per_action for x in eligible): reasons.append("lowest_cost")
            ranked.append(ConnectorRecommendation(self.workspace_id, action, m.connector_key, round(score, 6), round(confidence, 4), tuple(reasons)))
        ranked.sort(key=lambda r: (-r.score, r.connector_key))
        anomalies = self._anomalies(eligible)
        return OptimizationReport(self.workspace_id, action, tuple(ranked[:top_k]), tuple(anomalies), len(eligible))

    @staticmethod
    def _anomalies(metrics: Sequence[IntegrationMetric]) -> list[str]:
        out: list[str] = []
        if not metrics:
            return out
        avg_success = mean(m.success_rate for m in metrics)
        avg_latency = mean(m.p95_latency_ms for m in metrics)
        for m in metrics:
            if m.success_rate < max(0.0, avg_success - 0.15):
                out.append(f"reliability_drift:{m.connector_key}")
            if avg_latency and m.p95_latency_ms > avg_latency * 2:
                out.append(f"latency_anomaly:{m.connector_key}")
            if m.ambiguous_rate > 0.10:
                out.append(f"ambiguity_anomaly:{m.connector_key}")
        return sorted(out)

    def compare_process_options(self, metrics: Sequence[IntegrationMetric], *, action: str) -> ConnectorRecommendation | None:
        report = self.recommend(action, metrics, top_k=1)
        return report.recommendations[0] if report.recommendations else None


__all__ = ["OptimizationBlock", "IntegrationMetric", "OptimizationWeights", "ConnectorRecommendation", "OptimizationReport", "IntegrationIntelligence"]
