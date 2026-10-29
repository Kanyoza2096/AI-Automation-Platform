"""V71.26 — Governed Worker Assignment Lifecycle.

Tracks the coordination lifecycle of an admitted worker assignment and its
logical capacity consumption. This layer is deliberately not an execution
runtime: terminal execution/business outcomes remain authoritative elsewhere.

Invariant:
    assignment lifecycle != authorization != execution != business outcome
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any
import hashlib, json, sqlite3

from services.v71_25_worker_assignment import WorkerAssignmentAdmission, WorkerAssignment
from services.v71_9_worker_selection import WorkerCapacityRegistry, WorkerAvailability


class AssignmentLifecycleState(str, Enum):
    ACTIVE = "active"
    RELEASED = "released"
    CANCELLED = "cancelled"
    HOLD = "hold"


class LifecycleBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    ASSIGNMENT_MISSING = "assignment_missing"
    ASSIGNMENT_INVALID = "assignment_invalid"
    DUPLICATE = "duplicate"
    INVALID_TRANSITION = "invalid_transition"
    CAPACITY_MISSING = "capacity_missing"
    CAPACITY_INVALID = "capacity_invalid"
    LIFECYCLE_TAMPERED = "lifecycle_tampered"
    TERMINAL_REASON_REQUIRED = "terminal_reason_required"


@dataclass(frozen=True)
class WorkerAssignmentLifecycle:
    lifecycle_id: str
    workspace_id: str
    assignment_id: str
    task_id: str
    worker_id: str
    state: AssignmentLifecycleState
    reason: str
    assignment_fingerprint: str
    capacity_active_tasks_before: int
    capacity_active_tasks_after: int
    coordination_only: bool = True
    authorization_granted: bool = False
    execution_requested: bool = False
    business_outcome_observed: bool = False
    lifecycle_fingerprint: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "lifecycle_id": self.lifecycle_id,
            "workspace_id": self.workspace_id,
            "assignment_id": self.assignment_id,
            "task_id": self.task_id,
            "worker_id": self.worker_id,
            "state": self.state.value,
            "reason": self.reason,
            "assignment_fingerprint": self.assignment_fingerprint,
            "capacity_active_tasks_before": self.capacity_active_tasks_before,
            "capacity_active_tasks_after": self.capacity_active_tasks_after,
            "coordination_only": self.coordination_only,
            "authorization_granted": self.authorization_granted,
            "execution_requested": self.execution_requested,
            "business_outcome_observed": self.business_outcome_observed,
        }

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class WorkerAssignmentLifecycleRegistry:
    VERSION = "v71.26"

    def __init__(self, workspace_id: str, admission: WorkerAssignmentAdmission,
                 capacity_registry: WorkerCapacityRegistry, db_path: str = ":memory:"):
        ws = str(workspace_id or "").strip()
        if not ws or admission.workspace_id != ws or capacity_registry.workspace_id != ws:
            raise ValueError(LifecycleBlock.TENANT_MISMATCH.value)
        self.workspace_id = ws
        self.admission = admission
        self.capacity_registry = capacity_registry
        self._db = sqlite3.connect(db_path)
        self._db.execute("""CREATE TABLE IF NOT EXISTS workforce_assignment_lifecycle (
            lifecycle_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            assignment_id TEXT NOT NULL,
            state TEXT NOT NULL,
            payload TEXT NOT NULL,
            fingerprint TEXT NOT NULL
        )""")
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_assignment_lifecycle_assignment ON workforce_assignment_lifecycle(assignment_id)")
        self._db.commit()

    def start(self, *, lifecycle_id: str, assignment_id: str) -> WorkerAssignmentLifecycle:
        return self._transition(lifecycle_id=lifecycle_id, assignment_id=assignment_id,
                                state=AssignmentLifecycleState.ACTIVE, reason="assignment_activated")

    def release(self, *, lifecycle_id: str, assignment_id: str, reason: str) -> WorkerAssignmentLifecycle:
        return self._transition(lifecycle_id=lifecycle_id, assignment_id=assignment_id,
                                state=AssignmentLifecycleState.RELEASED, reason=reason)

    def cancel(self, *, lifecycle_id: str, assignment_id: str, reason: str) -> WorkerAssignmentLifecycle:
        return self._transition(lifecycle_id=lifecycle_id, assignment_id=assignment_id,
                                state=AssignmentLifecycleState.CANCELLED, reason=reason)

    def _transition(self, *, lifecycle_id: str, assignment_id: str,
                    state: AssignmentLifecycleState, reason: str) -> WorkerAssignmentLifecycle:
        lid, aid, why = (str(x or "").strip() for x in (lifecycle_id, assignment_id, reason))
        if not lid or not aid:
            raise ValueError(LifecycleBlock.ASSIGNMENT_MISSING.value)
        if self.get(lid) is not None:
            raise ValueError(LifecycleBlock.DUPLICATE.value)
        if state in (AssignmentLifecycleState.RELEASED, AssignmentLifecycleState.CANCELLED) and not why:
            raise ValueError(LifecycleBlock.TERMINAL_REASON_REQUIRED.value)
        assignment = self.admission.get(aid)
        if assignment is None:
            raise ValueError(LifecycleBlock.ASSIGNMENT_MISSING.value)
        self.admission.assert_integrity(assignment)
        if assignment.workspace_id != self.workspace_id:
            raise ValueError(LifecycleBlock.TENANT_MISMATCH.value)
        prior = self.latest_for_assignment(aid)
        if state == AssignmentLifecycleState.ACTIVE and prior is not None:
            raise ValueError(LifecycleBlock.INVALID_TRANSITION.value)
        if state in (AssignmentLifecycleState.RELEASED, AssignmentLifecycleState.CANCELLED):
            if prior is None or prior.state != AssignmentLifecycleState.ACTIVE:
                raise ValueError(LifecycleBlock.INVALID_TRANSITION.value)

        cap = self.capacity_registry.get(assignment.worker_id)
        if cap is None:
            raise ValueError(LifecycleBlock.CAPACITY_MISSING.value)
        before = cap.active_tasks
        if state == AssignmentLifecycleState.ACTIVE:
            if before >= cap.max_concurrent_tasks:
                raise ValueError(LifecycleBlock.CAPACITY_INVALID.value)
            after = before + 1
        else:
            if before < 1:
                raise ValueError(LifecycleBlock.CAPACITY_INVALID.value)
            after = before - 1
        availability = WorkerAvailability.AVAILABLE if after < cap.max_concurrent_tasks else WorkerAvailability.BUSY
        self.capacity_registry.update_load(assignment.worker_id, active_tasks=after,
                                           load_score=float(after), availability=availability)
        record = WorkerAssignmentLifecycle(
            lid, self.workspace_id, assignment.assignment_id, assignment.task_id,
            assignment.worker_id, state, why, assignment.assignment_fingerprint,
            before, after, True, False, False, False, "")
        record = WorkerAssignmentLifecycle(**{**record.__dict__, "lifecycle_fingerprint": record.fingerprint()})
        self._db.execute("INSERT INTO workforce_assignment_lifecycle VALUES (?,?,?,?,?,?)",
                          (record.lifecycle_id, record.workspace_id, record.assignment_id,
                           record.state.value, json.dumps(record.canonical_payload(), sort_keys=True),
                           record.lifecycle_fingerprint))
        self._db.commit()
        return record

    def get(self, lifecycle_id: str) -> WorkerAssignmentLifecycle | None:
        row = self._db.execute("SELECT payload, fingerprint FROM workforce_assignment_lifecycle WHERE lifecycle_id=?", (str(lifecycle_id),)).fetchone()
        if not row:
            return None
        p = json.loads(row[0])
        p["state"] = AssignmentLifecycleState(p["state"])
        p["lifecycle_fingerprint"] = row[1]
        return WorkerAssignmentLifecycle(**p)

    def latest_for_assignment(self, assignment_id: str) -> WorkerAssignmentLifecycle | None:
        row = self._db.execute("SELECT lifecycle_id FROM workforce_assignment_lifecycle WHERE assignment_id=? ORDER BY rowid DESC LIMIT 1", (str(assignment_id),)).fetchone()
        return self.get(row[0]) if row else None

    def assert_integrity(self, record: WorkerAssignmentLifecycle) -> None:
        if record.workspace_id != self.workspace_id:
            raise ValueError(LifecycleBlock.TENANT_MISMATCH.value)
        if not record.coordination_only or record.authorization_granted or record.execution_requested or record.business_outcome_observed:
            raise ValueError(LifecycleBlock.LIFECYCLE_TAMPERED.value)
        if record.lifecycle_fingerprint != record.fingerprint():
            raise ValueError(LifecycleBlock.LIFECYCLE_TAMPERED.value)
        stored = self.get(record.lifecycle_id)
        if stored is None or stored.lifecycle_fingerprint != record.lifecycle_fingerprint:
            raise ValueError(LifecycleBlock.LIFECYCLE_TAMPERED.value)

    def context(self, assignment_id: str) -> dict[str, Any]:
        record = self.latest_for_assignment(assignment_id)
        if record is None:
            raise ValueError(LifecycleBlock.ASSIGNMENT_MISSING.value)
        self.assert_integrity(record)
        return {
            "assignment_id": record.assignment_id,
            "worker_id": record.worker_id,
            "task_id": record.task_id,
            "state": record.state.value,
            "coordination_only": True,
            "authorization_granted": False,
            "execution_requested": False,
            "business_outcome_observed": False,
        }

    def close(self) -> None:
        self._db.close()


__all__ = ["AssignmentLifecycleState", "LifecycleBlock", "WorkerAssignmentLifecycle", "WorkerAssignmentLifecycleRegistry"]
