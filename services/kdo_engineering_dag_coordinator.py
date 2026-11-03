"""Persisted DAG readiness coordination for KDO engineering tasks.

This service validates a task DAG against persisted task contracts and queues only
tasks whose prerequisites are completed and whose specialist adapter is installed.
It does NOT execute agents, claim worker leases, or provide a durable queue. Those
remain explicit host/runtime responsibilities; callers must still enforce policy.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from services.kdo_engineering_agent_registry import AgentRegistry
from services.kdo_engineering_task_repository import (
    EngineeringTaskConflict,
    EngineeringTaskNotFound,
    SQLiteEngineeringTaskRepository,
)
from services.kdo_engineering_vertical_contracts import (
    EngineeringTaskContract,
    EngineeringTaskStatus,
)


class EngineeringDagError(ValueError):
    """Raised when a persisted task graph is invalid or out of scope."""


@dataclass(frozen=True)
class DagTaskSpec:
    task: EngineeringTaskContract
    depends_on: tuple[str, ...]
    role_id: str


def _task_spec(task: EngineeringTaskContract) -> DagTaskSpec:
    metadata = task.metadata
    if not isinstance(metadata, Mapping):
        raise EngineeringDagError("task_metadata_must_be_mapping")
    dependencies = metadata.get("depends_on", ())
    role_id = metadata.get("role_id")
    if not isinstance(dependencies, (list, tuple)):
        raise EngineeringDagError(f"dependencies_must_be_list:{task.task_id}")
    if any(not isinstance(dep, str) or not dep.strip() for dep in dependencies):
        raise EngineeringDagError(f"invalid_dependency_id:{task.task_id}")
    deps = tuple(dependencies)
    if len(set(deps)) != len(deps):
        raise EngineeringDagError(f"duplicate_dependency:{task.task_id}")
    if task.task_id in deps:
        raise EngineeringDagError(f"self_dependency:{task.task_id}")
    if not isinstance(role_id, str) or not role_id.strip():
        raise EngineeringDagError(f"role_id_required:{task.task_id}")
    return DagTaskSpec(task=task, depends_on=deps, role_id=role_id)


def _topological_order(specs: Mapping[str, DagTaskSpec]) -> list[str]:
    for task_id, spec in specs.items():
        unknown = set(spec.depends_on) - specs.keys()
        if unknown:
            raise EngineeringDagError(
                f"unknown_dependencies:{task_id}:{','.join(sorted(unknown))}"
            )
    visiting: set[str] = set()
    visited: set[str] = set()
    order: list[str] = []

    def visit(task_id: str) -> None:
        if task_id in visiting:
            raise EngineeringDagError("dependency_cycle_detected")
        if task_id in visited:
            return
        visiting.add(task_id)
        for dependency in specs[task_id].depends_on:
            visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)
        order.append(task_id)

    for task_id in sorted(specs):
        visit(task_id)
    return order


class EngineeringDagCoordinator:
    """Reconcile one persisted, tenant/workspace-scoped DAG into safe queue candidates.

    Each task's metadata must include:
      - role_id: a registered specialist role
      - depends_on: optional list/tuple of task IDs in this graph

    The supplied contracts are checked against the repository before any transition.
    Planned tasks are queued only when all dependencies are COMPLETED and the role's
    runtime adapter is installed. Failed prerequisites block downstream planned or
    queued tasks. This is a readiness coordinator, not a worker or authorization gate.
    """

    def __init__(
        self,
        *,
        task_repository: SQLiteEngineeringTaskRepository,
        agent_registry: AgentRegistry,
    ) -> None:
        self._tasks = task_repository
        self._agents = agent_registry

    def reconcile(
        self,
        tasks: Iterable[EngineeringTaskContract],
        *,
        tenant_id: str,
        workspace_id: str,
    ) -> dict[str, Any]:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise EngineeringDagError("tenant_id_required")
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise EngineeringDagError("workspace_id_required")

        supplied = list(tasks)
        if not supplied:
            raise EngineeringDagError("task_graph_required")
        specs: dict[str, DagTaskSpec] = {}
        for task in supplied:
            if task.tenant_id != tenant_id or task.workspace_id != workspace_id:
                raise EngineeringDagError("task_graph_scope_mismatch")
            if task.task_id in specs:
                raise EngineeringDagError(f"duplicate_task_id:{task.task_id}")
            try:
                stored = self._tasks.get(
                    task.task_id, tenant_id=tenant_id, workspace_id=workspace_id
                )
            except EngineeringTaskNotFound as exc:
                # Keep cross-scope existence opaque at the repository boundary,
                # while exposing the coordinator's stable scope error contract.
                raise EngineeringDagError("task_graph_scope_mismatch") from exc
            if stored.fingerprint() != task.fingerprint():
                raise EngineeringTaskConflict(
                    f"stale_supplied_task_revision:{task.task_id}"
                )
            spec = _task_spec(task)
            known_roles = {role.role_id for role in self._agents.roles()}
            if spec.role_id not in known_roles:
                raise EngineeringDagError(
                    f"unknown_role:{task.task_id}:{spec.role_id}"
                )
            specs[task.task_id] = spec

        order = _topological_order(specs)
        status_by_id = {task_id: specs[task_id].task.status for task_id in specs}
        results: dict[str, dict[str, Any]] = {}
        ready: list[str] = []
        queued: list[str] = []
        blocked: list[str] = []
        waiting: list[str] = []
        unavailable: list[str] = []

        for task_id in order:
            spec = specs[task_id]
            current_status = status_by_id[task_id]
            failed_dependencies = [
                dep for dep in spec.depends_on
                if status_by_id[dep] in {
                    EngineeringTaskStatus.FAILED,
                    EngineeringTaskStatus.BLOCKED,
                    EngineeringTaskStatus.CANCELLED,
                }
            ]
            if failed_dependencies:
                if current_status in {
                    EngineeringTaskStatus.PLANNED,
                    EngineeringTaskStatus.QUEUED,
                }:
                    updated = self._tasks.transition(
                        task_id,
                        EngineeringTaskStatus.BLOCKED,
                        tenant_id=tenant_id,
                        workspace_id=workspace_id,
                        expected_revision=spec.task.revision,
                        evidence={
                            "dag_block": {
                                "reason": "dependency_failed",
                                "blocked_by": failed_dependencies,
                            }
                        },
                    )
                    status_by_id[task_id] = updated.status
                    blocked.append(task_id)
                    results[task_id] = {
                        "status": updated.status.value,
                        "reason": "dependency_failed",
                        "blocked_by": failed_dependencies,
                    }
                else:
                    results[task_id] = {
                        "status": current_status.value,
                        "reason": "dependency_failed",
                        "blocked_by": failed_dependencies,
                    }
                continue

            incomplete_dependencies = [
                dep for dep in spec.depends_on
                if status_by_id[dep] != EngineeringTaskStatus.COMPLETED
            ]
            if incomplete_dependencies:
                waiting.append(task_id)
                results[task_id] = {
                    "status": current_status.value,
                    "reason": "dependencies_not_completed",
                    "waiting_for": incomplete_dependencies,
                }
                continue

            adapter = self._agents.adapter(spec.role_id)
            if adapter is None:
                unavailable.append(task_id)
                results[task_id] = {
                    "status": current_status.value,
                    "reason": "specialist_adapter_unavailable",
                    "role_id": spec.role_id,
                }
                continue

            if current_status == EngineeringTaskStatus.PLANNED:
                updated = self._tasks.transition(
                    task_id,
                    EngineeringTaskStatus.QUEUED,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    expected_revision=spec.task.revision,
                    evidence={
                        "dag_readiness": {
                            "status": "queued",
                            "dependencies": list(spec.depends_on),
                            "role_id": spec.role_id,
                            "execution_started": False,
                        }
                    },
                )
                status_by_id[task_id] = updated.status
                queued.append(task_id)
                results[task_id] = {
                    "status": updated.status.value,
                    "reason": "queued_dependencies_satisfied",
                    "role_id": spec.role_id,
                }
            elif current_status == EngineeringTaskStatus.QUEUED:
                ready.append(task_id)
                results[task_id] = {
                    "status": current_status.value,
                    "reason": "ready_for_worker_claim",
                    "role_id": spec.role_id,
                }
            else:
                results[task_id] = {
                    "status": current_status.value,
                    "reason": "lifecycle_state_not_dispatchable",
                    "role_id": spec.role_id,
                }

        return {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "topological_order": order,
            "queued_task_ids": queued,
            "ready_task_ids": ready,
            "blocked_task_ids": blocked,
            "waiting_task_ids": waiting,
            "unavailable_task_ids": unavailable,
            "tasks": results,
            "execution_started": False,
            "release_authorized": False,
        }
