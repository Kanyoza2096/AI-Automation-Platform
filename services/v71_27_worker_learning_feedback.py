"""Phase 7.16 — Worker Learning & Performance Feedback Boundary.

Consumes authoritative workforce closure records and stores bounded feedback
used for future coordination/selection. It never rewrites history, grants
authority, executes work, calls providers, or declares business truth.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any
import hashlib, json, sqlite3

from services.v71_15_worker_outcome_feedback_closure import (
    WorkerOutcomeFeedbackClosureRegistry, WorkerOutcomeFeedbackClosure,
    ClosureState, VerifiedDisposition,
)

class FeedbackBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    CLOSURE_MISSING = "closure_missing"
    CLOSURE_TAMPERED = "closure_tampered"
    INVALID_SCORE = "invalid_score"
    DUPLICATE = "duplicate"
    FEEDBACK_TAMPERED = "feedback_tampered"
    HISTORICAL_OUTCOME_MUTATION = "historical_outcome_mutation"

@dataclass(frozen=True)
class WorkerLearningFeedback:
    feedback_id: str
    workspace_id: str
    closure_id: str
    assignment_id: str
    task_id: str
    worker_id: str
    disposition: VerifiedDisposition
    closure_state: ClosureState
    performance_score: float
    evidence_quality_score: float
    coordination_reliability_score: float
    learning_signal: str
    closure_fingerprint: str
    coordination_only: bool = True
    authorization_granted: bool = False
    execution_requested: bool = False
    provider_called: bool = False
    historical_outcome_mutated: bool = False
    feedback_fingerprint: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "feedback_id": self.feedback_id, "workspace_id": self.workspace_id,
            "closure_id": self.closure_id, "assignment_id": self.assignment_id,
            "task_id": self.task_id, "worker_id": self.worker_id,
            "disposition": self.disposition.value, "closure_state": self.closure_state.value,
            "performance_score": self.performance_score,
            "evidence_quality_score": self.evidence_quality_score,
            "coordination_reliability_score": self.coordination_reliability_score,
            "learning_signal": self.learning_signal,
            "closure_fingerprint": self.closure_fingerprint,
            "coordination_only": self.coordination_only,
            "authorization_granted": self.authorization_granted,
            "execution_requested": self.execution_requested,
            "provider_called": self.provider_called,
            "historical_outcome_mutated": self.historical_outcome_mutated,
        }

    def fingerprint(self) -> str:
        raw = json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()

class WorkerLearningFeedbackRegistry:
    VERSION = "phase7.16"

    def __init__(self, workspace_id: str, closure: WorkerOutcomeFeedbackClosureRegistry,
                 db_path: str = ":memory:"):
        ws = str(workspace_id or "").strip()
        if not ws or closure.workspace_id != ws:
            raise ValueError(FeedbackBlock.TENANT_MISMATCH.value)
        self.workspace_id = ws
        self.closure = closure
        self._db = sqlite3.connect(db_path)
        self._db.execute("""CREATE TABLE IF NOT EXISTS worker_learning_feedback (
            feedback_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            closure_id TEXT NOT NULL,
            worker_id TEXT NOT NULL,
            payload TEXT NOT NULL,
            fingerprint TEXT NOT NULL
        )""")
        self._db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_worker_learning_closure ON worker_learning_feedback(closure_id)")
        self._db.commit()

    @staticmethod
    def _score(value: float) -> float:
        try: v = float(value)
        except (TypeError, ValueError): raise ValueError(FeedbackBlock.INVALID_SCORE.value)
        if v < 0.0 or v > 1.0: raise ValueError(FeedbackBlock.INVALID_SCORE.value)
        return round(v, 6)

    def record(self, *, feedback_id: str, closure_id: str,
               performance_score: float, evidence_quality_score: float,
               coordination_reliability_score: float,
               learning_signal: str = "verified_outcome_feedback") -> WorkerLearningFeedback:
        fid, cid = str(feedback_id or "").strip(), str(closure_id or "").strip()
        if not fid or not cid: raise ValueError(FeedbackBlock.CLOSURE_MISSING.value)
        if self.get(fid) is not None: raise ValueError(FeedbackBlock.DUPLICATE.value)
        if self._db.execute("SELECT 1 FROM worker_learning_feedback WHERE closure_id=?", (cid,)).fetchone():
            raise ValueError(FeedbackBlock.DUPLICATE.value)
        c = self.closure.get(cid)
        if c is None: raise ValueError(FeedbackBlock.CLOSURE_MISSING.value)
        self.closure.assert_integrity(c)
        ps, es, rs = map(self._score, (performance_score, evidence_quality_score, coordination_reliability_score))
        signal = str(learning_signal or "verified_outcome_feedback").strip() or "verified_outcome_feedback"
        rec = WorkerLearningFeedback(
            fid, self.workspace_id, c.closure_id, c.assignment_id, c.task_id, c.worker_id,
            c.disposition, c.state, ps, es, rs, signal, c.closure_fingerprint,
            True, False, False, False, False, "")
        rec = WorkerLearningFeedback(**{**rec.__dict__, "feedback_fingerprint": rec.fingerprint()})
        self._db.execute("INSERT INTO worker_learning_feedback VALUES (?,?,?,?,?,?)", (
            rec.feedback_id, rec.workspace_id, rec.closure_id, rec.worker_id,
            json.dumps(rec.canonical_payload(), sort_keys=True), rec.feedback_fingerprint))
        self._db.commit()
        return rec

    def get(self, feedback_id: str) -> WorkerLearningFeedback | None:
        row = self._db.execute("SELECT payload, fingerprint FROM worker_learning_feedback WHERE feedback_id=?", (str(feedback_id),)).fetchone()
        if not row: return None
        p = json.loads(row[0]); p["disposition"] = VerifiedDisposition(p["disposition"]); p["closure_state"] = ClosureState(p["closure_state"]); p["feedback_fingerprint"] = row[1]
        return WorkerLearningFeedback(**p)

    def latest_for_worker(self, worker_id: str) -> list[WorkerLearningFeedback]:
        rows = self._db.execute("SELECT feedback_id FROM worker_learning_feedback WHERE worker_id=? ORDER BY rowid", (str(worker_id),)).fetchall()
        return [self.get(r[0]) for r in rows]

    def assert_integrity(self, record: WorkerLearningFeedback) -> None:
        if record.workspace_id != self.workspace_id: raise ValueError(FeedbackBlock.TENANT_MISMATCH.value)
        if (not record.coordination_only or record.authorization_granted or record.execution_requested or
            record.provider_called or record.historical_outcome_mutated):
            raise ValueError(FeedbackBlock.FEEDBACK_TAMPERED.value)
        for s in (record.performance_score, record.evidence_quality_score, record.coordination_reliability_score): self._score(s)
        if not record.feedback_fingerprint or record.feedback_fingerprint != record.fingerprint(): raise ValueError(FeedbackBlock.FEEDBACK_TAMPERED.value)
        stored = self.get(record.feedback_id)
        if stored is None or stored.feedback_fingerprint != record.feedback_fingerprint: raise ValueError(FeedbackBlock.FEEDBACK_TAMPERED.value)
        c = self.closure.get(record.closure_id)
        if c is None: raise ValueError(FeedbackBlock.CLOSURE_MISSING.value)
        self.closure.assert_integrity(c)
        if c.closure_fingerprint != record.closure_fingerprint: raise ValueError(FeedbackBlock.HISTORICAL_OUTCOME_MUTATION.value)

    def worker_signal(self, worker_id: str) -> dict[str, Any]:
        rows = self.latest_for_worker(worker_id)
        for r in rows: self.assert_integrity(r)
        if not rows: return {"worker_id": worker_id, "sample_count": 0, "average_performance": None, "coordination_only": True}
        avg = round(sum(r.performance_score for r in rows) / len(rows), 6)
        return {"worker_id": worker_id, "sample_count": len(rows), "average_performance": avg, "coordination_only": True,
                "can_authorize": False, "can_execute": False, "can_call_provider": False}

    def close(self) -> None: self._db.close()

__all__ = ["FeedbackBlock", "WorkerLearningFeedback", "WorkerLearningFeedbackRegistry"]
