"""Phase 7.35 — Workforce Coordination Policy & Adaptive Planning.

Learning is an explicit, versioned planning input with a hard influence ceiling.
It may prioritize/recommend coordination choices, but it cannot bypass canonical
qualification, selection, capacity, safety, budget, governance, authorization,
or execution boundaries. Historical outcomes are immutable.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any
import hashlib, json, sqlite3

from services.v71_37_workforce_multi_worker_planning import WorkforceMultiWorkerPlanner, MultiWorkerPlan
from services.v71_45_workforce_coordination_feedback import WorkforceCoordinationFeedbackRegistry, WorkforceCoordinationFeedback

class AdaptivePlanningBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    INVALID_POLICY = "invalid_policy"
    FEEDBACK_MISSING = "feedback_missing"
    FEEDBACK_TAMPERED = "feedback_tampered"
    PLAN_TAMPERED = "plan_tampered"
    DUPLICATE = "duplicate"
    INFLUENCE_EXCEEDED = "influence_exceeded"
    AUTHORITY_VIOLATION = "authority_violation"
    HISTORICAL_MUTATION = "historical_mutation"

@dataclass(frozen=True)
class WorkforceCoordinationPolicy:
    version: str = "1"
    learning_influence_ceiling: float = 0.10
    min_reliability_for_positive_bias: float = 0.70
    uncertainty_penalty: float = 0.10
    failure_penalty: float = 0.20
    max_priority_adjustment: float = 0.10
    require_verified_feedback: bool = True

    def validate(self) -> None:
        vals = (self.learning_influence_ceiling, self.min_reliability_for_positive_bias,
                self.uncertainty_penalty, self.failure_penalty, self.max_priority_adjustment)
        if any(v < 0.0 or v > 1.0 for v in vals):
            raise ValueError(AdaptivePlanningBlock.INVALID_POLICY.value)
        if self.learning_influence_ceiling > 0.10 or self.max_priority_adjustment > self.learning_influence_ceiling:
            raise ValueError(AdaptivePlanningBlock.INFLUENCE_EXCEEDED.value)
        if not str(self.version).strip():
            raise ValueError(AdaptivePlanningBlock.INVALID_POLICY.value)

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.__dict__, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

@dataclass(frozen=True)
class AdaptiveTaskRecommendation:
    task_id: str
    worker_id: str
    baseline_priority: float
    adaptive_priority: float
    bounded_adjustment: float
    reliability: float
    signal: str

    def payload(self) -> dict[str, Any]:
        return {"task_id": self.task_id, "worker_id": self.worker_id,
                "baseline_priority": round(self.baseline_priority, 6),
                "adaptive_priority": round(self.adaptive_priority, 6),
                "bounded_adjustment": round(self.bounded_adjustment, 6),
                "reliability": round(self.reliability, 6), "signal": self.signal}

@dataclass(frozen=True)
class AdaptivePlanningAssessment:
    assessment_id: str
    workspace_id: str
    base_plan_id: str
    policy_version: str
    policy_fingerprint: str
    feedback_ids: tuple[str, ...]
    recommendations: tuple[AdaptiveTaskRecommendation, ...]
    learning_influence: float
    historical_source_fingerprints: tuple[str, ...]
    advisory_only: bool = True
    authority_granted: bool = False
    execution_requested: bool = False
    provider_called: bool = False
    action_kernel_invoked: bool = False
    plan_mutated: bool = False
    assessment_fingerprint: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return {"assessment_id": self.assessment_id, "workspace_id": self.workspace_id,
                "base_plan_id": self.base_plan_id, "policy_version": self.policy_version,
                "policy_fingerprint": self.policy_fingerprint, "feedback_ids": list(self.feedback_ids),
                "recommendations": [r.payload() for r in self.recommendations],
                "learning_influence": round(self.learning_influence, 6),
                "historical_source_fingerprints": list(self.historical_source_fingerprints),
                "advisory_only": self.advisory_only, "authority_granted": self.authority_granted,
                "execution_requested": self.execution_requested, "provider_called": self.provider_called,
                "action_kernel_invoked": self.action_kernel_invoked, "plan_mutated": self.plan_mutated}

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()

class WorkforceAdaptivePlanner:
    VERSION = "phase7.35"

    def __init__(self, workspace_id: str, planner: WorkforceMultiWorkerPlanner,
                 feedback: WorkforceCoordinationFeedbackRegistry, db_path: str = ":memory:"):
        ws = str(workspace_id or "").strip()
        if not ws or planner.workspace_id != ws or feedback.workspace_id != ws:
            raise ValueError(AdaptivePlanningBlock.TENANT_MISMATCH.value)
        self.workspace_id, self.planner, self.feedback = ws, planner, feedback
        self._db = sqlite3.connect(db_path)
        self._db.execute("""CREATE TABLE IF NOT EXISTS workforce_adaptive_planning (
            assessment_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
            payload TEXT NOT NULL, fingerprint TEXT NOT NULL,
            UNIQUE(workspace_id, assessment_id))""")
        self._db.commit()

    def assess(self, *, assessment_id: str, plan: MultiWorkerPlan,
               policy: WorkforceCoordinationPolicy,
               feedback_ids: tuple[str, ...]) -> AdaptivePlanningAssessment:
        policy.validate()
        self.planner.assert_integrity(plan)
        if plan.workspace_id != self.workspace_id:
            raise ValueError(AdaptivePlanningBlock.TENANT_MISMATCH.value)
        if self.get(assessment_id) is not None:
            raise ValueError(AdaptivePlanningBlock.DUPLICATE.value)

        ids = tuple(dict.fromkeys(str(x).strip() for x in feedback_ids if str(x).strip()))
        if policy.require_verified_feedback and not ids:
            raise ValueError(AdaptivePlanningBlock.FEEDBACK_MISSING.value)
        records: list[WorkforceCoordinationFeedback] = []
        for fid in ids:
            rec = self.feedback.get(fid)
            if rec is None:
                raise ValueError(AdaptivePlanningBlock.FEEDBACK_MISSING.value)
            self.feedback.assert_integrity(rec)
            if rec.plan_id != plan.plan_id:
                # Historical learning from another plan is valid evidence, but only as
                # a bounded aggregate signal; it cannot rewrite this plan.
                pass
            records.append(rec)

        if not records and not policy.require_verified_feedback:
            aggregate = 0.0
        else:
            aggregate = sum(r.coordination_reliability_score for r in records) / len(records)
        influence = min(policy.learning_influence_ceiling, policy.max_priority_adjustment)
        recs = []
        for idx, task in enumerate(sorted(plan.tasks, key=lambda t: t.task_id)):
            baseline = 1.0 / (idx + 1)
            if aggregate >= policy.min_reliability_for_positive_bias:
                delta = influence * aggregate
                signal = "bounded_positive_learning_bias"
            else:
                delta = -min(influence, policy.failure_penalty + policy.uncertainty_penalty) * (1.0 - aggregate)
                signal = "bounded_caution_bias"
            delta = max(-policy.max_priority_adjustment, min(policy.max_priority_adjustment, delta))
            recs.append(AdaptiveTaskRecommendation(task.task_id, task.worker_id, baseline, max(0.0, min(1.0, baseline + delta)), delta, aggregate, signal))

        result = AdaptivePlanningAssessment(
            str(assessment_id), self.workspace_id, plan.plan_id, policy.version, policy.fingerprint(), ids,
            tuple(recs), round(abs(max((r.bounded_adjustment for r in recs), default=0.0)), 6),
            tuple(r.source_fingerprint for r in records),
        )
        result = AdaptivePlanningAssessment(**{**result.__dict__, "assessment_fingerprint": result.fingerprint()})
        self._db.execute("INSERT INTO workforce_adaptive_planning VALUES (?,?,?,?)",
                          (result.assessment_id, result.workspace_id, json.dumps(result.canonical_payload(), sort_keys=True), result.assessment_fingerprint))
        self._db.commit()
        return result

    def get(self, assessment_id: str) -> AdaptivePlanningAssessment | None:
        row = self._db.execute("SELECT payload,fingerprint FROM workforce_adaptive_planning WHERE assessment_id=? AND workspace_id=?", (str(assessment_id), self.workspace_id)).fetchone()
        if not row: return None
        p = json.loads(row[0]); p["feedback_ids"] = tuple(p["feedback_ids"]); p["historical_source_fingerprints"] = tuple(p["historical_source_fingerprints"])
        p["recommendations"] = tuple(AdaptiveTaskRecommendation(**x) for x in p["recommendations"]); p["assessment_fingerprint"] = row[1]
        return AdaptivePlanningAssessment(**p)

    def assert_integrity(self, assessment: AdaptivePlanningAssessment) -> None:
        if assessment.workspace_id != self.workspace_id:
            raise ValueError(AdaptivePlanningBlock.TENANT_MISMATCH.value)
        if assessment.assessment_fingerprint != assessment.fingerprint():
            raise ValueError(AdaptivePlanningBlock.FEEDBACK_TAMPERED.value)
        stored = self.get(assessment.assessment_id)
        if stored is None or stored.assessment_fingerprint != assessment.assessment_fingerprint:
            raise ValueError(AdaptivePlanningBlock.FEEDBACK_TAMPERED.value)
        if (not assessment.advisory_only or assessment.authority_granted or assessment.execution_requested or
            assessment.provider_called or assessment.action_kernel_invoked or assessment.plan_mutated):
            raise ValueError(AdaptivePlanningBlock.AUTHORITY_VIOLATION.value)
        plan = self.planner.get(assessment.base_plan_id)
        if plan is None:
            raise ValueError(AdaptivePlanningBlock.PLAN_TAMPERED.value)
        self.planner.assert_integrity(plan)
        for fid, source_fp in zip(assessment.feedback_ids, assessment.historical_source_fingerprints):
            rec = self.feedback.get(fid)
            if rec is None:
                raise ValueError(AdaptivePlanningBlock.FEEDBACK_MISSING.value)
            self.feedback.assert_integrity(rec)
            if rec.source_fingerprint != source_fp:
                raise ValueError(AdaptivePlanningBlock.HISTORICAL_MUTATION.value)

    def context(self, assessment_id: str) -> dict[str, Any]:
        rec = self.get(assessment_id)
        if rec is None: raise ValueError(AdaptivePlanningBlock.FEEDBACK_MISSING.value)
        self.assert_integrity(rec)
        return {"assessment_id": rec.assessment_id, "base_plan_id": rec.base_plan_id,
                "policy_version": rec.policy_version, "recommendation_count": len(rec.recommendations),
                "learning_influence": rec.learning_influence, "advisory_only": True,
                "authority": "none", "execution_requested": False, "plan_mutated": False}

    def close(self) -> None:
        self._db.close()

__all__ = ["AdaptivePlanningBlock", "WorkforceCoordinationPolicy", "AdaptiveTaskRecommendation", "AdaptivePlanningAssessment", "WorkforceAdaptivePlanner"]
