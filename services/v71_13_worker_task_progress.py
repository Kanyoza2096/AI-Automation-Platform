"""V71.13 — Durable Worker Task Progress & Result Submission Journal.

Records worker-local execution progress after an acknowledged handoff.  The
journal is evidence/coordination state only: a worker cannot authorize,
execute providers, or declare a canonical business outcome through it.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any
import hashlib, json, sqlite3

from services.v71_12_worker_task_handoff import WorkerTaskHandoffRegistry, HandoffState


class ProgressState(str, Enum):
    STARTED = "started"
    PROGRESS = "progress"
    BLOCKED = "blocked"
    SUBMITTED = "submitted"
    ABANDONED = "abandoned"


class ProgressBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    HANDOFF_MISSING = "handoff_missing"
    HANDOFF_NOT_ACKNOWLEDGED = "handoff_not_acknowledged"
    DUPLICATE = "duplicate"
    INVALID_TRANSITION = "invalid_transition"
    REASON_REQUIRED = "reason_required"
    INVALID_SEQUENCE = "invalid_sequence"
    PROGRESS_TAMPERED = "progress_tampered"
    PAYLOAD_TOO_LARGE = "payload_too_large"

TERMINAL = frozenset({ProgressState.SUBMITTED, ProgressState.ABANDONED})


@dataclass(frozen=True)
class WorkerTaskProgress:
    progress_id: str
    workspace_id: str
    handoff_id: str
    assignment_id: str
    task_id: str
    worker_id: str
    sequence: int
    state: ProgressState
    message: str
    evidence: dict[str, Any]
    coordination_only: bool = True
    authorization_granted: bool = False
    execution_requested: bool = False
    provider_called: bool = False
    business_outcome_asserted: bool = False
    progress_fingerprint: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "progress_id": self.progress_id,
            "workspace_id": self.workspace_id,
            "handoff_id": self.handoff_id,
            "assignment_id": self.assignment_id,
            "task_id": self.task_id,
            "worker_id": self.worker_id,
            "sequence": self.sequence,
            "state": self.state.value,
            "message": self.message,
            "evidence": self.evidence,
            "coordination_only": self.coordination_only,
            "authorization_granted": self.authorization_granted,
            "execution_requested": self.execution_requested,
            "provider_called": self.provider_called,
            "business_outcome_asserted": self.business_outcome_asserted,
        }

    def fingerprint(self) -> str:
        raw = json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()


class WorkerTaskProgressRegistry:
    VERSION = "v71.13"
    MAX_EVIDENCE_BYTES = 32768

    def __init__(self, workspace_id: str, handoffs: WorkerTaskHandoffRegistry,
                 db_path: str = ":memory:"):
        ws = str(workspace_id or "").strip()
        if not ws or handoffs.workspace_id != ws:
            raise ValueError(ProgressBlock.TENANT_MISMATCH.value)
        self.workspace_id = ws
        self.handoffs = handoffs
        self._db = sqlite3.connect(db_path)
        self._db.execute("""CREATE TABLE IF NOT EXISTS workforce_task_progress (
            progress_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            handoff_id TEXT NOT NULL,
            assignment_id TEXT NOT NULL,
            sequence INTEGER NOT NULL,
            state TEXT NOT NULL,
            payload TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            UNIQUE(handoff_id, sequence)
        )""")
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_task_progress_handoff ON workforce_task_progress(handoff_id, sequence)")
        self._db.commit()

    def start(self, *, progress_id: str, handoff_id: str, message: str = "worker_started",
              evidence: dict[str, Any] | None = None) -> WorkerTaskProgress:
        return self.record(progress_id=progress_id, handoff_id=handoff_id,
                           state=ProgressState.STARTED, message=message, evidence=evidence or {})

    def report_progress(self, *, progress_id: str, handoff_id: str, message: str,
                        evidence: dict[str, Any] | None = None) -> WorkerTaskProgress:
        return self.record(progress_id=progress_id, handoff_id=handoff_id,
                           state=ProgressState.PROGRESS, message=message, evidence=evidence or {})

    def block(self, *, progress_id: str, handoff_id: str, message: str,
              evidence: dict[str, Any] | None = None) -> WorkerTaskProgress:
        return self.record(progress_id=progress_id, handoff_id=handoff_id,
                           state=ProgressState.BLOCKED, message=message, evidence=evidence or {})

    def submit(self, *, progress_id: str, handoff_id: str, message: str,
               evidence: dict[str, Any] | None = None) -> WorkerTaskProgress:
        return self.record(progress_id=progress_id, handoff_id=handoff_id,
                           state=ProgressState.SUBMITTED, message=message, evidence=evidence or {})

    def abandon(self, *, progress_id: str, handoff_id: str, message: str,
                evidence: dict[str, Any] | None = None) -> WorkerTaskProgress:
        return self.record(progress_id=progress_id, handoff_id=handoff_id,
                           state=ProgressState.ABANDONED, message=message, evidence=evidence or {})

    def record(self, *, progress_id: str, handoff_id: str, state: ProgressState,
               message: str, evidence: dict[str, Any]) -> WorkerTaskProgress:
        pid, hid, msg = (str(x or "").strip() for x in (progress_id, handoff_id, message))
        if not pid or not hid:
            raise ValueError(ProgressBlock.HANDOFF_MISSING.value)
        if not msg and state in (ProgressState.BLOCKED, ProgressState.SUBMITTED, ProgressState.ABANDONED):
            raise ValueError(ProgressBlock.REASON_REQUIRED.value)
        if self.get(pid) is not None:
            raise ValueError(ProgressBlock.DUPLICATE.value)
        handoff = self.handoffs.get(hid)
        if handoff is None:
            raise ValueError(ProgressBlock.HANDOFF_MISSING.value)
        self.handoffs.assert_integrity(handoff)
        if handoff.workspace_id != self.workspace_id:
            raise ValueError(ProgressBlock.TENANT_MISMATCH.value)
        if handoff.state is not HandoffState.ACKNOWLEDGED:
            raise ValueError(ProgressBlock.HANDOFF_NOT_ACKNOWLEDGED.value)
        raw_evidence = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
        if len(raw_evidence.encode()) > self.MAX_EVIDENCE_BYTES:
            raise ValueError(ProgressBlock.PAYLOAD_TOO_LARGE.value)
        prior = self.latest_for_handoff(hid)
        sequence = 1 if prior is None else prior.sequence + 1
        if prior is not None:
            if prior.state in TERMINAL:
                raise ValueError(ProgressBlock.INVALID_TRANSITION.value)
            if state is ProgressState.STARTED:
                raise ValueError(ProgressBlock.INVALID_TRANSITION.value)
            if prior.state is ProgressState.BLOCKED and state not in (ProgressState.PROGRESS, ProgressState.SUBMITTED, ProgressState.ABANDONED):
                raise ValueError(ProgressBlock.INVALID_TRANSITION.value)
        record = WorkerTaskProgress(
            pid, self.workspace_id, handoff.handoff_id, handoff.assignment_id,
            handoff.task_id, handoff.worker_id, sequence, state, msg, dict(evidence),
            True, False, False, False, False, ""
        )
        record = WorkerTaskProgress(**{**record.__dict__, "progress_fingerprint": record.fingerprint()})
        self._db.execute("INSERT INTO workforce_task_progress VALUES (?,?,?,?,?,?,?,?)", (
            record.progress_id, record.workspace_id, record.handoff_id, record.assignment_id,
            record.sequence, record.state.value, json.dumps(record.canonical_payload(), sort_keys=True),
            record.progress_fingerprint))
        self._db.commit()
        return record

    def get(self, progress_id: str) -> WorkerTaskProgress | None:
        row = self._db.execute("SELECT payload, fingerprint FROM workforce_task_progress WHERE progress_id=?", (str(progress_id),)).fetchone()
        if not row:
            return None
        payload = json.loads(row[0])
        payload["state"] = ProgressState(payload["state"])
        payload["progress_fingerprint"] = row[1]
        return WorkerTaskProgress(**payload)

    def latest_for_handoff(self, handoff_id: str) -> WorkerTaskProgress | None:
        row = self._db.execute("SELECT progress_id FROM workforce_task_progress WHERE handoff_id=? ORDER BY sequence DESC LIMIT 1", (str(handoff_id),)).fetchone()
        return self.get(row[0]) if row else None

    def history(self, handoff_id: str) -> tuple[WorkerTaskProgress, ...]:
        rows = self._db.execute("SELECT progress_id FROM workforce_task_progress WHERE handoff_id=? ORDER BY sequence ASC", (str(handoff_id),)).fetchall()
        return tuple(self.get(row[0]) for row in rows if self.get(row[0]) is not None)

    def assert_integrity(self, progress: WorkerTaskProgress) -> None:
        if progress.workspace_id != self.workspace_id:
            raise ValueError(ProgressBlock.TENANT_MISMATCH.value)
        if (not progress.coordination_only or progress.authorization_granted or
                progress.execution_requested or progress.provider_called or
                progress.business_outcome_asserted):
            raise ValueError(ProgressBlock.PROGRESS_TAMPERED.value)
        if progress.sequence < 1 or progress.progress_fingerprint != progress.fingerprint():
            raise ValueError(ProgressBlock.PROGRESS_TAMPERED.value)
        stored = self.get(progress.progress_id)
        if stored is None or stored.progress_fingerprint != progress.progress_fingerprint:
            raise ValueError(ProgressBlock.PROGRESS_TAMPERED.value)

    def context(self, handoff_id: str) -> dict[str, Any]:
        latest = self.latest_for_handoff(handoff_id)
        if latest is None:
            raise ValueError(ProgressBlock.HANDOFF_MISSING.value)
        self.assert_integrity(latest)
        return {
            "handoff_id": latest.handoff_id,
            "assignment_id": latest.assignment_id,
            "task_id": latest.task_id,
            "worker_id": latest.worker_id,
            "sequence": latest.sequence,
            "state": latest.state.value,
            "coordination_only": True,
            "authorization_granted": False,
            "execution_requested": False,
            "provider_called": False,
            "business_outcome_asserted": False,
        }

    def close(self) -> None:
        self._db.close()


__all__ = ["ProgressState", "ProgressBlock", "WorkerTaskProgress", "WorkerTaskProgressRegistry"]
