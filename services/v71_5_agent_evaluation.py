"""V71.5 — Governed AI Workforce: Agent Evaluation Plane.

Evaluation measures agent/plan performance from observed execution and outcome evidence.
It never grants authority, permissions, autonomy, approval, or execution rights.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional
import hashlib, json

from services.v71_1_agent_identity import AgentIdentityRegistry, AgentStatus
from services.v71_4_agent_planning import AgentPlanningRegistry, AgentPlan


class EvaluationStatus(str, Enum):
    EVALUATED = "evaluated"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    INVALID = "invalid"


class EvaluationBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    AGENT_NOT_FOUND = "agent_not_found"
    AGENT_NOT_ACTIVE = "agent_not_active"
    PLAN_NOT_FOUND = "plan_not_found"
    DUPLICATE_EVALUATION = "duplicate_evaluation"
    EVALUATION_NOT_FOUND = "evaluation_not_found"
    EVALUATION_TAMPERED = "evaluation_tampered"
    INVALID_EVIDENCE = "invalid_evidence"


@dataclass(frozen=True)
class EvaluationMetrics:
    execution_success: Optional[float] = None
    outcome_alignment: Optional[float] = None
    policy_compliance: Optional[float] = None
    reliability: Optional[float] = None
    recovery_efficiency: Optional[float] = None

    def values(self) -> tuple[float, ...]:
        return tuple(v for v in (
            self.execution_success, self.outcome_alignment,
            self.policy_compliance, self.reliability, self.recovery_efficiency
        ) if v is not None)

    def composite(self) -> Optional[float]:
        # Missing dimensions are not silently scored as zero; composite uses
        # only dimensions backed by evidence and is therefore explicitly partial.
        weights = {
            "execution_success": 0.25,
            "outcome_alignment": 0.30,
            "policy_compliance": 0.20,
            "reliability": 0.15,
            "recovery_efficiency": 0.10,
        }
        present = [(getattr(self, k), w) for k, w in weights.items() if getattr(self, k) is not None]
        if not present:
            return None
        denom = sum(w for _, w in present)
        return round(sum(float(v) * w for v, w in present) / denom, 3)

    def as_dict(self) -> dict[str, Optional[float]]:
        return {
            "execution_success": self.execution_success,
            "outcome_alignment": self.outcome_alignment,
            "policy_compliance": self.policy_compliance,
            "reliability": self.reliability,
            "recovery_efficiency": self.recovery_efficiency,
            "composite": self.composite(),
        }


@dataclass(frozen=True)
class AgentEvaluation:
    evaluation_id: str
    workspace_id: str
    agent_id: str
    plan_id: str
    status: EvaluationStatus
    metrics: EvaluationMetrics
    evidence: Mapping[str, Any] = field(default_factory=dict)
    findings: tuple[str, ...] = ()
    version: str = "1"
    evaluation_fingerprint: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "evaluation_id": self.evaluation_id,
            "workspace_id": self.workspace_id,
            "agent_id": self.agent_id,
            "plan_id": self.plan_id,
            "status": self.status.value,
            "metrics": self.metrics.as_dict(),
            "evidence": dict(self.evidence),
            "findings": list(self.findings),
            "version": self.version,
        }

    def fingerprint(self) -> str:
        raw = json.dumps(self.canonical_payload(), sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()


class AgentEvaluationRegistry:
    VERSION = "v71.5"

    def __init__(self, workspace_id: str, identity_registry: AgentIdentityRegistry,
                 planning_registry: AgentPlanningRegistry):
        ws = str(workspace_id or "").strip()
        if not ws or identity_registry.workspace_id != ws or planning_registry.workspace_id != ws:
            raise ValueError(EvaluationBlock.TENANT_MISMATCH.value)
        self.workspace_id = ws
        self.identity_registry = identity_registry
        self.planning_registry = planning_registry
        self._evaluations: dict[str, AgentEvaluation] = {}

    def _validate_agent(self, agent_id: str):
        agent = self.identity_registry.get(agent_id)
        if agent is None:
            raise ValueError(EvaluationBlock.AGENT_NOT_FOUND.value)
        self.identity_registry.assert_integrity(agent)
        if agent.status != AgentStatus.ACTIVE:
            raise ValueError(EvaluationBlock.AGENT_NOT_ACTIVE.value)
        return agent

    @staticmethod
    def _bounded(value: Any, field_name: str) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError):
            raise ValueError(EvaluationBlock.INVALID_EVIDENCE.value)
        if not 0.0 <= result <= 1.0:
            raise ValueError(EvaluationBlock.INVALID_EVIDENCE.value)
        return round(result, 3)

    def evaluate(self, *, evaluation_id: str, plan_id: str,
                 execution_evidence: Optional[Mapping[str, Any]] = None,
                 outcome_evidence: Optional[Mapping[str, Any]] = None,
                 policy_evidence: Optional[Mapping[str, Any]] = None,
                 reliability_evidence: Optional[Mapping[str, Any]] = None,
                 recovery_evidence: Optional[Mapping[str, Any]] = None,
                 version: str = "1") -> AgentEvaluation:
        eid = str(evaluation_id or "").strip()
        if not eid:
            raise ValueError(EvaluationBlock.INVALID_EVIDENCE.value)
        if eid in self._evaluations:
            raise ValueError(EvaluationBlock.DUPLICATE_EVALUATION.value)
        plan = self.planning_registry.get(plan_id)
        if plan is None:
            raise ValueError(EvaluationBlock.PLAN_NOT_FOUND.value)
        if plan.workspace_id != self.workspace_id:
            raise ValueError(EvaluationBlock.TENANT_MISMATCH.value)
        if plan.plan_fingerprint != plan.fingerprint():
            raise ValueError(EvaluationBlock.EVALUATION_TAMPERED.value)
        self._validate_agent(plan.agent_id)
        if not str(version or "").strip():
            raise ValueError(EvaluationBlock.INVALID_EVIDENCE.value)

        execution_evidence = dict(execution_evidence or {})
        outcome_evidence = dict(outcome_evidence or {})
        policy_evidence = dict(policy_evidence or {})
        reliability_evidence = dict(reliability_evidence or {})
        recovery_evidence = dict(recovery_evidence or {})
        all_evidence = {
            "execution": execution_evidence,
            "outcome": outcome_evidence,
            "policy": policy_evidence,
            "reliability": reliability_evidence,
            "recovery": recovery_evidence,
        }

        # Evidence may be supplied as direct normalized scores, or derived from
        # explicit observed counts. No model-generated assertion is treated as proof.
        metrics = EvaluationMetrics(
            execution_success=self._metric(execution_evidence, "execution_success", "successful_actions", "attempted_actions"),
            outcome_alignment=self._metric(outcome_evidence, "outcome_alignment", "aligned_outcomes", "measured_outcomes"),
            policy_compliance=self._metric(policy_evidence, "policy_compliance", "compliant_actions", "reviewed_actions"),
            reliability=self._metric(reliability_evidence, "reliability", "successful_runs", "total_runs"),
            recovery_efficiency=self._metric(recovery_evidence, "recovery_efficiency", "recovered_failures", "recoverable_failures"),
        )
        findings: list[str] = []
        if metrics.execution_success is not None and metrics.execution_success < 0.8:
            findings.append("execution_reliability_below_target")
        if metrics.outcome_alignment is not None and metrics.outcome_alignment < 0.7:
            findings.append("outcome_alignment_below_target")
        if metrics.policy_compliance is not None and metrics.policy_compliance < 1.0:
            findings.append("policy_compliance_gap_observed")
        if metrics.reliability is not None and metrics.reliability < 0.9:
            findings.append("runtime_reliability_below_target")
        if metrics.recovery_efficiency is not None and metrics.recovery_efficiency < 0.7:
            findings.append("recovery_efficiency_below_target")

        status = EvaluationStatus.EVALUATED if metrics.values() else EvaluationStatus.INSUFFICIENT_EVIDENCE
        if status == EvaluationStatus.INSUFFICIENT_EVIDENCE:
            findings.append("insufficient_observed_evidence")
        evidence = {k: dict(v) for k, v in all_evidence.items() if v}
        evaluation = AgentEvaluation(eid, self.workspace_id, plan.agent_id, plan.plan_id,
                                     status, metrics, evidence, tuple(findings), str(version), "")
        evaluation = AgentEvaluation(**{**evaluation.__dict__, "evaluation_fingerprint": evaluation.fingerprint()})
        self._evaluations[eid] = evaluation
        return evaluation

    @staticmethod
    def _metric(evidence: Mapping[str, Any], direct: str, numerator: str, denominator: str) -> Optional[float]:
        if direct in evidence:
            return AgentEvaluationRegistry._bounded(evidence[direct], direct)
        if numerator in evidence or denominator in evidence:
            if numerator not in evidence or denominator not in evidence:
                raise ValueError(EvaluationBlock.INVALID_EVIDENCE.value)
            try:
                n, d = float(evidence[numerator]), float(evidence[denominator])
            except (TypeError, ValueError):
                raise ValueError(EvaluationBlock.INVALID_EVIDENCE.value)
            if d <= 0 or n < 0 or n > d:
                raise ValueError(EvaluationBlock.INVALID_EVIDENCE.value)
            return round(n / d, 3)
        return None

    def get(self, evaluation_id: str) -> Optional[AgentEvaluation]:
        return self._evaluations.get(str(evaluation_id))

    def assert_integrity(self, evaluation: AgentEvaluation) -> None:
        if evaluation.workspace_id != self.workspace_id:
            raise ValueError(EvaluationBlock.TENANT_MISMATCH.value)
        if evaluation.evaluation_fingerprint != evaluation.fingerprint():
            raise ValueError(EvaluationBlock.EVALUATION_TAMPERED.value)

    def list_evaluations(self, agent_id: Optional[str] = None, plan_id: Optional[str] = None) -> tuple[AgentEvaluation, ...]:
        values = tuple(self._evaluations.values())
        if agent_id is not None:
            values = tuple(v for v in values if v.agent_id == str(agent_id))
        if plan_id is not None:
            values = tuple(v for v in values if v.plan_id == str(plan_id))
        return values

    def performance_summary(self, agent_id: str) -> Mapping[str, Any]:
        self._validate_agent(agent_id)
        rows = [e for e in self._evaluations.values() if e.agent_id == str(agent_id)]
        scored = [e.metrics.composite() for e in rows if e.metrics.composite() is not None]
        return {
            "agent_id": str(agent_id),
            "evaluation_count": len(rows),
            "evaluated_count": sum(e.status == EvaluationStatus.EVALUATED for e in rows),
            "insufficient_evidence_count": sum(e.status == EvaluationStatus.INSUFFICIENT_EVIDENCE for e in rows),
            "average_composite": round(sum(scored) / len(scored), 3) if scored else None,
            "advisory": True,
            "authority_granted": False,
        }


__all__ = ["EvaluationStatus", "EvaluationBlock", "EvaluationMetrics", "AgentEvaluation", "AgentEvaluationRegistry"]
