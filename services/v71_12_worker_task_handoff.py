"""V71.12 — Governed Worker Task Handoff & Acknowledgement.

Bridges an active workforce assignment into a durable worker-facing task
handoff.  The handoff is coordination state only: acknowledgement does not
authorize, execute, retry, or reconcile the task.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any
import hashlib, json, sqlite3

from services.v71_25_worker_assignment import WorkerAssignmentAdmission
from services.v71_26_worker_assignment_lifecycle import (
    AssignmentLifecycleState, WorkerAssignmentLifecycleRegistry,
)


class HandoffState(str, Enum):
    OFFERED = "offered"
    ACKNOWLEDGED = "acknowledged"
    DECLINED = "declined"
    CANCELLED = "cancelled"


class HandoffBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    ASSIGNMENT_MISSING = "assignment_missing"
    ASSIGNMENT_INVALID = "assignment_invalid"
    LIFECYCLE_MISSING = "lifecycle_missing"
    LIFECYCLE_NOT_ACTIVE = "lifecycle_not_active"
    DUPLICATE = "duplicate"
    INVALID_TRANSITION = "invalid_transition"
    TERMINAL_REASON_REQUIRED = "terminal_reason_required"
    HANDOFF_TAMPERED = "handoff_tampered"


TERMINAL = frozenset({HandoffState.DECLINED, HandoffState.CANCELLED})


@dataclass(frozen=True)
class WorkerTaskHandoff:
    handoff_id: str
    workspace_id: str
    assignment_id: str
    task_id: str
    worker_id: str
    assignment_fingerprint: str
    lifecycle_id: str
    lifecycle_fingerprint: str
    state: HandoffState
    reason: str
    coordination_only: bool = True
    authorization_granted: bool = False
    execution_requested: bool = False
    provider_called: bool = False
    handoff_fingerprint: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "handoff_id": self.handoff_id,
            "workspace_id": self.workspace_id,
            "assignment_id": self.assignment_id,
            "task_id": self.task_id,
            "worker_id": self.worker_id,
            "assignment_fingerprint": self.assignment_fingerprint,
            "lifecycle_id": self.lifecycle_id,
            "lifecycle_fingerprint": self.lifecycle_fingerprint,
            "state": self.state.value,
            "reason": self.reason,
            "coordination_only": self.coordination_only,
            "authorization_granted": self.authorization_granted,
            "execution_requested": self.execution_requested,
            "provider_called": self.provider_called,
        }

    def fingerprint(self) -> str:
        raw = json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()


class WorkerTaskHandoffRegistry:
    VERSION = "v71.27"

    def __init__(self, workspace_id: str, admission: WorkerAssignmentAdmission,
                 lifecycle: WorkerAssignmentLifecycleRegistry,
                 db_path: str = ":memory:"):
        ws = str(workspace_id or "").strip()
        if not ws or admission.workspace_id != ws or lifecycle.workspace_id != ws:
            raise ValueError(HandoffBlock.TENANT_MISMATCH.value)
        self.workspace_id = ws
        self.admission = admission
        self.lifecycle = lifecycle
        self._db = sqlite3.connect(db_path)
        self._db.execute("""CREATE TABLE IF NOT EXISTS workforce_task_handoffs (
            handoff_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            assignment_id TEXT NOT NULL,
            state TEXT NOT NULL,
            payload TEXT NOT NULL,
            fingerprint TEXT NOT NULL
        )""")
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_task_handoff_assignment ON workforce_task_handoffs(assignment_id)")
        self._db.commit()

    def offer(self, *, handoff_id: str, assignment_id: str, reason: str = "task_offered") -> WorkerTaskHandoff:
        return self._transition(handoff_id=handoff_id, assignment_id=assignment_id,
                                state=HandoffState.OFFERED, reason=reason)

    def acknowledge(self, *, handoff_id: str, assignment_id: str, reason: str = "worker_acknowledged") -> WorkerTaskHandoff:
        return self._transition(handoff_id=handoff_id, assignment_id=assignment_id,
                                state=HandoffState.ACKNOWLEDGED, reason=reason)

    def decline(self, *, handoff_id: str, assignment_id: str, reason: str) -> WorkerTaskHandoff:
        return self._transition(handoff_id=handoff_id, assignment_id=assignment_id,
                                state=HandoffState.DECLINED, reason=reason)

    def cancel(self, *, handoff_id: str, assignment_id: str, reason: str) -> WorkerTaskHandoff:
        return self._transition(handoff_id=handoff_id, assignment_id=assignment_id,
                                state=HandoffState.CANCELLED, reason=reason)

    def _transition(self, *, handoff_id: str, assignment_id: str,
                    state: HandoffState, reason: str) -> WorkerTaskHandoff:
        hid, aid, why = (str(x or "").strip() for x in (handoff_id, assignment_id, reason))
        if not hid or not aid:
            raise ValueError(HandoffBlock.ASSIGNMENT_MISSING.value)
        if self.get(hid) is not None:
            raise ValueError(HandoffBlock.DUPLICATE.value)
        if state in TERMINAL and not why:
            raise ValueError(HandoffBlock.TERMINAL_REASON_REQUIRED.value)

        assignment = self.admission.get(aid)
        if assignment is None:
            raise ValueError(HandoffBlock.ASSIGNMENT_MISSING.value)
        self.admission.assert_integrity(assignment)
        if assignment.workspace_id != self.workspace_id:
            raise ValueError(HandoffBlock.TENANT_MISMATCH.value)

        current = self.lifecycle.latest_for_assignment(aid)
        if current is None:
            raise ValueError(HandoffBlock.LIFECYCLE_MISSING.value)
        self.lifecycle.assert_integrity(current)
        if current.state is not AssignmentLifecycleState.ACTIVE:
            raise ValueError(HandoffBlock.LIFECYCLE_NOT_ACTIVE.value)

        prior = self.latest_for_assignment(aid)
        if prior is not None:
            if prior.state in TERMINAL:
                raise ValueError(HandoffBlock.INVALID_TRANSITION.value)
            if state is HandoffState.OFFERED:
                raise ValueError(HandoffBlock.INVALID_TRANSITION.value)
            if prior.state is HandoffState.OFFERED and state not in (HandoffState.ACKNOWLEDGED, HandoffState.DECLINED, HandoffState.CANCELLED):
                raise ValueError(HandoffBlock.INVALID_TRANSITION.value)
            if prior.state is HandoffState.ACKNOWLEDGED:
                raise ValueError(HandoffBlock.INVALID_TRANSITION.value)

        record = WorkerTaskHandoff(
            hid, self.workspace_id, assignment.assignment_id, assignment.task_id,
            assignment.worker_id, assignment.assignment_fingerprint,
            current.lifecycle_id, current.lifecycle_fingerprint, state, why,
            True, False, False, False, ""
        )
        record = WorkerTaskHandoff(**{**record.__dict__, "handoff_fingerprint": record.fingerprint()})
        self._db.execute("INSERT INTO workforce_task_handoffs VALUES (?,?,?,?,?,?)",
                          (record.handoff_id, record.workspace_id, record.assignment_id,
                           record.state.value, json.dumps(record.canonical_payload(), sort_keys=True),
                           record.handoff_fingerprint))
        self._db.commit()
        return record

    def get(self, handoff_id: str) -> WorkerTaskHandoff | None:
        row = self._db.execute("SELECT payload, fingerprint FROM workforce_task_handoffs WHERE handoff_id=?", (str(handoff_id),)).fetchone()
        if not row:
            return None
        p = json.loads(row[0])
        p["state"] = HandoffState(p["state"])
        p["handoff_fingerprint"] = row[1]
        return WorkerTaskHandoff(**p)

    def latest_for_assignment(self, assignment_id: str) -> WorkerTaskHandoff | None:
        row = self._db.execute("SELECT handoff_id FROM workforce_task_handoffs WHERE assignment_id=? ORDER BY rowid DESC LIMIT 1", (str(assignment_id),)).fetchone()
        return self.get(row[0]) if row else None

    def assert_integrity(self, handoff: WorkerTaskHandoff) -> None:
        if handoff.workspace_id != self.workspace_id:
            raise ValueError(HandoffBlock.TENANT_MISMATCH.value)
        if not handoff.coordination_only or handoff.authorization_granted or handoff.execution_requested or handoff.provider_called:
            raise ValueError(HandoffBlock.HANDOFF_TAMPERED.value)
        if handoff.handoff_fingerprint != handoff.fingerprint():
            raise ValueError(HandoffBlock.HANDOFF_TAMPERED.value)
        stored = self.get(handoff.handoff_id)
        if stored is None or stored.handoff_fingerprint != handoff.handoff_fingerprint:
            raise ValueError(HandoffBlock.HANDOFF_TAMPERED.value)

    def context(self, assignment_id: str) -> dict[str, Any]:
        handoff = self.latest_for_assignment(assignment_id)
        if handoff is None:
            raise ValueError(HandoffBlock.ASSIGNMENT_MISSING.value)
        self.assert_integrity(handoff)
        return {
            "handoff_id": handoff.handoff_id,
            "assignment_id": handoff.assignment_id,
            "task_id": handoff.task_id,
            "worker_id": handoff.worker_id,
            "state": handoff.state.value,
            "coordination_only": True,
            "authorization_granted": False,
            "execution_requested": False,
            "provider_called": False,
        }

    def close(self) -> None:
        self._db.close()


__all__ = ["HandoffState", "HandoffBlock", "WorkerTaskHandoff", "WorkerTaskHandoffRegistry"]
