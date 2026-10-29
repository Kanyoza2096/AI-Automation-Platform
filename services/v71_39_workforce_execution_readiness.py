"""Phase 7.28 — Workforce Coordination Execution Readiness & Pre-Admission.

Validates whether a resource-feasible multi-worker plan satisfies every known
coordination prerequisite before it enters the existing assignment-admission
boundary. This layer never creates assignments, consumes capacity, authorizes,
executes, calls providers, or invokes the Action Kernel.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping
import hashlib, json, sqlite3

from services.v71_37_workforce_multi_worker_planning import MultiWorkerPlan, WorkforceMultiWorkerPlanner
from services.v71_38_workforce_resource_cost_coordination import ResourceCostPlan, WorkforceResourceCostCoordinator
from services.v71_21_worker_registry import WorkerRegistry, WorkerLifecycle, WorkerHealth
from services.v71_24_workforce_capacity_allocation import WorkforceCapacityAllocation, AllocationDisposition


class ReadinessBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    PLAN_MISSING = "plan_missing"
    PLAN_TAMPERED = "plan_tampered"
    COST_PLAN_MISSING = "cost_plan_missing"
    COST_PLAN_INVALID = "cost_plan_invalid"
    COST_PLAN_INFEASIBLE = "cost_plan_infeasible"
    RESERVATION_MISSING = "reservation_missing"
    RESERVATION_INVALID = "reservation_invalid"
    RESERVATION_INSUFFICIENT = "reservation_insufficient"
    WORKER_MISSING = "worker_missing"
    WORKER_NOT_ACTIVE = "worker_not_active"
    WORKER_UNAVAILABLE = "worker_unavailable"
    TASK_SELECTION_INVALID = "task_selection_invalid"
    QUALIFICATION_INVALID = "qualification_invalid"
    DUPLICATE = "duplicate"
    READINESS_TAMPERED = "readiness_tampered"
    AUTHORITY_VIOLATION = "authority_violation"


class ReadinessDisposition(str, Enum):
    READY = "ready"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class TaskReadiness:
    task_id: str
    worker_id: str
    pool_id: str
    selection_id: str
    selection_fingerprint: str
    qualification_id: str
    reservation_id: str
    ready: bool
    reason_codes: tuple[str, ...] = ()

    def payload(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id, "worker_id": self.worker_id, "pool_id": self.pool_id,
            "selection_id": self.selection_id, "selection_fingerprint": self.selection_fingerprint,
            "qualification_id": self.qualification_id, "reservation_id": self.reservation_id,
            "ready": self.ready, "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class WorkforceExecutionReadiness:
    readiness_id: str
    workspace_id: str
    plan_id: str
    cost_plan_id: str
    disposition: ReadinessDisposition
    tasks: tuple[TaskReadiness, ...]
    advisory_only: bool = True
    authority_granted: bool = False
    execution_requested: bool = False
    provider_called: bool = False
    action_kernel_invoked: bool = False
    assignments_created: bool = False
    readiness_fingerprint: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "readiness_id": self.readiness_id, "workspace_id": self.workspace_id,
            "plan_id": self.plan_id, "cost_plan_id": self.cost_plan_id,
            "disposition": self.disposition.value, "tasks": [t.payload() for t in self.tasks],
            "advisory_only": self.advisory_only, "authority_granted": self.authority_granted,
            "execution_requested": self.execution_requested, "provider_called": self.provider_called,
            "action_kernel_invoked": self.action_kernel_invoked, "assignments_created": self.assignments_created,
        }

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


class WorkforceExecutionReadinessRegistry:
    VERSION = "phase7.28"

    def __init__(self, workspace_id: str, planner: WorkforceMultiWorkerPlanner,
                 cost_coordinator: WorkforceResourceCostCoordinator,
                 worker_registry: WorkerRegistry, allocation: WorkforceCapacityAllocation,
                 db_path: str = ":memory:"):
        ws = str(workspace_id or "").strip()
        if not ws or any(x.workspace_id != ws for x in (planner, cost_coordinator, worker_registry, allocation)):
            raise ValueError(ReadinessBlock.TENANT_MISMATCH.value)
        self.workspace_id = ws
        self.planner = planner
        self.cost_coordinator = cost_coordinator
        self.worker_registry = worker_registry
        self.allocation = allocation
        self._db = sqlite3.connect(db_path)
        self._db.execute("""CREATE TABLE IF NOT EXISTS workforce_execution_readiness(
            readiness_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
            payload TEXT NOT NULL, fingerprint TEXT NOT NULL,
            UNIQUE(workspace_id, readiness_id))""")
        self._db.commit()

    def evaluate(self, *, readiness_id: str, plan: MultiWorkerPlan,
                 cost_plan: ResourceCostPlan, reservation_ids: Mapping[str, str]) -> WorkforceExecutionReadiness:
        rid = str(readiness_id or "").strip()
        if not rid:
            raise ValueError(ReadinessBlock.PLAN_MISSING.value)
        if self.get(rid) is not None:
            raise ValueError(ReadinessBlock.DUPLICATE.value)
        if plan.workspace_id != self.workspace_id or cost_plan.workspace_id != self.workspace_id:
            raise ValueError(ReadinessBlock.TENANT_MISMATCH.value)
        self.planner.assert_integrity(plan)
        self.cost_coordinator.assert_integrity(cost_plan)
        if cost_plan.base_plan_id != plan.plan_id:
            raise ValueError(ReadinessBlock.COST_PLAN_INVALID.value)
        if not cost_plan.feasible:
            raise ValueError(ReadinessBlock.COST_PLAN_INFEASIBLE.value)

        by_task = {str(k): str(v) for k, v in dict(reservation_ids or {}).items()}
        if set(by_task) != {t.task_id for t in plan.tasks}:
            raise ValueError(ReadinessBlock.RESERVATION_MISSING.value)

        task_records: list[TaskReadiness] = []
        # A reservation is a logical capacity reservation. It may back multiple
        # tasks, but must cover the number of planned tasks in its pool.
        reservation_cache = {}
        for task in plan.tasks:
            reservation = self.allocation.get(by_task[task.task_id])
            if reservation is None:
                raise ValueError(ReadinessBlock.RESERVATION_MISSING.value)
            self.allocation.assert_integrity(reservation)
            if reservation.workspace_id != self.workspace_id:
                raise ValueError(ReadinessBlock.TENANT_MISMATCH.value)
            if reservation.disposition != AllocationDisposition.RESERVE or reservation.reserved_slots < 1:
                raise ValueError(ReadinessBlock.RESERVATION_INVALID.value)
            reservation_cache[reservation.reservation_id] = reservation

            worker = self.worker_registry.get(task.worker_id)
            if worker is None:
                raise ValueError(ReadinessBlock.WORKER_MISSING.value)
            self.worker_registry.assert_integrity(worker)
            if worker.lifecycle != WorkerLifecycle.ACTIVE:
                raise ValueError(ReadinessBlock.WORKER_NOT_ACTIVE.value)
            if worker.health not in {WorkerHealth.HEALTHY, WorkerHealth.DEGRADED} or not worker.eligible_for_selection:
                raise ValueError(ReadinessBlock.WORKER_UNAVAILABLE.value)
            if worker.pool_id != reservation.pool_id:
                raise ValueError(ReadinessBlock.RESERVATION_INVALID.value)

            selection = self.planner.selector.get(task.selection_id)
            if selection is None or selection.selected_agent_id != task.worker_id:
                raise ValueError(ReadinessBlock.TASK_SELECTION_INVALID.value)
            self.planner.selector.assert_integrity(selection)
            if selection.selection_fingerprint != task.selection_fingerprint:
                raise ValueError(ReadinessBlock.TASK_SELECTION_INVALID.value)
            q = self.planner.qualification.get_for_worker(task.worker_id, task.capability)
            if q is None or q.qualification_id != task.qualification_id or not self.planner.qualification.is_currently_qualified(task.worker_id, task.capability):
                raise ValueError(ReadinessBlock.QUALIFICATION_INVALID.value)
            self.planner.qualification.assert_integrity(q)
            task_records.append(TaskReadiness(task.task_id, task.worker_id, worker.pool_id,
                                              task.selection_id, task.selection_fingerprint,
                                              task.qualification_id, reservation.reservation_id, True,
                                              ("plan_cost_capacity_worker_qualification_valid",)))

        # Ensure each reservation has enough logical slots for all tasks mapped to it.
        counts: dict[str, int] = {}
        for t in task_records:
            counts[t.reservation_id] = counts.get(t.reservation_id, 0) + 1
        for reservation_id, count in counts.items():
            if count > reservation_cache[reservation_id].reserved_slots:
                raise ValueError(ReadinessBlock.RESERVATION_INSUFFICIENT.value)

        result = WorkforceExecutionReadiness(
            rid, self.workspace_id, plan.plan_id, cost_plan.plan_id,
            ReadinessDisposition.READY, tuple(sorted(task_records, key=lambda x: x.task_id)))
        result = WorkforceExecutionReadiness(**{**result.__dict__, "readiness_fingerprint": result.fingerprint()})
        self._db.execute("INSERT INTO workforce_execution_readiness VALUES (?,?,?,?)",
                         (result.readiness_id, result.workspace_id,
                          json.dumps(result.canonical_payload(), sort_keys=True), result.readiness_fingerprint))
        self._db.commit()
        return result

    def get(self, readiness_id: str) -> WorkforceExecutionReadiness | None:
        row = self._db.execute("SELECT payload,fingerprint FROM workforce_execution_readiness WHERE readiness_id=? AND workspace_id=?",
                               (str(readiness_id), self.workspace_id)).fetchone()
        if not row:
            return None
        p = json.loads(row[0])
        p["disposition"] = ReadinessDisposition(p["disposition"])
        p["tasks"] = tuple(TaskReadiness(**{**t, "reason_codes": tuple(t.get("reason_codes", ()))}) for t in p["tasks"])
        p["readiness_fingerprint"] = row[1]
        return WorkforceExecutionReadiness(**p)

    def assert_integrity(self, readiness: WorkforceExecutionReadiness) -> None:
        if readiness.workspace_id != self.workspace_id:
            raise ValueError(ReadinessBlock.TENANT_MISMATCH.value)
        if readiness.readiness_fingerprint != readiness.fingerprint():
            raise ValueError(ReadinessBlock.READINESS_TAMPERED.value)
        stored = self.get(readiness.readiness_id)
        if stored is None or stored.readiness_fingerprint != readiness.readiness_fingerprint:
            raise ValueError(ReadinessBlock.READINESS_TAMPERED.value)
        if (not readiness.advisory_only or readiness.authority_granted or readiness.execution_requested or
            readiness.provider_called or readiness.action_kernel_invoked or readiness.assignments_created):
            raise ValueError(ReadinessBlock.AUTHORITY_VIOLATION.value)
        plan = self.planner.get(readiness.plan_id)
        if plan is None:
            raise ValueError(ReadinessBlock.PLAN_MISSING.value)
        self.planner.assert_integrity(plan)
        cost = self.cost_coordinator.get(readiness.cost_plan_id)
        if cost is None:
            raise ValueError(ReadinessBlock.COST_PLAN_MISSING.value)
        self.cost_coordinator.assert_integrity(cost)
        if cost.base_plan_id != plan.plan_id or not cost.feasible:
            raise ValueError(ReadinessBlock.COST_PLAN_INVALID.value)

    def context(self, readiness_id: str) -> Mapping[str, Any]:
        record = self.get(readiness_id)
        if record is None:
            raise ValueError(ReadinessBlock.PLAN_MISSING.value)
        self.assert_integrity(record)
        return {"readiness_id": record.readiness_id, "plan_id": record.plan_id,
                "cost_plan_id": record.cost_plan_id, "disposition": record.disposition.value,
                "task_count": len(record.tasks), "advisory_only": True,
                "authority": "none", "assignments_created": False,
                "execution_requested": False}

    def close(self) -> None:
        self._db.close()


__all__ = ["ReadinessBlock", "ReadinessDisposition", "TaskReadiness",
           "WorkforceExecutionReadiness", "WorkforceExecutionReadinessRegistry"]
