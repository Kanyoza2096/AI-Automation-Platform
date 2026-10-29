"""Phase 7.17 — Worker Reputation & Capability Evidence Boundary.

Derives bounded reputation/capability evidence only from durable Phase 7.16
learning feedback. Evidence is advisory input for future coordination; it is
not identity, authorization, execution authority, or business truth.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any
import hashlib, json, sqlite3

from services.v71_27_worker_learning_feedback import (
    WorkerLearningFeedbackRegistry, WorkerLearningFeedback,
)

class ReputationBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    FEEDBACK_MISSING = "feedback_missing"
    FEEDBACK_TAMPERED = "feedback_tampered"
    INVALID_CAPABILITY = "invalid_capability"
    INVALID_SCORE = "invalid_score"
    DUPLICATE = "duplicate"
    EVIDENCE_TAMPERED = "evidence_tampered"
    SELF_MUTATION = "self_mutation"
    AUTHORITY_VIOLATION = "authority_violation"

@dataclass(frozen=True)
class WorkerCapabilityEvidence:
    evidence_id: str
    workspace_id: str
    feedback_id: str
    closure_id: str
    worker_id: str
    capability: str
    evidence_type: str
    capability_score: float
    performance_score: float
    verified: bool
    source_fingerprint: str
    coordination_only: bool = True
    worker_mutable: bool = False
    authorization_granted: bool = False
    execution_requested: bool = False
    provider_called: bool = False
    evidence_fingerprint: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id, "workspace_id": self.workspace_id,
            "feedback_id": self.feedback_id, "closure_id": self.closure_id,
            "worker_id": self.worker_id, "capability": self.capability,
            "evidence_type": self.evidence_type,
            "capability_score": self.capability_score,
            "performance_score": self.performance_score, "verified": self.verified,
            "source_fingerprint": self.source_fingerprint,
            "coordination_only": self.coordination_only,
            "worker_mutable": self.worker_mutable,
            "authorization_granted": self.authorization_granted,
            "execution_requested": self.execution_requested,
            "provider_called": self.provider_called,
        }

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()

@dataclass(frozen=True)
class WorkerReputationProfile:
    workspace_id: str
    worker_id: str
    capability: str
    sample_count: int
    average_capability_score: float
    average_performance_score: float
    verified_sample_count: int
    evidence_ids: tuple[str, ...]
    coordination_only: bool = True
    can_authorize: bool = False
    can_execute: bool = False
    can_call_provider: bool = False

class WorkerReputationCapabilityRegistry:
    VERSION = "phase7.17"

    def __init__(self, workspace_id: str, learning: WorkerLearningFeedbackRegistry,
                 db_path: str = ":memory:"):
        ws = str(workspace_id or "").strip()
        if not ws or learning.workspace_id != ws:
            raise ValueError(ReputationBlock.TENANT_MISMATCH.value)
        self.workspace_id = ws
        self.learning = learning
        self._db = sqlite3.connect(db_path)
        self._db.execute("""CREATE TABLE IF NOT EXISTS worker_capability_evidence (
            evidence_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            feedback_id TEXT NOT NULL,
            worker_id TEXT NOT NULL,
            capability TEXT NOT NULL,
            payload TEXT NOT NULL,
            fingerprint TEXT NOT NULL
        )""")
        self._db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_worker_capability_feedback ON worker_capability_evidence(feedback_id, capability)")
        self._db.commit()

    @staticmethod
    def _score(value: float) -> float:
        try: v = float(value)
        except (TypeError, ValueError): raise ValueError(ReputationBlock.INVALID_SCORE.value)
        if v < 0.0 or v > 1.0: raise ValueError(ReputationBlock.INVALID_SCORE.value)
        return round(v, 6)

    @staticmethod
    def _text(value: str, block: ReputationBlock) -> str:
        value = str(value or "").strip()
        if not value: raise ValueError(block.value)
        return value

    def record(self, *, evidence_id: str, feedback_id: str, capability: str,
               capability_score: float, evidence_type: str = "verified_performance") -> WorkerCapabilityEvidence:
        eid = self._text(evidence_id, ReputationBlock.DUPLICATE)
        fid = self._text(feedback_id, ReputationBlock.FEEDBACK_MISSING)
        cap = self._text(capability, ReputationBlock.INVALID_CAPABILITY)
        if self.get(eid) is not None or self._db.execute(
            "SELECT 1 FROM worker_capability_evidence WHERE feedback_id=? AND capability=?", (fid, cap)
        ).fetchone():
            raise ValueError(ReputationBlock.DUPLICATE.value)
        feedback = self.learning.get(fid)
        if feedback is None: raise ValueError(ReputationBlock.FEEDBACK_MISSING.value)
        self.learning.assert_integrity(feedback)
        cs = self._score(capability_score)
        et = self._text(evidence_type, ReputationBlock.INVALID_CAPABILITY)
        rec = WorkerCapabilityEvidence(
            eid, self.workspace_id, feedback.feedback_id, feedback.closure_id,
            feedback.worker_id, cap, et, cs, feedback.performance_score,
            True, feedback.feedback_fingerprint, True, False, False, False, False, ""
        )
        rec = WorkerCapabilityEvidence(**{**rec.__dict__, "evidence_fingerprint": rec.fingerprint()})
        self._db.execute("INSERT INTO worker_capability_evidence VALUES (?,?,?,?,?,?,?)", (
            rec.evidence_id, rec.workspace_id, rec.feedback_id, rec.worker_id,
            rec.capability, json.dumps(rec.canonical_payload(), sort_keys=True), rec.evidence_fingerprint
        ))
        self._db.commit()
        return rec

    def get(self, evidence_id: str) -> WorkerCapabilityEvidence | None:
        row = self._db.execute("SELECT payload, fingerprint FROM worker_capability_evidence WHERE evidence_id=?", (str(evidence_id),)).fetchone()
        if not row: return None
        p = json.loads(row[0]); p["evidence_fingerprint"] = row[1]
        return WorkerCapabilityEvidence(**p)

    def assert_integrity(self, record: WorkerCapabilityEvidence) -> None:
        if record.workspace_id != self.workspace_id: raise ValueError(ReputationBlock.TENANT_MISMATCH.value)
        if (not record.coordination_only or record.worker_mutable or record.authorization_granted or
            record.execution_requested or record.provider_called or not record.verified):
            raise ValueError(ReputationBlock.EVIDENCE_TAMPERED.value)
        self._score(record.capability_score); self._score(record.performance_score)
        if not record.evidence_fingerprint or record.evidence_fingerprint != record.fingerprint():
            raise ValueError(ReputationBlock.EVIDENCE_TAMPERED.value)
        stored = self.get(record.evidence_id)
        if stored is None or stored.evidence_fingerprint != record.evidence_fingerprint:
            raise ValueError(ReputationBlock.EVIDENCE_TAMPERED.value)
        feedback = self.learning.get(record.feedback_id)
        if feedback is None: raise ValueError(ReputationBlock.FEEDBACK_MISSING.value)
        self.learning.assert_integrity(feedback)
        if feedback.feedback_fingerprint != record.source_fingerprint:
            raise ValueError(ReputationBlock.FEEDBACK_TAMPERED.value)
        if feedback.worker_id != record.worker_id or feedback.closure_id != record.closure_id:
            raise ValueError(ReputationBlock.FEEDBACK_TAMPERED.value)

    def profile(self, *, worker_id: str, capability: str) -> WorkerReputationProfile:
        wid = self._text(worker_id, ReputationBlock.FEEDBACK_MISSING)
        cap = self._text(capability, ReputationBlock.INVALID_CAPABILITY)
        ids = [r[0] for r in self._db.execute(
            "SELECT evidence_id FROM worker_capability_evidence WHERE worker_id=? AND capability=? ORDER BY rowid",
            (wid, cap)).fetchall()]
        records = [self.get(i) for i in ids]
        for record in records: self.assert_integrity(record)
        if not records:
            return WorkerReputationProfile(self.workspace_id, wid, cap, 0, 0.0, 0.0, 0, (), True, False, False, False)
        return WorkerReputationProfile(
            self.workspace_id, wid, cap, len(records),
            round(sum(r.capability_score for r in records) / len(records), 6),
            round(sum(r.performance_score for r in records) / len(records), 6),
            sum(1 for r in records if r.verified), tuple(ids), True, False, False, False
        )

    def close(self) -> None:
        self._db.close()

__all__ = ["ReputationBlock", "WorkerCapabilityEvidence", "WorkerReputationProfile", "WorkerReputationCapabilityRegistry"]
