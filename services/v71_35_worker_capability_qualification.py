"""Phase 7.24 — Worker Capability Qualification & Certification Boundary.

A declared capability is not equivalent to a currently qualified capability.
Qualification is derived only from verified, tamper-checked Phase 7.17 evidence.
It is advisory selection evidence; it never grants authorization or execution.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional
import hashlib, json, sqlite3

from services.v71_28_worker_reputation_capability_evidence import WorkerReputationCapabilityRegistry

class QualificationBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    WORKER_MISSING = "worker_missing"
    CAPABILITY_MISSING = "capability_missing"
    INSUFFICIENT_EVIDENCE = "insufficient_verified_evidence"
    SCORE_TOO_LOW = "qualification_score_too_low"
    QUALIFICATION_MISSING = "qualification_missing"
    QUALIFICATION_TAMPERED = "qualification_tampered"
    DUPLICATE = "duplicate_qualification"
    INVALID_STATUS = "invalid_qualification_status"
    EXPIRED = "qualification_expired"
    SELF_CERTIFICATION = "worker_cannot_self_certify"
    AUTHORITY_VIOLATION = "authority_violation"

class QualificationStatus(str, Enum):
    QUALIFIED = "qualified"
    EXPIRED = "expired"
    SUSPENDED = "suspended"

@dataclass(frozen=True)
class WorkerCapabilityQualification:
    qualification_id: str
    workspace_id: str
    worker_id: str
    capability: str
    status: QualificationStatus
    evidence_ids: tuple[str, ...]
    verified_sample_count: int
    qualification_score: float
    minimum_score: float
    minimum_samples: int
    qualified_by: str
    issued_at: str
    expires_at: Optional[str] = None
    qualification_fingerprint: str = ""
    advisory_only: bool = True
    authority_granted: bool = False
    execution_requested: bool = False
    provider_called: bool = False

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "qualification_id": self.qualification_id,
            "workspace_id": self.workspace_id,
            "worker_id": self.worker_id,
            "capability": self.capability,
            "status": self.status.value,
            "evidence_ids": list(self.evidence_ids),
            "verified_sample_count": self.verified_sample_count,
            "qualification_score": self.qualification_score,
            "minimum_score": self.minimum_score,
            "minimum_samples": self.minimum_samples,
            "qualified_by": self.qualified_by,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "advisory_only": self.advisory_only,
            "authority_granted": self.authority_granted,
            "execution_requested": self.execution_requested,
            "provider_called": self.provider_called,
        }

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()

class WorkerCapabilityQualificationRegistry:
    VERSION = "phase7.24"

    def __init__(self, workspace_id: str, reputation: WorkerReputationCapabilityRegistry, db_path: str = ":memory:"):
        ws = str(workspace_id or "").strip()
        if not ws or reputation.workspace_id != ws:
            raise ValueError(QualificationBlock.TENANT_MISMATCH.value)
        self.workspace_id = ws
        self.reputation = reputation
        self._db = sqlite3.connect(db_path)
        self._db.execute("""CREATE TABLE IF NOT EXISTS worker_capability_qualifications (
            qualification_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            worker_id TEXT NOT NULL,
            capability TEXT NOT NULL,
            payload TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            UNIQUE(workspace_id, worker_id, capability)
        )""")
        self._db.commit()

    @staticmethod
    def _score(value: float) -> float:
        try: value = float(value)
        except (TypeError, ValueError): raise ValueError(QualificationBlock.SCORE_TOO_LOW.value)
        if not 0.0 <= value <= 1.0: raise ValueError(QualificationBlock.SCORE_TOO_LOW.value)
        return round(value, 6)

    def certify(self, *, qualification_id: str, worker_id: str, capability: str,
                qualified_by: str, issued_at: str, minimum_samples: int = 3,
                minimum_score: float = 0.70, expires_at: Optional[str] = None) -> WorkerCapabilityQualification:
        qid, wid, cap, actor = [str(x or "").strip() for x in (qualification_id, worker_id, capability, qualified_by)]
        if not qid or not wid: raise ValueError(QualificationBlock.WORKER_MISSING.value)
        if not cap: raise ValueError(QualificationBlock.CAPABILITY_MISSING.value)
        if not actor: raise ValueError(QualificationBlock.SELF_CERTIFICATION.value)
        if int(minimum_samples) < 1: raise ValueError(QualificationBlock.INSUFFICIENT_EVIDENCE.value)
        minimum_score = self._score(minimum_score)
        if self.get(qid) is not None or self._db.execute("SELECT 1 FROM worker_capability_qualifications WHERE workspace_id=? AND worker_id=? AND capability=?", (self.workspace_id,wid,cap)).fetchone():
            raise ValueError(QualificationBlock.DUPLICATE.value)
        profile = self.reputation.profile(worker_id=wid, capability=cap)
        if profile.verified_sample_count < int(minimum_samples):
            raise ValueError(QualificationBlock.INSUFFICIENT_EVIDENCE.value)
        score = round((profile.average_capability_score + profile.average_performance_score) / 2.0, 6)
        if score < minimum_score: raise ValueError(QualificationBlock.SCORE_TOO_LOW.value)
        rec = WorkerCapabilityQualification(qid, self.workspace_id, wid, cap, QualificationStatus.QUALIFIED,
            profile.evidence_ids, profile.verified_sample_count, score, minimum_score, int(minimum_samples), actor,
            str(issued_at), str(expires_at).strip() if expires_at else None, "", True, False, False, False)
        rec = WorkerCapabilityQualification(**{**rec.__dict__, "qualification_fingerprint": rec.fingerprint()})
        self._db.execute("INSERT INTO worker_capability_qualifications VALUES (?,?,?,?,?,?)", (rec.qualification_id,rec.workspace_id,rec.worker_id,rec.capability,json.dumps(rec.canonical_payload(),sort_keys=True),rec.qualification_fingerprint))
        self._db.commit()
        return rec

    def get(self, qualification_id: str) -> Optional[WorkerCapabilityQualification]:
        row = self._db.execute("SELECT payload,fingerprint FROM worker_capability_qualifications WHERE qualification_id=?", (str(qualification_id),)).fetchone()
        if not row: return None
        p=json.loads(row[0]); p["status"]=QualificationStatus(p["status"]); p["evidence_ids"]=tuple(p.get("evidence_ids",())); p["qualification_fingerprint"]=row[1]
        return WorkerCapabilityQualification(**p)

    def get_for_worker(self, worker_id: str, capability: str) -> Optional[WorkerCapabilityQualification]:
        row=self._db.execute("SELECT qualification_id FROM worker_capability_qualifications WHERE workspace_id=? AND worker_id=? AND capability=?",(self.workspace_id,str(worker_id),str(capability))).fetchone()
        return self.get(row[0]) if row else None

    def assert_integrity(self, qualification: WorkerCapabilityQualification) -> None:
        if qualification.workspace_id != self.workspace_id: raise ValueError(QualificationBlock.TENANT_MISMATCH.value)
        if qualification.qualification_fingerprint != qualification.fingerprint(): raise ValueError(QualificationBlock.QUALIFICATION_TAMPERED.value)
        if not qualification.advisory_only or qualification.authority_granted or qualification.execution_requested or qualification.provider_called:
            raise ValueError(QualificationBlock.AUTHORITY_VIOLATION.value)
        if qualification.status not in set(QualificationStatus): raise ValueError(QualificationBlock.INVALID_STATUS.value)
        stored=self.get(qualification.qualification_id)
        if stored is None or stored.qualification_fingerprint != qualification.qualification_fingerprint: raise ValueError(QualificationBlock.QUALIFICATION_TAMPERED.value)
        profile=self.reputation.profile(worker_id=qualification.worker_id, capability=qualification.capability)
        if not set(qualification.evidence_ids).issubset(set(profile.evidence_ids)): raise ValueError(QualificationBlock.QUALIFICATION_TAMPERED.value)
        if qualification.verified_sample_count < qualification.minimum_samples: raise ValueError(QualificationBlock.INSUFFICIENT_EVIDENCE.value)
        if qualification.qualification_score < qualification.minimum_score: raise ValueError(QualificationBlock.SCORE_TOO_LOW.value)

    def is_currently_qualified(self, worker_id: str, capability: str) -> bool:
        q=self.get_for_worker(worker_id, capability)
        if q is None: return False
        self.assert_integrity(q)
        return q.status == QualificationStatus.QUALIFIED

    def context(self, worker_id: str, capability: str) -> dict[str, Any]:
        q=self.get_for_worker(worker_id, capability)
        if q is None: raise ValueError(QualificationBlock.QUALIFICATION_MISSING.value)
        self.assert_integrity(q)
        return {"qualification_id":q.qualification_id,"worker_id":q.worker_id,"capability":q.capability,"status":q.status.value,"qualification_score":q.qualification_score,"verified_sample_count":q.verified_sample_count,"advisory_only":True,"authority_granted":False,"execution_requested":False}

    def close(self): self._db.close()

__all__=["QualificationBlock","QualificationStatus","WorkerCapabilityQualification","WorkerCapabilityQualificationRegistry"]
