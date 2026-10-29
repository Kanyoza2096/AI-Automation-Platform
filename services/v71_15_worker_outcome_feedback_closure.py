"""V71.15 — Worker Outcome Feedback & Task Closure Boundary.

Consumes an authoritative verification disposition and closes the workforce
assignment without becoming the business-outcome authority. The verifier owns
truth; this layer records the verified disposition for workforce coordination
and releases the assignment's logical capacity exactly once.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any
import hashlib, json, sqlite3

from services.v71_14_worker_result_verification_intake import (
    WorkerResultVerificationIntakeRegistry, IntakeState,
)
from services.v71_26_worker_assignment_lifecycle import (
    WorkerAssignmentLifecycleRegistry, AssignmentLifecycleState,
)


class VerifiedDisposition(str, Enum):
    VERIFIED_SUCCESS = "verified_success"
    VERIFIED_FAILURE = "verified_failure"
    VERIFIED_UNCERTAIN = "verified_uncertain"
    VERIFIED_REJECTED = "verified_rejected"


class ClosureState(str, Enum):
    CLOSED = "closed"
    ESCALATED = "escalated"


class ClosureBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    INTAKE_MISSING = "intake_missing"
    INTAKE_NOT_HANDED = "intake_not_handed_to_verification"
    INTAKE_TAMPERED = "intake_tampered"
    VERIFICATION_ID_REQUIRED = "verification_id_required"
    VERIFICATION_FINGERPRINT_REQUIRED = "verification_fingerprint_required"
    INVALID_DISPOSITION = "invalid_disposition"
    DUPLICATE = "duplicate"
    LIFECYCLE_MISSING = "lifecycle_missing"
    LIFECYCLE_NOT_ACTIVE = "lifecycle_not_active"
    LIFECYCLE_TAMPERED = "lifecycle_tampered"
    CLOSURE_TAMPERED = "closure_tampered"
    CAPACITY_RELEASE_FAILED = "capacity_release_failed"


@dataclass(frozen=True)
class WorkerOutcomeFeedbackClosure:
    closure_id: str
    workspace_id: str
    intake_id: str
    assignment_id: str
    task_id: str
    worker_id: str
    verification_id: str
    verification_fingerprint: str
    disposition: VerifiedDisposition
    state: ClosureState
    reason: str
    lifecycle_release_id: str
    coordination_only: bool = True
    authorization_granted: bool = False
    execution_requested: bool = False
    provider_called: bool = False
    business_outcome_owned: bool = False
    closure_fingerprint: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "closure_id": self.closure_id,
            "workspace_id": self.workspace_id,
            "intake_id": self.intake_id,
            "assignment_id": self.assignment_id,
            "task_id": self.task_id,
            "worker_id": self.worker_id,
            "verification_id": self.verification_id,
            "verification_fingerprint": self.verification_fingerprint,
            "disposition": self.disposition.value,
            "state": self.state.value,
            "reason": self.reason,
            "lifecycle_release_id": self.lifecycle_release_id,
            "coordination_only": self.coordination_only,
            "authorization_granted": self.authorization_granted,
            "execution_requested": self.execution_requested,
            "provider_called": self.provider_called,
            "business_outcome_owned": self.business_outcome_owned,
        }

    def fingerprint(self) -> str:
        raw = json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()


class WorkerOutcomeFeedbackClosureRegistry:
    VERSION = "v71.15"

    def __init__(self, workspace_id: str,
                 intake: WorkerResultVerificationIntakeRegistry,
                 lifecycle: WorkerAssignmentLifecycleRegistry,
                 db_path: str = ":memory:"):
        ws = str(workspace_id or "").strip()
        if not ws or intake.workspace_id != ws or lifecycle.workspace_id != ws:
            raise ValueError(ClosureBlock.TENANT_MISMATCH.value)
        self.workspace_id = ws
        self.intake = intake
        self.lifecycle = lifecycle
        self._db = sqlite3.connect(db_path)
        self._db.execute("""CREATE TABLE IF NOT EXISTS workforce_outcome_feedback_closure (
            closure_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            intake_id TEXT NOT NULL,
            assignment_id TEXT NOT NULL,
            state TEXT NOT NULL,
            payload TEXT NOT NULL,
            fingerprint TEXT NOT NULL
        )""")
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_worker_closure_assignment ON workforce_outcome_feedback_closure(assignment_id)")
        self._db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_worker_closure_intake ON workforce_outcome_feedback_closure(intake_id)")
        self._db.commit()

    def close_from_verification(self, *, closure_id: str, intake_id: str,
                                verification_id: str,
                                verification_fingerprint: str,
                                disposition: VerifiedDisposition | str,
                                reason: str = "verified_disposition_received") -> WorkerOutcomeFeedbackClosure:
        cid, iid, vid, vfp = (str(x or "").strip() for x in
                              (closure_id, intake_id, verification_id, verification_fingerprint))
        if not cid or not iid:
            raise ValueError(ClosureBlock.INTAKE_MISSING.value)
        if not vid:
            raise ValueError(ClosureBlock.VERIFICATION_ID_REQUIRED.value)
        if not vfp:
            raise ValueError(ClosureBlock.VERIFICATION_FINGERPRINT_REQUIRED.value)
        if self.get(cid) is not None:
            raise ValueError(ClosureBlock.DUPLICATE.value)
        existing = self._db.execute("SELECT closure_id FROM workforce_outcome_feedback_closure WHERE intake_id=?", (iid,)).fetchone()
        if existing:
            raise ValueError(ClosureBlock.DUPLICATE.value)
        try:
            disp = disposition if isinstance(disposition, VerifiedDisposition) else VerifiedDisposition(str(disposition))
        except ValueError as exc:
            raise ValueError(ClosureBlock.INVALID_DISPOSITION.value) from exc

        intake = self.intake.get(iid)
        if intake is None:
            raise ValueError(ClosureBlock.INTAKE_MISSING.value)
        self.intake.assert_integrity(intake)
        if intake.state is not IntakeState.HANDED_TO_VERIFICATION:
            raise ValueError(ClosureBlock.INTAKE_NOT_HANDED.value)

        current = self.lifecycle.latest_for_assignment(intake.assignment_id)
        if current is None:
            raise ValueError(ClosureBlock.LIFECYCLE_MISSING.value)
        self.lifecycle.assert_integrity(current)
        if current.state is not AssignmentLifecycleState.ACTIVE:
            raise ValueError(ClosureBlock.LIFECYCLE_NOT_ACTIVE.value)

        # Workforce closure never writes the canonical business outcome. It only
        # records the verifier's disposition and releases logical assignment capacity.
        release_id = f"{cid}:release"
        try:
            released = self.lifecycle.release(
                lifecycle_id=release_id,
                assignment_id=intake.assignment_id,
                reason=f"verified:{disp.value}",
            )
        except ValueError as exc:
            raise ValueError(ClosureBlock.CAPACITY_RELEASE_FAILED.value) from exc

        state = ClosureState.ESCALATED if disp in (VerifiedDisposition.VERIFIED_UNCERTAIN, VerifiedDisposition.VERIFIED_REJECTED) else ClosureState.CLOSED
        record = WorkerOutcomeFeedbackClosure(
            cid, self.workspace_id, intake.intake_id, intake.assignment_id,
            intake.task_id, intake.worker_id, vid, vfp, disp, state,
            str(reason or "verified_disposition_received").strip() or "verified_disposition_received",
            released.lifecycle_id, True, False, False, False, False, ""
        )
        record = WorkerOutcomeFeedbackClosure(**{**record.__dict__, "closure_fingerprint": record.fingerprint()})
        self._db.execute("INSERT INTO workforce_outcome_feedback_closure VALUES (?,?,?,?,?,?,?)", (
            record.closure_id, record.workspace_id, record.intake_id,
            record.assignment_id, record.state.value,
            json.dumps(record.canonical_payload(), sort_keys=True), record.closure_fingerprint,
        ))
        self._db.commit()
        return record

    def get(self, closure_id: str) -> WorkerOutcomeFeedbackClosure | None:
        row = self._db.execute("SELECT payload, fingerprint FROM workforce_outcome_feedback_closure WHERE closure_id=?", (str(closure_id),)).fetchone()
        if not row:
            return None
        p = json.loads(row[0])
        p["disposition"] = VerifiedDisposition(p["disposition"])
        p["state"] = ClosureState(p["state"])
        p["closure_fingerprint"] = row[1]
        return WorkerOutcomeFeedbackClosure(**p)

    def latest_for_assignment(self, assignment_id: str) -> WorkerOutcomeFeedbackClosure | None:
        row = self._db.execute("SELECT closure_id FROM workforce_outcome_feedback_closure WHERE assignment_id=? ORDER BY rowid DESC LIMIT 1", (str(assignment_id),)).fetchone()
        return self.get(row[0]) if row else None

    def assert_integrity(self, record: WorkerOutcomeFeedbackClosure) -> None:
        if record.workspace_id != self.workspace_id:
            raise ValueError(ClosureBlock.TENANT_MISMATCH.value)
        if (not record.coordination_only or record.authorization_granted or
                record.execution_requested or record.provider_called or record.business_outcome_owned):
            raise ValueError(ClosureBlock.CLOSURE_TAMPERED.value)
        if not record.closure_fingerprint or record.closure_fingerprint != record.fingerprint():
            raise ValueError(ClosureBlock.CLOSURE_TAMPERED.value)
        stored = self.get(record.closure_id)
        if stored is None or stored.closure_fingerprint != record.closure_fingerprint:
            raise ValueError(ClosureBlock.CLOSURE_TAMPERED.value)
        intake = self.intake.get(record.intake_id)
        if intake is None:
            raise ValueError(ClosureBlock.INTAKE_MISSING.value)
        self.intake.assert_integrity(intake)
        if intake.assignment_id != record.assignment_id or intake.workspace_id != record.workspace_id:
            raise ValueError(ClosureBlock.CLOSURE_TAMPERED.value)

    def context(self, assignment_id: str) -> dict[str, Any]:
        record = self.latest_for_assignment(assignment_id)
        if record is None:
            raise ValueError(ClosureBlock.INTAKE_MISSING.value)
        self.assert_integrity(record)
        return {
            "assignment_id": record.assignment_id,
            "task_id": record.task_id,
            "worker_id": record.worker_id,
            "verification_id": record.verification_id,
            "verified_disposition": record.disposition.value,
            "closure_state": record.state.value,
            "coordination_only": True,
            "authorization_granted": False,
            "execution_requested": False,
            "provider_called": False,
            "business_outcome_owned": False,
        }

    def close(self) -> None:
        self._db.close()


__all__ = ["VerifiedDisposition", "ClosureState", "ClosureBlock",
           "WorkerOutcomeFeedbackClosure", "WorkerOutcomeFeedbackClosureRegistry"]
