"""V71.25 — Governed Worker Assignment Admission.

Binds a previously selected active worker to a task under a valid workforce
capacity reservation. Assignment is coordination state only: it does not grant
authorization, mutate worker runtime capacity, invoke the Action Kernel, or
execute a task.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any
import hashlib, json, sqlite3

from services.v71_21_worker_registry import WorkerRegistry, WorkerLifecycle
from services.v71_24_workforce_capacity_allocation import WorkforceCapacityAllocation, AllocationDisposition
from services.v71_9_worker_selection import WorkerSelectionRegistry


class AssignmentDisposition(str, Enum):
    ASSIGNED = "assigned"
    HOLD = "hold"
    REJECTED = "rejected"


class AssignmentBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    SELECTION_INVALID = "selection_invalid"
    SELECTION_MISSING = "selection_missing"
    NO_SELECTED_WORKER = "no_selected_worker"
    WORKER_INVALID = "worker_invalid"
    WORKER_NOT_ACTIVE = "worker_not_active"
    CAPACITY_INVALID = "capacity_invalid"
    CAPACITY_MISSING = "capacity_missing"
    CAPACITY_INSUFFICIENT = "capacity_insufficient"
    TASK_MISMATCH = "task_mismatch"
    DUPLICATE = "duplicate"
    ASSIGNMENT_TAMPERED = "assignment_tampered"


@dataclass(frozen=True)
class WorkerAssignment:
    assignment_id: str
    workspace_id: str
    task_id: str
    worker_id: str
    selection_id: str
    selection_fingerprint: str
    reservation_id: str
    reservation_fingerprint: str
    pool_id: str
    disposition: AssignmentDisposition
    reason_codes: tuple[str, ...]
    coordination_only: bool = True
    authorization_granted: bool = False
    execution_requested: bool = False
    assignment_fingerprint: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "assignment_id": self.assignment_id,
            "workspace_id": self.workspace_id,
            "task_id": self.task_id,
            "worker_id": self.worker_id,
            "selection_id": self.selection_id,
            "selection_fingerprint": self.selection_fingerprint,
            "reservation_id": self.reservation_id,
            "reservation_fingerprint": self.reservation_fingerprint,
            "pool_id": self.pool_id,
            "disposition": self.disposition.value,
            "reason_codes": list(self.reason_codes),
            "coordination_only": self.coordination_only,
            "authorization_granted": self.authorization_granted,
            "execution_requested": self.execution_requested,
        }

    def fingerprint(self) -> str:
        raw = json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()


class WorkerAssignmentAdmission:
    VERSION = "v71.25"

    def __init__(self, workspace_id: str, worker_registry: WorkerRegistry,
                 selection_registry: WorkerSelectionRegistry,
                 allocation: WorkforceCapacityAllocation,
                 db_path: str = ":memory:"):
        ws = str(workspace_id or "").strip()
        if not ws or any(x.workspace_id != ws for x in (worker_registry, selection_registry, allocation)):
            raise ValueError(AssignmentBlock.TENANT_MISMATCH.value)
        self.workspace_id = ws
        self.worker_registry = worker_registry
        self.selection_registry = selection_registry
        self.allocation = allocation
        self._db = sqlite3.connect(db_path)
        self._db.execute("""CREATE TABLE IF NOT EXISTS workforce_worker_assignments (
            assignment_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            payload TEXT NOT NULL,
            fingerprint TEXT NOT NULL
        )""")
        self._db.commit()

    def assign(self, *, assignment_id: str, task_id: str, selection_id: str,
               reservation_id: str) -> WorkerAssignment:
        aid, tid, sid, rid = (str(x or "").strip() for x in
                               (assignment_id, task_id, selection_id, reservation_id))
        if not aid or not tid or not sid or not rid:
            raise ValueError(AssignmentBlock.TASK_MISMATCH.value)
        if self.get(aid) is not None:
            raise ValueError(AssignmentBlock.DUPLICATE.value)

        selection = self.selection_registry.get(sid)
        if selection is None:
            raise ValueError(AssignmentBlock.SELECTION_MISSING.value)
        self.selection_registry.assert_integrity(selection)
        if selection.workspace_id != self.workspace_id:
            raise ValueError(AssignmentBlock.TENANT_MISMATCH.value)
        if selection.task_id != tid:
            raise ValueError(AssignmentBlock.TASK_MISMATCH.value)
        if not selection.advisory_only or selection.authority_granted or selection.execution_requested:
            raise ValueError(AssignmentBlock.SELECTION_INVALID.value)
        if not selection.selected_agent_id:
            raise ValueError(AssignmentBlock.NO_SELECTED_WORKER.value)

        reservation = self.allocation.get(rid)
        if reservation is None:
            raise ValueError(AssignmentBlock.CAPACITY_MISSING.value)
        self.allocation.assert_integrity(reservation)
        if reservation.workspace_id != self.workspace_id:
            raise ValueError(AssignmentBlock.TENANT_MISMATCH.value)
        if reservation.disposition != AllocationDisposition.RESERVE or reservation.reserved_slots < 1:
            raise ValueError(AssignmentBlock.CAPACITY_INSUFFICIENT.value)

        worker = self.worker_registry.get(selection.selected_agent_id)
        if worker is None:
            raise ValueError(AssignmentBlock.WORKER_INVALID.value)
        self.worker_registry.assert_integrity(worker)
        if worker.lifecycle != WorkerLifecycle.ACTIVE or not worker.eligible_for_selection:
            raise ValueError(AssignmentBlock.WORKER_NOT_ACTIVE.value)
        if worker.pool_id != reservation.pool_id:
            raise ValueError(AssignmentBlock.CAPACITY_INVALID.value)

        record = WorkerAssignment(
            aid, self.workspace_id, tid, worker.worker_id, selection.selection_id,
            selection.selection_fingerprint, reservation.reservation_id,
            reservation.reservation_fingerprint, reservation.pool_id,
            AssignmentDisposition.ASSIGNED, ("selected_worker_and_capacity_valid",),
            True, False, False, ""
        )
        record = WorkerAssignment(**{**record.__dict__, "assignment_fingerprint": record.fingerprint()})
        self._db.execute("INSERT INTO workforce_worker_assignments VALUES (?,?,?,?)",
                          (record.assignment_id, record.workspace_id,
                           json.dumps(record.canonical_payload(), sort_keys=True),
                           record.assignment_fingerprint))
        self._db.commit()
        return record

    def assign_many(self, assignments: tuple[dict[str, str], ...]) -> tuple[WorkerAssignment, ...]:
        """Atomically admit a batch of assignments through the canonical boundary.

        This is still coordination state only. It does not authorize, execute,
        consume provider capacity, or invoke the Action Kernel. Validation is
        performed for the entire batch before the first row is committed so a
        coordinated admission cannot leave a partial assignment set.
        """
        if not assignments:
            raise ValueError(AssignmentBlock.TASK_MISMATCH.value)
        normalized=[]
        seen=set()
        for item in assignments:
            aid, tid, sid, rid = (str(item.get(k, '') or '').strip() for k in ('assignment_id','task_id','selection_id','reservation_id'))
            if not aid or not tid or not sid or not rid:
                raise ValueError(AssignmentBlock.TASK_MISMATCH.value)
            if aid in seen or self.get(aid) is not None:
                raise ValueError(AssignmentBlock.DUPLICATE.value)
            seen.add(aid)
            selection=self.selection_registry.get(sid)
            if selection is None:
                raise ValueError(AssignmentBlock.SELECTION_MISSING.value)
            self.selection_registry.assert_integrity(selection)
            if selection.workspace_id != self.workspace_id:
                raise ValueError(AssignmentBlock.TENANT_MISMATCH.value)
            if selection.task_id != tid:
                raise ValueError(AssignmentBlock.TASK_MISMATCH.value)
            if not selection.advisory_only or selection.authority_granted or selection.execution_requested:
                raise ValueError(AssignmentBlock.SELECTION_INVALID.value)
            if not selection.selected_agent_id:
                raise ValueError(AssignmentBlock.NO_SELECTED_WORKER.value)
            reservation=self.allocation.get(rid)
            if reservation is None:
                raise ValueError(AssignmentBlock.CAPACITY_MISSING.value)
            self.allocation.assert_integrity(reservation)
            if reservation.workspace_id != self.workspace_id:
                raise ValueError(AssignmentBlock.TENANT_MISMATCH.value)
            if reservation.disposition != AllocationDisposition.RESERVE or reservation.reserved_slots < 1:
                raise ValueError(AssignmentBlock.CAPACITY_INSUFFICIENT.value)
            worker=self.worker_registry.get(selection.selected_agent_id)
            if worker is None:
                raise ValueError(AssignmentBlock.WORKER_INVALID.value)
            self.worker_registry.assert_integrity(worker)
            if worker.lifecycle != WorkerLifecycle.ACTIVE or not worker.eligible_for_selection:
                raise ValueError(AssignmentBlock.WORKER_NOT_ACTIVE.value)
            if worker.pool_id != reservation.pool_id:
                raise ValueError(AssignmentBlock.CAPACITY_INVALID.value)
            normalized.append(WorkerAssignment(
                aid,self.workspace_id,tid,worker.worker_id,selection.selection_id,
                selection.selection_fingerprint,reservation.reservation_id,
                reservation.reservation_fingerprint,reservation.pool_id,
                AssignmentDisposition.ASSIGNED,("selected_worker_and_capacity_valid","coordinated_batch_admission"),
                True,False,False,""))
        records=[]
        for record in normalized:
            record=WorkerAssignment(**{**record.__dict__, 'assignment_fingerprint': record.fingerprint()})
            records.append(record)
        try:
            self._db.execute('BEGIN')
            self._db.executemany(
                'INSERT INTO workforce_worker_assignments VALUES (?,?,?,?)',
                [(r.assignment_id,r.workspace_id,json.dumps(r.canonical_payload(),sort_keys=True),r.assignment_fingerprint) for r in records]
            )
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise
        return tuple(records)

    def get(self, assignment_id: str) -> WorkerAssignment | None:
        row = self._db.execute(
            "SELECT payload, fingerprint FROM workforce_worker_assignments WHERE assignment_id=?",
            (str(assignment_id),)).fetchone()
        if not row:
            return None
        p = json.loads(row[0])
        p["reason_codes"] = tuple(p.get("reason_codes", ()))
        p["disposition"] = AssignmentDisposition(p["disposition"])
        p["assignment_fingerprint"] = row[1]
        return WorkerAssignment(**p)

    def assert_integrity(self, assignment: WorkerAssignment) -> None:
        if assignment.workspace_id != self.workspace_id:
            raise ValueError(AssignmentBlock.TENANT_MISMATCH.value)
        if not assignment.coordination_only or assignment.authorization_granted or assignment.execution_requested:
            raise ValueError(AssignmentBlock.ASSIGNMENT_TAMPERED.value)
        if assignment.disposition != AssignmentDisposition.ASSIGNED:
            raise ValueError(AssignmentBlock.ASSIGNMENT_TAMPERED.value)
        if assignment.assignment_fingerprint != assignment.fingerprint():
            raise ValueError(AssignmentBlock.ASSIGNMENT_TAMPERED.value)
        stored = self.get(assignment.assignment_id)
        if stored is None or stored.assignment_fingerprint != assignment.assignment_fingerprint:
            raise ValueError(AssignmentBlock.ASSIGNMENT_TAMPERED.value)

    def context(self, assignment_id: str) -> dict[str, Any]:
        assignment = self.get(assignment_id)
        if assignment is None:
            raise ValueError(AssignmentBlock.SELECTION_MISSING.value)
        self.assert_integrity(assignment)
        return {
            "assignment_id": assignment.assignment_id,
            "task_id": assignment.task_id,
            "worker_id": assignment.worker_id,
            "pool_id": assignment.pool_id,
            "coordination_only": True,
            "authorization_granted": False,
            "execution_requested": False,
        }

    def close(self) -> None:
        self._db.close()


__all__ = ["AssignmentDisposition", "AssignmentBlock", "WorkerAssignment", "WorkerAssignmentAdmission"]
