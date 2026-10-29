"""IPC-18 — objective-bound multi-worker coordination.

Coordination is durable planning state, never execution authority. Workers may
coordinate task ownership and dependencies, but every worker still enters the
existing governed workforce/Action Kernel path independently.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping
import hashlib, json

SCHEMA_VERSION = "1.0"

class TaskState(str, Enum):
    PROPOSED = "proposed"
    READY = "ready"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"

TERMINAL = {TaskState.COMPLETED.value, TaskState.CANCELLED.value}

@dataclass(frozen=True)
class WorkforceTask:
    task_id: str
    worker_id: str
    capability: str
    title: str
    depends_on: tuple[str, ...] = ()
    state: TaskState = TaskState.PROPOSED
    requested_scope: Mapping[str, Any] = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        return {"task_id": self.task_id, "worker_id": self.worker_id,
                "capability": self.capability, "title": self.title,
                "depends_on": list(self.depends_on), "state": self.state.value,
                "requested_scope": dict(self.requested_scope)}

@dataclass(frozen=True)
class WorkforceCoordinationPlan:
    coordination_id: str
    workspace_id: str
    objective_id: str
    cycle_id: str
    coordinator_worker_id: str
    tasks: tuple[WorkforceTask, ...]
    version: str = "1"
    fingerprint: str = ""

    def payload(self) -> dict[str, Any]:
        return {"coordination_id": self.coordination_id, "workspace_id": self.workspace_id,
                "objective_id": self.objective_id, "cycle_id": self.cycle_id,
                "coordinator_worker_id": self.coordinator_worker_id,
                "tasks": [t.payload() for t in self.tasks], "version": self.version}

    def compute_fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.payload(), sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _validate(plan: WorkforceCoordinationPlan) -> None:
    if plan.fingerprint != plan.compute_fingerprint():
        raise ValueError("coordination fingerprint mismatch")
    for key in (plan.workspace_id, plan.objective_id, plan.cycle_id, plan.coordinator_worker_id):
        if not str(key).strip():
            raise ValueError("coordination lineage is required")
    ids = [t.task_id for t in plan.tasks]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("coordination tasks must be unique")
    known = set(ids)
    graph = {t.task_id: set(t.depends_on) for t in plan.tasks}
    if any(dep not in known or dep == tid for tid, deps in graph.items() for dep in deps):
        raise ValueError("invalid task dependency")
    visiting: set[str] = set(); visited: set[str] = set()
    def visit(node: str) -> None:
        if node in visiting: raise ValueError("coordination dependency cycle")
        if node in visited: return
        visiting.add(node)
        for dep in graph[node]: visit(dep)
        visiting.remove(node); visited.add(node)
    for node in graph: visit(node)


def create_plan(*, workspace_id: str, objective_id: str, cycle_id: str,
                coordinator_worker_id: str, tasks: tuple[WorkforceTask, ...],
                coordination_id: str, version: str = "1") -> WorkforceCoordinationPlan:
    plan = WorkforceCoordinationPlan(coordination_id, workspace_id, objective_id, cycle_id,
                                     coordinator_worker_id, tasks, version, "")
    plan = WorkforceCoordinationPlan(**{**plan.__dict__, "fingerprint": plan.compute_fingerprint()})
    _validate(plan)
    return plan


def ready_tasks(plan: WorkforceCoordinationPlan) -> tuple[WorkforceTask, ...]:
    _validate(plan)
    states = {t.task_id: t.state.value for t in plan.tasks}
    return tuple(t for t in plan.tasks if t.state.value == TaskState.PROPOSED.value and all(states[d] == TaskState.COMPLETED.value for d in t.depends_on))


def claim_task(*, plan: WorkforceCoordinationPlan, task_id: str, worker_id: str) -> WorkforceCoordinationPlan:
    _validate(plan)
    task = next((t for t in plan.tasks if t.task_id == task_id), None)
    if task is None: raise ValueError("task not found")
    if task.worker_id != worker_id: raise ValueError("worker assignment mismatch")
    if task not in ready_tasks(plan): raise ValueError("task is not ready")
    updated = tuple(WorkforceTask(**{**t.__dict__, "state": TaskState.CLAIMED}) if t.task_id == task_id else t for t in plan.tasks)
    result = WorkforceCoordinationPlan(**{**plan.__dict__, "tasks": updated, "fingerprint": ""})
    return WorkforceCoordinationPlan(**{**result.__dict__, "fingerprint": result.compute_fingerprint()})


def mark_task(*, plan: WorkforceCoordinationPlan, task_id: str, worker_id: str, state: TaskState) -> WorkforceCoordinationPlan:
    _validate(plan)
    task = next((t for t in plan.tasks if t.task_id == task_id), None)
    if task is None: raise ValueError("task not found")
    if task.worker_id != worker_id: raise ValueError("worker assignment mismatch")
    if state.value not in {TaskState.COMPLETED.value, TaskState.BLOCKED.value, TaskState.FAILED.value, TaskState.CANCELLED.value}:
        raise ValueError("invalid task completion state")
    if task.state.value != TaskState.CLAIMED.value: raise ValueError("task must be claimed")
    updated = tuple(WorkforceTask(**{**t.__dict__, "state": state}) if t.task_id == task_id else t for t in plan.tasks)
    result = WorkforceCoordinationPlan(**{**plan.__dict__, "tasks": updated, "fingerprint": ""})
    result = WorkforceCoordinationPlan(**{**result.__dict__, "fingerprint": result.compute_fingerprint()})
    return result


def execution_context(*, plan: WorkforceCoordinationPlan, task_id: str) -> Mapping[str, Any]:
    _validate(plan)
    task = next((t for t in plan.tasks if t.task_id == task_id), None)
    if task is None: raise ValueError("task not found")
    if task.state.value != TaskState.CLAIMED.value: raise ValueError("task must be claimed before execution")
    return {"schema_version": SCHEMA_VERSION, "coordination_id": plan.coordination_id,
            "workspace_id": plan.workspace_id, "objective_id": plan.objective_id,
            "cycle_id": plan.cycle_id, "task_id": task.task_id,
            "worker_id": task.worker_id, "capability": task.capability,
            "coordination_fingerprint": plan.fingerprint,
            "authority": "none", "advisory_only": True,
            "execution_effect": "none"}


def validate_execution_context(context: Mapping[str, Any], *, workspace_id: str,
                               objective_id: str, cycle_id: str) -> None:
    if context.get("authority") != "none" or context.get("advisory_only") is not True:
        raise ValueError("coordination cannot grant authority")
    if context.get("workspace_id") != workspace_id: raise ValueError("coordination workspace mismatch")
    if context.get("objective_id") != objective_id: raise ValueError("coordination objective mismatch")
    if context.get("cycle_id") != cycle_id: raise ValueError("coordination cycle mismatch")
    if context.get("execution_effect") != "none": raise ValueError("coordination cannot authorize execution")

__all__ = ["TaskState", "WorkforceTask", "WorkforceCoordinationPlan", "create_plan", "ready_tasks", "claim_task", "mark_task", "execution_context", "validate_execution_context"]
