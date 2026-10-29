"""Phase 7.30 — Coordinated Worker Assignment Lifecycle & Synchronization.

Synchronizes an admitted multi-worker assignment set over the existing
single-assignment lifecycle boundary. This layer coordinates lifecycle state;
it never authorizes, executes, calls providers, or invokes the Action Kernel.

Invariant:
    coordinated lifecycle != authorization != execution != business outcome
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any
import hashlib, json, sqlite3

from services.v71_40_coordinated_worker_assignment_admission import CoordinatedWorkerAssignmentAdmission
from services.v71_26_worker_assignment_lifecycle import (
    AssignmentLifecycleState, WorkerAssignmentLifecycleRegistry,
)


class CoordinatedTaskState(str, Enum):
    ASSIGNED = "assigned"
    ACTIVE = "active"
    RELEASED = "released"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


class CoordinatedLifecycleState(str, Enum):
    ASSIGNED = "assigned"
    ACTIVE = "active"
    PARTIAL = "partial"
    RELEASED = "released"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


class CoordinatedLifecycleBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    ADMISSION_MISSING = "admission_missing"
    ADMISSION_INVALID = "admission_invalid"
    DUPLICATE = "duplicate"
    TASK_MISSING = "task_missing"
    INVALID_TRANSITION = "invalid_transition"
    DEPENDENCY_NOT_RELEASED = "dependency_not_released"
    DEPENDENCY_CANCELLED = "dependency_cancelled"
    TERMINAL = "terminal"
    LIFECYCLE_TAMPERED = "lifecycle_tampered"
    AUTHORITY_VIOLATION = "authority_violation"


@dataclass(frozen=True)
class CoordinatedTaskLifecycle:
    task_id: str
    assignment_id: str
    worker_id: str
    depends_on: tuple[str, ...]
    state: CoordinatedTaskState
    lifecycle_id: str = ""

    def payload(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id, "assignment_id": self.assignment_id,
            "worker_id": self.worker_id, "depends_on": list(self.depends_on),
            "state": self.state.value, "lifecycle_id": self.lifecycle_id,
        }


@dataclass(frozen=True)
class CoordinatedAssignmentLifecycle:
    coordination_lifecycle_id: str
    workspace_id: str
    admission_id: str
    plan_id: str
    tasks: tuple[CoordinatedTaskLifecycle, ...]
    state: CoordinatedLifecycleState
    coordination_only: bool = True
    authorization_granted: bool = False
    execution_requested: bool = False
    provider_called: bool = False
    action_kernel_invoked: bool = False
    lifecycle_fingerprint: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "coordination_lifecycle_id": self.coordination_lifecycle_id,
            "workspace_id": self.workspace_id, "admission_id": self.admission_id,
            "plan_id": self.plan_id, "tasks": [t.payload() for t in self.tasks],
            "state": self.state.value, "coordination_only": self.coordination_only,
            "authorization_granted": self.authorization_granted,
            "execution_requested": self.execution_requested,
            "provider_called": self.provider_called,
            "action_kernel_invoked": self.action_kernel_invoked,
        }

    def fingerprint(self) -> str:
        raw = json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()


class CoordinatedWorkerAssignmentLifecycle:
    VERSION = "phase7.30"

    def __init__(self, workspace_id: str,
                 admission: CoordinatedWorkerAssignmentAdmission,
                 lifecycle: WorkerAssignmentLifecycleRegistry,
                 db_path: str = ":memory:"):
        ws = str(workspace_id or "").strip()
        if not ws or admission.workspace_id != ws or lifecycle.workspace_id != ws:
            raise ValueError(CoordinatedLifecycleBlock.TENANT_MISMATCH.value)
        self.workspace_id = ws
        self.admission = admission
        self.lifecycle = lifecycle
        self._db = sqlite3.connect(db_path)
        self._db.execute("""CREATE TABLE IF NOT EXISTS coordinated_assignment_lifecycles (
            coordination_lifecycle_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            payload TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            UNIQUE(workspace_id, coordination_lifecycle_id)
        )""")
        self._db.commit()

    def create(self, *, coordination_lifecycle_id: str, admission_id: str) -> CoordinatedAssignmentLifecycle:
        lid = str(coordination_lifecycle_id or "").strip()
        if not lid:
            raise ValueError(CoordinatedLifecycleBlock.ADMISSION_MISSING.value)
        if self.get(lid) is not None:
            raise ValueError(CoordinatedLifecycleBlock.DUPLICATE.value)
        admission = self.admission.get(admission_id)
        if admission is None:
            raise ValueError(CoordinatedLifecycleBlock.ADMISSION_MISSING.value)
        self.admission.assert_integrity(admission)
        assignments = self.admission.assignments(admission_id)
        plan = self.admission.planner.get(admission.plan_id)
        if plan is None:
            raise ValueError(CoordinatedLifecycleBlock.ADMISSION_INVALID.value)
        self.admission.planner.assert_integrity(plan)
        amap = {a.task_id: a for a in assignments}
        tasks = []
        for spec in sorted(plan.tasks, key=lambda x: x.task_id):
            assignment = amap.get(spec.task_id)
            if assignment is None:
                raise ValueError(CoordinatedLifecycleBlock.ADMISSION_INVALID.value)
            tasks.append(CoordinatedTaskLifecycle(
                spec.task_id, assignment.assignment_id, assignment.worker_id,
                tuple(spec.depends_on), CoordinatedTaskState.ASSIGNED, ""))
        if not tasks:
            raise ValueError(CoordinatedLifecycleBlock.ADMISSION_INVALID.value)
        record = CoordinatedAssignmentLifecycle(
            lid, self.workspace_id, admission.admission_id, admission.plan_id,
            tuple(tasks), CoordinatedLifecycleState.ASSIGNED)
        record = CoordinatedAssignmentLifecycle(**{**record.__dict__, "lifecycle_fingerprint": record.fingerprint()})
        self._persist(record)
        return record

    def start_task(self, *, coordination_lifecycle_id: str, task_id: str) -> CoordinatedAssignmentLifecycle:
        record = self._require(coordination_lifecycle_id)
        task = self._task(record, task_id)
        if task.state is not CoordinatedTaskState.ASSIGNED:
            raise ValueError(CoordinatedLifecycleBlock.INVALID_TRANSITION.value)
        states = {t.task_id: t.state for t in record.tasks}
        if any(states.get(dep) is CoordinatedTaskState.CANCELLED for dep in task.depends_on):
            raise ValueError(CoordinatedLifecycleBlock.DEPENDENCY_CANCELLED.value)
        if any(states.get(dep) is not CoordinatedTaskState.RELEASED for dep in task.depends_on):
            raise ValueError(CoordinatedLifecycleBlock.DEPENDENCY_NOT_RELEASED.value)
        new_lifecycle_id = f"{record.coordination_lifecycle_id}:{task.task_id}"
        self.lifecycle.start(lifecycle_id=new_lifecycle_id, assignment_id=task.assignment_id)
        updated = self._replace_task(record, task.task_id,
            CoordinatedTaskLifecycle(task.task_id, task.assignment_id, task.worker_id,
                                     task.depends_on, CoordinatedTaskState.ACTIVE, new_lifecycle_id))
        updated = self._with_state(updated)
        return self._persist(updated)

    def release_task(self, *, coordination_lifecycle_id: str, task_id: str, reason: str) -> CoordinatedAssignmentLifecycle:
        record = self._require(coordination_lifecycle_id)
        task = self._task(record, task_id)
        if task.state is not CoordinatedTaskState.ACTIVE:
            raise ValueError(CoordinatedLifecycleBlock.INVALID_TRANSITION.value)
        terminal_lifecycle_id = f"{task.lifecycle_id}:release"
        self.lifecycle.release(lifecycle_id=terminal_lifecycle_id, assignment_id=task.assignment_id, reason=reason)
        updated = self._replace_task(record, task.task_id,
            CoordinatedTaskLifecycle(task.task_id, task.assignment_id, task.worker_id,
                                     task.depends_on, CoordinatedTaskState.RELEASED, task.lifecycle_id))
        updated = self._with_state(updated)
        return self._persist(updated)

    def cancel_task(self, *, coordination_lifecycle_id: str, task_id: str, reason: str) -> CoordinatedAssignmentLifecycle:
        if not str(reason or "").strip():
            raise ValueError(CoordinatedLifecycleBlock.INVALID_TRANSITION.value)
        record = self._require(coordination_lifecycle_id)
        task = self._task(record, task_id)
        if task.state in {CoordinatedTaskState.RELEASED, CoordinatedTaskState.CANCELLED}:
            raise ValueError(CoordinatedLifecycleBlock.TERMINAL.value)
        mutable = {t.task_id: t for t in record.tasks}
        # Cancel the selected task and every downstream task that can no longer
        # satisfy its dependency chain. Active tasks release logical capacity
        # exactly once through the canonical single-assignment lifecycle.
        queue = [task.task_id]
        while queue:
            current_id = queue.pop(0)
            current = mutable[current_id]
            if current.state in {CoordinatedTaskState.RELEASED, CoordinatedTaskState.CANCELLED}:
                continue
            if current.state is CoordinatedTaskState.ACTIVE:
                terminal_lifecycle_id = f"{current.lifecycle_id}:cancel"
                self.lifecycle.cancel(lifecycle_id=terminal_lifecycle_id,
                                      assignment_id=current.assignment_id, reason=reason)
            mutable[current_id] = CoordinatedTaskLifecycle(
                current.task_id, current.assignment_id, current.worker_id,
                current.depends_on, CoordinatedTaskState.CANCELLED,
                current.lifecycle_id)
            for candidate in tuple(mutable.values()):
                if candidate.state not in {CoordinatedTaskState.RELEASED, CoordinatedTaskState.CANCELLED} and current_id in candidate.depends_on:
                    queue.append(candidate.task_id)
        updated = CoordinatedAssignmentLifecycle(
            record.coordination_lifecycle_id, record.workspace_id, record.admission_id,
            record.plan_id, tuple(mutable[t.task_id] for t in record.tasks),
            CoordinatedLifecycleState.ASSIGNED, record.coordination_only,
            record.authorization_granted, record.execution_requested,
            record.provider_called, record.action_kernel_invoked, "")
        updated = self._with_state(updated)
        return self._persist(updated)

    def mark_task_terminal_from_canonical_closure(self, *, coordination_lifecycle_id: str, task_id: str, terminal_state: CoordinatedTaskState) -> CoordinatedAssignmentLifecycle:
        """Project an already-committed canonical single-assignment terminal event.

        This method never touches capacity or creates a second single-assignment
        lifecycle event. The canonical closure registry must have already released
        or cancelled the assignment.
        """
        if terminal_state not in {CoordinatedTaskState.RELEASED, CoordinatedTaskState.CANCELLED}:
            raise ValueError(CoordinatedLifecycleBlock.INVALID_TRANSITION.value)
        record = self._require(coordination_lifecycle_id)
        task = self._task(record, task_id)
        if task.state is not CoordinatedTaskState.ACTIVE:
            if task.state is terminal_state:
                return record
            raise ValueError(CoordinatedLifecycleBlock.INVALID_TRANSITION.value)
        latest = self.lifecycle.latest_for_assignment(task.assignment_id)
        if latest is None:
            raise ValueError(CoordinatedLifecycleBlock.INVALID_TRANSITION.value)
        expected = AssignmentLifecycleState.RELEASED if terminal_state is CoordinatedTaskState.RELEASED else AssignmentLifecycleState.CANCELLED
        if latest.state is not expected:
            raise ValueError(CoordinatedLifecycleBlock.INVALID_TRANSITION.value)
        updated = self._replace_task(record, task.task_id, CoordinatedTaskLifecycle(
            task.task_id, task.assignment_id, task.worker_id, task.depends_on, terminal_state, task.lifecycle_id))
        return self._persist(self._with_state(updated))

    def release_all_active(self, *, coordination_lifecycle_id: str, reason: str) -> CoordinatedAssignmentLifecycle:
        record = self._require(coordination_lifecycle_id)
        current = record
        for task in tuple(record.tasks):
            latest = self._task(current, task.task_id)
            if latest.state is CoordinatedTaskState.ACTIVE:
                current = self.release_task(coordination_lifecycle_id=current.coordination_lifecycle_id,
                                            task_id=task.task_id, reason=reason)
        return self._require(coordination_lifecycle_id)

    def cancel_all(self, *, coordination_lifecycle_id: str, reason: str) -> CoordinatedAssignmentLifecycle:
        record = self._require(coordination_lifecycle_id)
        for task in tuple(record.tasks):
            latest = self._require(coordination_lifecycle_id)
            state = self._task(latest, task.task_id).state
            if state not in {CoordinatedTaskState.RELEASED, CoordinatedTaskState.CANCELLED}:
                self.cancel_task(coordination_lifecycle_id=coordination_lifecycle_id,
                                 task_id=task.task_id, reason=reason)
        return self._require(coordination_lifecycle_id)

    def get(self, coordination_lifecycle_id: str) -> CoordinatedAssignmentLifecycle | None:
        row = self._db.execute(
            "SELECT payload,fingerprint FROM coordinated_assignment_lifecycles WHERE coordination_lifecycle_id=? AND workspace_id=?",
            (str(coordination_lifecycle_id), self.workspace_id)).fetchone()
        if not row:
            return None
        p = json.loads(row[0])
        p["state"] = CoordinatedLifecycleState(p["state"])
        p["tasks"] = tuple(CoordinatedTaskLifecycle(
            t["task_id"], t["assignment_id"], t["worker_id"],
            tuple(t.get("depends_on", ())), CoordinatedTaskState(t["state"]), t.get("lifecycle_id", ""))
            for t in p["tasks"])
        p["lifecycle_fingerprint"] = row[1]
        return CoordinatedAssignmentLifecycle(**p)

    def assert_integrity(self, record: CoordinatedAssignmentLifecycle) -> None:
        if record.workspace_id != self.workspace_id:
            raise ValueError(CoordinatedLifecycleBlock.TENANT_MISMATCH.value)
        if not record.coordination_only or record.authorization_granted or record.execution_requested or record.provider_called or record.action_kernel_invoked:
            raise ValueError(CoordinatedLifecycleBlock.AUTHORITY_VIOLATION.value)
        if record.lifecycle_fingerprint != record.fingerprint():
            raise ValueError(CoordinatedLifecycleBlock.LIFECYCLE_TAMPERED.value)
        stored = self.get(record.coordination_lifecycle_id)
        if stored is None or stored.lifecycle_fingerprint != record.lifecycle_fingerprint:
            raise ValueError(CoordinatedLifecycleBlock.LIFECYCLE_TAMPERED.value)
        admission = self.admission.get(record.admission_id)
        if admission is None:
            raise ValueError(CoordinatedLifecycleBlock.ADMISSION_INVALID.value)
        self.admission.assert_integrity(admission)
        if admission.plan_id != record.plan_id:
            raise ValueError(CoordinatedLifecycleBlock.ADMISSION_INVALID.value)
        assignments = {a.assignment_id: a for a in self.admission.assignments(admission.admission_id)}
        if len(record.tasks) != len(assignments):
            raise ValueError(CoordinatedLifecycleBlock.LIFECYCLE_TAMPERED.value)
        for task in record.tasks:
            assignment = assignments.get(task.assignment_id)
            if assignment is None or assignment.task_id != task.task_id or assignment.worker_id != task.worker_id:
                raise ValueError(CoordinatedLifecycleBlock.LIFECYCLE_TAMPERED.value)
            if task.state is CoordinatedTaskState.ACTIVE:
                if not task.lifecycle_id or self.lifecycle.get(task.lifecycle_id) is None:
                    raise ValueError(CoordinatedLifecycleBlock.LIFECYCLE_TAMPERED.value)
                self.lifecycle.assert_integrity(self.lifecycle.get(task.lifecycle_id))

    def context(self, coordination_lifecycle_id: str) -> dict[str, Any]:
        record = self._require(coordination_lifecycle_id)
        counts = {state.value: sum(t.state is state for t in record.tasks) for state in CoordinatedTaskState}
        return {"coordination_lifecycle_id": record.coordination_lifecycle_id,
                "admission_id": record.admission_id, "plan_id": record.plan_id,
                "state": record.state.value, "task_counts": counts,
                "coordination_only": True, "authorization_granted": False,
                "execution_requested": False, "provider_called": False,
                "action_kernel_invoked": False}

    def _task(self, record: CoordinatedAssignmentLifecycle, task_id: str) -> CoordinatedTaskLifecycle:
        for task in record.tasks:
            if task.task_id == str(task_id):
                return task
        raise ValueError(CoordinatedLifecycleBlock.TASK_MISSING.value)

    def _replace_task(self, record: CoordinatedAssignmentLifecycle, task_id: str,
                      replacement: CoordinatedTaskLifecycle) -> CoordinatedAssignmentLifecycle:
        tasks = tuple(replacement if t.task_id == task_id else t for t in record.tasks)
        return CoordinatedAssignmentLifecycle(record.coordination_lifecycle_id, record.workspace_id,
            record.admission_id, record.plan_id, tasks, record.state,
            record.coordination_only, record.authorization_granted,
            record.execution_requested, record.provider_called,
            record.action_kernel_invoked, "")

    @staticmethod
    def _derive_state(tasks: tuple[CoordinatedTaskLifecycle, ...]) -> CoordinatedLifecycleState:
        states = [t.state for t in tasks]
        if all(s is CoordinatedTaskState.RELEASED for s in states):
            return CoordinatedLifecycleState.RELEASED
        if all(s is CoordinatedTaskState.CANCELLED for s in states):
            return CoordinatedLifecycleState.CANCELLED
        if any(s is CoordinatedTaskState.BLOCKED for s in states):
            return CoordinatedLifecycleState.BLOCKED
        if all(s is CoordinatedTaskState.ASSIGNED for s in states):
            return CoordinatedLifecycleState.ASSIGNED
        if all(s in {CoordinatedTaskState.RELEASED, CoordinatedTaskState.CANCELLED} for s in states):
            return CoordinatedLifecycleState.CANCELLED
        if any(s is CoordinatedTaskState.ACTIVE for s in states):
            return CoordinatedLifecycleState.ACTIVE
        return CoordinatedLifecycleState.PARTIAL

    def _with_state(self, record: CoordinatedAssignmentLifecycle) -> CoordinatedAssignmentLifecycle:
        state = self._derive_state(record.tasks)
        return CoordinatedAssignmentLifecycle(record.coordination_lifecycle_id, record.workspace_id,
            record.admission_id, record.plan_id, record.tasks, state,
            record.coordination_only, record.authorization_granted,
            record.execution_requested, record.provider_called,
            record.action_kernel_invoked, "")

    def _require(self, coordination_lifecycle_id: str) -> CoordinatedAssignmentLifecycle:
        record = self.get(coordination_lifecycle_id)
        if record is None:
            raise ValueError(CoordinatedLifecycleBlock.ADMISSION_MISSING.value)
        self.assert_integrity(record)
        return record

    def _persist(self, record: CoordinatedAssignmentLifecycle) -> CoordinatedAssignmentLifecycle:
        record = CoordinatedAssignmentLifecycle(**{**record.__dict__, "lifecycle_fingerprint": record.fingerprint()})
        self._db.execute(
            "INSERT OR REPLACE INTO coordinated_assignment_lifecycles VALUES (?,?,?,?)",
            (record.coordination_lifecycle_id, record.workspace_id,
             json.dumps(record.canonical_payload(), sort_keys=True), record.lifecycle_fingerprint))
        self._db.commit()
        # Mutating callers reload from the durable record; returning the original
        # object without its persisted fingerprint would create false tamper reads.
        return record

    def close(self) -> None:
        self._db.close()


__all__ = [
    "CoordinatedTaskState", "CoordinatedLifecycleState",
    "CoordinatedLifecycleBlock", "CoordinatedTaskLifecycle",
    "CoordinatedAssignmentLifecycle", "CoordinatedWorkerAssignmentLifecycle",
]
