"""Phase 7.34 — Workforce Learning & Coordination Feedback.

Consumes only canonical Phase 7.33 coordinated closures and existing per-worker
learning feedback. Produces durable coordination-level learning signals that
may inform future planning/selection/capacity decisions. It never rewrites
history, self-awards reputation, authorizes, executes, calls providers, or
asserts business truth.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any
import hashlib, json, sqlite3

from services.v71_44_coordinated_verification_outcome_closure import (
    CoordinatedVerificationOutcomeClosure, CoordinatedClosureState,
)
from services.v71_27_worker_learning_feedback import WorkerLearningFeedbackRegistry

class CoordinationFeedbackBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    CLOSURE_MISSING = "closure_missing"
    CLOSURE_TAMPERED = "closure_tampered"
    DUPLICATE = "duplicate"
    INVALID_SCORE = "invalid_score"
    FEEDBACK_TAMPERED = "feedback_tampered"
    AUTHORITY_VIOLATION = "authority_violation"
    HISTORICAL_OUTCOME_MUTATION = "historical_outcome_mutation"

@dataclass(frozen=True)
class WorkforceCoordinationFeedback:
    feedback_id: str
    workspace_id: str
    coordinated_closure_id: str
    plan_id: str
    coordination_lifecycle_id: str
    worker_feedback_ids: tuple[str, ...]
    worker_count: int
    success_count: int
    failure_count: int
    uncertain_count: int
    rejected_count: int
    coordination_performance_score: float
    evidence_quality_score: float
    coordination_reliability_score: float
    learning_signal: str
    source_fingerprint: str
    coordination_only: bool = True
    worker_mutable: bool = False
    authorization_granted: bool = False
    execution_requested: bool = False
    provider_called: bool = False
    historical_outcome_mutated: bool = False
    feedback_fingerprint: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "feedback_id": self.feedback_id, "workspace_id": self.workspace_id,
            "coordinated_closure_id": self.coordinated_closure_id,
            "plan_id": self.plan_id, "coordination_lifecycle_id": self.coordination_lifecycle_id,
            "worker_feedback_ids": list(self.worker_feedback_ids), "worker_count": self.worker_count,
            "success_count": self.success_count, "failure_count": self.failure_count,
            "uncertain_count": self.uncertain_count, "rejected_count": self.rejected_count,
            "coordination_performance_score": self.coordination_performance_score,
            "evidence_quality_score": self.evidence_quality_score,
            "coordination_reliability_score": self.coordination_reliability_score,
            "learning_signal": self.learning_signal, "source_fingerprint": self.source_fingerprint,
            "coordination_only": self.coordination_only, "worker_mutable": self.worker_mutable,
            "authorization_granted": self.authorization_granted,
            "execution_requested": self.execution_requested, "provider_called": self.provider_called,
            "historical_outcome_mutated": self.historical_outcome_mutated,
        }

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()

class WorkforceCoordinationFeedbackRegistry:
    VERSION = "phase7.34"

    def __init__(self, workspace_id: str, coordination_closure: CoordinatedVerificationOutcomeClosure,
                 learning: WorkerLearningFeedbackRegistry, db_path: str = ":memory:"):
        ws = str(workspace_id or "").strip()
        if not ws or coordination_closure.workspace_id != ws or learning.workspace_id != ws:
            raise ValueError(CoordinationFeedbackBlock.TENANT_MISMATCH.value)
        self.workspace_id = ws
        self.coordination_closure = coordination_closure
        self.learning = learning
        self._db = sqlite3.connect(db_path)
        self._db.execute("""CREATE TABLE IF NOT EXISTS workforce_coordination_feedback (
            feedback_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            coordinated_closure_id TEXT NOT NULL,
            payload TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            UNIQUE(workspace_id, coordinated_closure_id)
        )""")
        self._db.commit()

    @staticmethod
    def _score(v: float) -> float:
        try: x = float(v)
        except (TypeError, ValueError): raise ValueError(CoordinationFeedbackBlock.INVALID_SCORE.value)
        if x < 0.0 or x > 1.0: raise ValueError(CoordinationFeedbackBlock.INVALID_SCORE.value)
        return round(x, 6)

    def record(self, *, feedback_id: str, coordinated_closure_id: str,
               learning_signal: str = "verified_coordination_outcome") -> WorkforceCoordinationFeedback:
        fid, cid = str(feedback_id or "").strip(), str(coordinated_closure_id or "").strip()
        if not fid or not cid: raise ValueError(CoordinationFeedbackBlock.CLOSURE_MISSING.value)
        if self.get(fid) is not None or self._db.execute(
            "SELECT 1 FROM workforce_coordination_feedback WHERE coordinated_closure_id=? AND workspace_id=?", (cid, self.workspace_id)
        ).fetchone(): raise ValueError(CoordinationFeedbackBlock.DUPLICATE.value)
        closure = self.coordination_closure.get(cid)
        if closure is None: raise ValueError(CoordinationFeedbackBlock.CLOSURE_MISSING.value)
        self.coordination_closure.assert_integrity(closure)
        if closure.state not in {CoordinatedClosureState.PROPAGATED, CoordinatedClosureState.CLOSED, CoordinatedClosureState.ESCALATED}:
            raise ValueError(CoordinationFeedbackBlock.CLOSURE_MISSING.value)

        worker_ids = []
        for worker_closure_id in closure.worker_closure_ids:
            wc = self.coordination_closure.worker_closure.get(worker_closure_id)
            if wc is None: raise ValueError(CoordinationFeedbackBlock.CLOSURE_MISSING.value)
            self.coordination_closure.worker_closure.assert_integrity(wc)
            existing = self.learning.latest_for_worker(wc.worker_id)
            # Reuse the canonical closure as the source; create exactly one learning record
            # for this worker/closure if it does not already exist.
            feedback = next((x for x in existing if x.closure_id == wc.closure_id), None)
            if feedback is None:
                score = {"verified_success": 1.0, "verified_failure": 0.0,
                         "verified_uncertain": 0.5, "verified_rejected": 0.0}[wc.disposition.value]
                evidence = 1.0 if wc.disposition.value == "verified_success" else 0.5
                reliability = 1.0 if wc.disposition.value == "verified_success" else 0.5
                feedback = self.learning.record(
                    feedback_id=f"{fid}:worker:{wc.worker_id}:{wc.task_id}",
                    closure_id=wc.closure_id,
                    performance_score=score,
                    evidence_quality_score=evidence,
                    coordination_reliability_score=reliability,
                    learning_signal="verified_coordinated_worker_outcome",
                )
            self.learning.assert_integrity(feedback)
            worker_ids.append(feedback.feedback_id)

        counts = {"verified_success": 0, "verified_failure": 0, "verified_uncertain": 0, "verified_rejected": 0}
        for outcome in closure.outcomes: counts[outcome.disposition.value] += 1
        n = len(closure.outcomes)
        if n == 0: raise ValueError(CoordinationFeedbackBlock.CLOSURE_MISSING.value)
        performance = (counts["verified_success"] + 0.5 * counts["verified_uncertain"]) / n
        evidence_quality = (counts["verified_success"] + 0.5 * (counts["verified_failure"] + counts["verified_uncertain"] + counts["verified_rejected"])) / n
        reliability = counts["verified_success"] / n
        rec = WorkforceCoordinationFeedback(
            fid, self.workspace_id, closure.closure_id, closure.plan_id, closure.coordination_lifecycle_id,
            tuple(worker_ids), n, counts["verified_success"], counts["verified_failure"],
            counts["verified_uncertain"], counts["verified_rejected"],
            self._score(performance), self._score(evidence_quality), self._score(reliability),
            str(learning_signal or "verified_coordination_outcome").strip() or "verified_coordination_outcome",
            closure.closure_fingerprint,
        )
        rec = WorkforceCoordinationFeedback(**{**rec.__dict__, "feedback_fingerprint": rec.fingerprint()})
        self._db.execute("INSERT INTO workforce_coordination_feedback VALUES (?,?,?,?,?)", (
            rec.feedback_id, rec.workspace_id, rec.coordinated_closure_id,
            json.dumps(rec.canonical_payload(), sort_keys=True), rec.feedback_fingerprint))
        self._db.commit()
        return rec

    def get(self, feedback_id: str) -> WorkforceCoordinationFeedback | None:
        row = self._db.execute("SELECT payload,fingerprint FROM workforce_coordination_feedback WHERE feedback_id=? AND workspace_id=?", (str(feedback_id), self.workspace_id)).fetchone()
        if not row: return None
        p = json.loads(row[0]); p["worker_feedback_ids"] = tuple(p.get("worker_feedback_ids", ())); p["feedback_fingerprint"] = row[1]
        return WorkforceCoordinationFeedback(**p)

    def assert_integrity(self, record: WorkforceCoordinationFeedback) -> None:
        if record.workspace_id != self.workspace_id: raise ValueError(CoordinationFeedbackBlock.TENANT_MISMATCH.value)
        if (not record.coordination_only or record.worker_mutable or record.authorization_granted or record.execution_requested or
            record.provider_called or record.historical_outcome_mutated): raise ValueError(CoordinationFeedbackBlock.AUTHORITY_VIOLATION.value)
        for s in (record.coordination_performance_score, record.evidence_quality_score, record.coordination_reliability_score): self._score(s)
        if not record.feedback_fingerprint or record.feedback_fingerprint != record.fingerprint(): raise ValueError(CoordinationFeedbackBlock.FEEDBACK_TAMPERED.value)
        stored = self.get(record.feedback_id)
        if stored is None or stored.feedback_fingerprint != record.feedback_fingerprint: raise ValueError(CoordinationFeedbackBlock.FEEDBACK_TAMPERED.value)
        closure = self.coordination_closure.get(record.coordinated_closure_id)
        if closure is None: raise ValueError(CoordinationFeedbackBlock.CLOSURE_MISSING.value)
        self.coordination_closure.assert_integrity(closure)
        if closure.closure_fingerprint != record.source_fingerprint: raise ValueError(CoordinationFeedbackBlock.HISTORICAL_OUTCOME_MUTATION.value)
        for fid in record.worker_feedback_ids:
            f = self.learning.get(fid)
            if f is None: raise ValueError(CoordinationFeedbackBlock.FEEDBACK_TAMPERED.value)
            self.learning.assert_integrity(f)

    def context(self, feedback_id: str) -> dict[str, Any]:
        rec = self.get(feedback_id)
        if rec is None: raise ValueError(CoordinationFeedbackBlock.CLOSURE_MISSING.value)
        self.assert_integrity(rec)
        return {"feedback_id": rec.feedback_id, "plan_id": rec.plan_id, "worker_count": rec.worker_count,
                "coordination_performance_score": rec.coordination_performance_score,
                "coordination_reliability_score": rec.coordination_reliability_score,
                "coordination_only": True, "authorization_granted": False,
                "execution_requested": False, "provider_called": False}

    def close(self) -> None: self._db.close()

__all__ = ["CoordinationFeedbackBlock", "WorkforceCoordinationFeedback", "WorkforceCoordinationFeedbackRegistry"]
