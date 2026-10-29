"""V71.14 — Worker Result Evidence Handoff & Verification Intake.

Converts a terminal worker result submission into an immutable verification-intake
record. This is a coordination/evidence boundary only. It does not verify the
business result, authorize anything, execute anything, call providers, or mutate
the canonical business outcome.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any
import hashlib
import json
import sqlite3

from services.v71_13_worker_task_progress import (
    WorkerTaskProgressRegistry, ProgressState,
)


class IntakeState(str, Enum):
    PREPARED = "prepared"
    HANDED_TO_VERIFICATION = "handed_to_verification"
    RETURNED = "returned"
    CANCELLED = "cancelled"


class IntakeBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    PROGRESS_MISSING = "progress_missing"
    RESULT_NOT_SUBMITTED = "result_not_submitted"
    DUPLICATE = "duplicate"
    INVALID_TRANSITION = "invalid_transition"
    REASON_REQUIRED = "reason_required"
    INTAKE_TAMPERED = "intake_tampered"
    PAYLOAD_TOO_LARGE = "payload_too_large"

TERMINAL = frozenset({IntakeState.HANDED_TO_VERIFICATION, IntakeState.CANCELLED})


@dataclass(frozen=True)
class WorkerResultVerificationIntake:
    intake_id: str
    workspace_id: str
    handoff_id: str
    assignment_id: str
    task_id: str
    worker_id: str
    submitted_progress_id: str
    submitted_progress_fingerprint: str
    state: IntakeState
    message: str
    evidence_manifest: dict[str, Any]
    coordination_only: bool = True
    verification_requested: bool = True
    authorization_granted: bool = False
    execution_requested: bool = False
    provider_called: bool = False
    business_outcome_asserted: bool = False
    intake_fingerprint: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "intake_id": self.intake_id,
            "workspace_id": self.workspace_id,
            "handoff_id": self.handoff_id,
            "assignment_id": self.assignment_id,
            "task_id": self.task_id,
            "worker_id": self.worker_id,
            "submitted_progress_id": self.submitted_progress_id,
            "submitted_progress_fingerprint": self.submitted_progress_fingerprint,
            "state": self.state.value,
            "message": self.message,
            "evidence_manifest": self.evidence_manifest,
            "coordination_only": self.coordination_only,
            "verification_requested": self.verification_requested,
            "authorization_granted": self.authorization_granted,
            "execution_requested": self.execution_requested,
            "provider_called": self.provider_called,
            "business_outcome_asserted": self.business_outcome_asserted,
        }

    def fingerprint(self) -> str:
        raw = json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()


class WorkerResultVerificationIntakeRegistry:
    VERSION = "v71.14"
    MAX_MANIFEST_BYTES = 32768

    def __init__(self, workspace_id: str, progress: WorkerTaskProgressRegistry,
                 db_path: str = ":memory:"):
        ws = str(workspace_id or "").strip()
        if not ws or progress.workspace_id != ws:
            raise ValueError(IntakeBlock.TENANT_MISMATCH.value)
        self.workspace_id = ws
        self.progress = progress
        self._db = sqlite3.connect(db_path)
        self._db.execute("""CREATE TABLE IF NOT EXISTS worker_result_verification_intake (
            intake_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            handoff_id TEXT NOT NULL,
            assignment_id TEXT NOT NULL,
            submitted_progress_id TEXT NOT NULL,
            state TEXT NOT NULL,
            payload TEXT NOT NULL,
            fingerprint TEXT NOT NULL
        )""")
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_result_intake_handoff ON worker_result_verification_intake(handoff_id)")
        self._db.commit()

    def prepare(self, *, intake_id: str, handoff_id: str,
                message: str = "result_ready_for_verification") -> WorkerResultVerificationIntake:
        iid, hid, msg = (str(x or "").strip() for x in (intake_id, handoff_id, message))
        if not iid or not hid:
            raise ValueError(IntakeBlock.PROGRESS_MISSING.value)
        if self.get(iid) is not None:
            raise ValueError(IntakeBlock.DUPLICATE.value)
        submitted = self.progress.latest_for_handoff(hid)
        if submitted is None:
            raise ValueError(IntakeBlock.PROGRESS_MISSING.value)
        self.progress.assert_integrity(submitted)
        if submitted.state is not ProgressState.SUBMITTED:
            raise ValueError(IntakeBlock.RESULT_NOT_SUBMITTED.value)
        manifest = {
            "progress_id": submitted.progress_id,
            "progress_fingerprint": submitted.progress_fingerprint,
            "sequence": submitted.sequence,
            "evidence": submitted.evidence,
            "source": "worker_task_progress",
        }
        return self._insert(
            intake_id=iid,
            submitted=submitted,
            state=IntakeState.PREPARED,
            message=msg,
            evidence_manifest=manifest,
        )

    def hand_to_verification(self, *, intake_id: str,
                             message: str = "handed_to_verification") -> WorkerResultVerificationIntake:
        current = self._require(intake_id)
        if current.state is not IntakeState.PREPARED:
            raise ValueError(IntakeBlock.INVALID_TRANSITION.value)
        return self._transition(current, IntakeState.HANDED_TO_VERIFICATION, message)

    def return_for_clarification(self, *, intake_id: str,
                                 message: str) -> WorkerResultVerificationIntake:
        current = self._require(intake_id)
        if current.state not in (IntakeState.PREPARED, IntakeState.HANDED_TO_VERIFICATION):
            raise ValueError(IntakeBlock.INVALID_TRANSITION.value)
        if not str(message or "").strip():
            raise ValueError(IntakeBlock.REASON_REQUIRED.value)
        return self._transition(current, IntakeState.RETURNED, message)

    def cancel(self, *, intake_id: str, message: str) -> WorkerResultVerificationIntake:
        current = self._require(intake_id)
        if current.state in TERMINAL or current.state is IntakeState.RETURNED:
            raise ValueError(IntakeBlock.INVALID_TRANSITION.value)
        if not str(message or "").strip():
            raise ValueError(IntakeBlock.REASON_REQUIRED.value)
        return self._transition(current, IntakeState.CANCELLED, message)

    def _insert(self, *, intake_id: str, submitted, state: IntakeState,
                message: str, evidence_manifest: dict[str, Any]) -> WorkerResultVerificationIntake:
        raw = json.dumps(evidence_manifest, sort_keys=True, separators=(",", ":"))
        if len(raw.encode()) > self.MAX_MANIFEST_BYTES:
            raise ValueError(IntakeBlock.PAYLOAD_TOO_LARGE.value)
        record = WorkerResultVerificationIntake(
            intake_id, self.workspace_id, submitted.handoff_id, submitted.assignment_id,
            submitted.task_id, submitted.worker_id, submitted.progress_id,
            submitted.progress_fingerprint, state, message, evidence_manifest,
            True, True, False, False, False, False, ""
        )
        record = WorkerResultVerificationIntake(**{**record.__dict__, "intake_fingerprint": record.fingerprint()})
        self._db.execute("INSERT INTO worker_result_verification_intake VALUES (?,?,?,?,?,?,?,?)", (
            record.intake_id, record.workspace_id, record.handoff_id, record.assignment_id,
            record.submitted_progress_id, record.state.value,
            json.dumps(record.canonical_payload(), sort_keys=True), record.intake_fingerprint))
        self._db.commit()
        return record

    def _transition(self, current: WorkerResultVerificationIntake,
                    state: IntakeState, message: str) -> WorkerResultVerificationIntake:
        updated = WorkerResultVerificationIntake(**{
            **current.__dict__, "state": state, "message": str(message).strip(),
            "intake_fingerprint": "",
        })
        updated = WorkerResultVerificationIntake(**{**updated.__dict__, "intake_fingerprint": updated.fingerprint()})
        self._db.execute(
            "UPDATE worker_result_verification_intake SET state=?, payload=?, fingerprint=? WHERE intake_id=?",
            (updated.state.value, json.dumps(updated.canonical_payload(), sort_keys=True),
             updated.intake_fingerprint, updated.intake_id),
        )
        self._db.commit()
        return updated

    def get(self, intake_id: str) -> WorkerResultVerificationIntake | None:
        row = self._db.execute(
            "SELECT payload, fingerprint FROM worker_result_verification_intake WHERE intake_id=?",
            (str(intake_id),),
        ).fetchone()
        if not row:
            return None
        payload = json.loads(row[0])
        payload["state"] = IntakeState(payload["state"])
        payload["intake_fingerprint"] = row[1]
        return WorkerResultVerificationIntake(**payload)

    def latest_for_handoff(self, handoff_id: str) -> WorkerResultVerificationIntake | None:
        row = self._db.execute(
            "SELECT intake_id FROM worker_result_verification_intake WHERE handoff_id=? ORDER BY rowid DESC LIMIT 1",
            (str(handoff_id),)).fetchone()
        return self.get(row[0]) if row else None

    def _require(self, intake_id: str) -> WorkerResultVerificationIntake:
        item = self.get(intake_id)
        if item is None:
            raise ValueError(IntakeBlock.PROGRESS_MISSING.value)
        self.assert_integrity(item)
        return item

    def assert_integrity(self, item: WorkerResultVerificationIntake) -> None:
        if item.workspace_id != self.workspace_id:
            raise ValueError(IntakeBlock.TENANT_MISMATCH.value)
        if (not item.coordination_only or not item.verification_requested or
                item.authorization_granted or item.execution_requested or
                item.provider_called or item.business_outcome_asserted):
            raise ValueError(IntakeBlock.INTAKE_TAMPERED.value)
        if not item.intake_fingerprint or item.intake_fingerprint != item.fingerprint():
            raise ValueError(IntakeBlock.INTAKE_TAMPERED.value)
        stored = self.get(item.intake_id)
        if stored is None or stored.intake_fingerprint != item.intake_fingerprint:
            raise ValueError(IntakeBlock.INTAKE_TAMPERED.value)
        submitted = self.progress.get(item.submitted_progress_id)
        if submitted is None:
            raise ValueError(IntakeBlock.PROGRESS_MISSING.value)
        self.progress.assert_integrity(submitted)
        if submitted.progress_fingerprint != item.submitted_progress_fingerprint:
            raise ValueError(IntakeBlock.INTAKE_TAMPERED.value)

    def verification_context(self, intake_id: str) -> dict[str, Any]:
        item = self._require(intake_id)
        return {
            "intake_id": item.intake_id,
            "workspace_id": item.workspace_id,
            "handoff_id": item.handoff_id,
            "assignment_id": item.assignment_id,
            "task_id": item.task_id,
            "worker_id": item.worker_id,
            "submitted_progress_id": item.submitted_progress_id,
            "submitted_progress_fingerprint": item.submitted_progress_fingerprint,
            "state": item.state.value,
            "verification_requested": True,
            "authorization_granted": False,
            "execution_requested": False,
            "provider_called": False,
            "business_outcome_asserted": False,
        }

    def close(self) -> None:
        self._db.close()


__all__ = ["IntakeState", "IntakeBlock", "WorkerResultVerificationIntake",
           "WorkerResultVerificationIntakeRegistry"]
