"""V71.8 — Governed AI Workforce: Multi-Agent Coordination.

Coordinates bounded collaboration between agents without creating a second
execution or governance plane. Delegation is a coordination request only:
the receiving agent must independently satisfy identity, capability,
scope, policy, risk, budget, approval and Action Kernel controls before any
real-world side effect can occur.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional
import hashlib, json

from services.v71_1_agent_identity import AgentIdentityRegistry, AgentStatus
from services.v71_2_agent_capability_permissions import AgentCapabilityPermissionRegistry
from services.v71_3_agent_memory import AgentMemoryRegistry
from services.v71_4_agent_planning import AgentPlanningRegistry
from services.v71_5_agent_evaluation import AgentEvaluationRegistry
from services.v71_6_agent_budgets import AgentBudgetRegistry
from services.v71_7_human_takeover import HumanTakeoverRegistry


class CoordinationStatus(str, Enum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class CoordinationDisposition(str, Enum):
    ACCEPTED_FOR_GOVERNANCE = "accepted_for_governance"
    ADVISORY_ONLY = "advisory_only"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    REJECTED = "rejected"


class CoordinationBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    AGENT_NOT_FOUND = "agent_not_found"
    AGENT_NOT_ACTIVE = "agent_not_active"
    DUPLICATE_COORDINATION = "duplicate_coordination"
    COORDINATION_NOT_FOUND = "coordination_not_found"
    COORDINATION_TAMPERED = "coordination_tampered"
    INVALID_COORDINATION = "invalid_coordination"
    TERMINAL = "coordination_terminal"
    INVALID_TRANSITION = "invalid_coordination_transition"
    SELF_DELEGATION = "self_delegation"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    PERMISSION_DENIED = "permission_denied"
    SCOPE_MISMATCH = "scope_mismatch"
    CROSS_TENANT = "cross_tenant_delegation"


TERMINAL = frozenset({CoordinationStatus.COMPLETED, CoordinationStatus.CANCELLED})


@dataclass(frozen=True)
class CoordinationTask:
    task_id: str
    title: str
    capability: str
    requested_scope: Mapping[str, Any] = field(default_factory=dict)
    rationale: str = ""
    depends_on: tuple[str, ...] = ()
    disposition: CoordinationDisposition = CoordinationDisposition.ADVISORY_ONLY
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id, "title": self.title, "capability": self.capability,
            "requested_scope": dict(self.requested_scope), "rationale": self.rationale,
            "depends_on": list(self.depends_on), "disposition": self.disposition.value,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class CoordinationRequest:
    coordination_id: str
    workspace_id: str
    coordinator_agent_id: str
    target_agent_id: str
    objective: str
    tasks: tuple[CoordinationTask, ...]
    status: CoordinationStatus = CoordinationStatus.PROPOSED
    version: str = "1"
    coordination_fingerprint: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "coordination_id": self.coordination_id, "workspace_id": self.workspace_id,
            "coordinator_agent_id": self.coordinator_agent_id,
            "target_agent_id": self.target_agent_id, "objective": self.objective,
            "tasks": [t.canonical_payload() for t in self.tasks],
            "status": self.status.value, "version": self.version,
        }

    def fingerprint(self) -> str:
        raw = json.dumps(self.canonical_payload(), sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class CoordinationResult:
    coordination_id: str
    status: CoordinationStatus
    dispositions: Mapping[str, CoordinationDisposition]
    reasons: Mapping[str, tuple[str, ...]]
    authority_granted: bool = False
    execution_requested: bool = False


class MultiAgentCoordinationRegistry:
    VERSION = "v71.8"

    def __init__(
        self,
        workspace_id: str,
        identity_registry: AgentIdentityRegistry,
        permission_registry: AgentCapabilityPermissionRegistry,
        planning_registry: Optional[AgentPlanningRegistry] = None,
        memory_registry: Optional[AgentMemoryRegistry] = None,
        evaluation_registry: Optional[AgentEvaluationRegistry] = None,
        budget_registry: Optional[AgentBudgetRegistry] = None,
        takeover_registry: Optional[HumanTakeoverRegistry] = None,
    ):
        ws = str(workspace_id or "").strip()
        registries = (identity_registry, permission_registry, planning_registry,
                      memory_registry, evaluation_registry, budget_registry, takeover_registry)
        if not ws or any(r is not None and r.workspace_id != ws for r in registries):
            raise ValueError(CoordinationBlock.TENANT_MISMATCH.value)
        self.workspace_id = ws
        self.identity_registry = identity_registry
        self.permission_registry = permission_registry
        self.planning_registry = planning_registry
        self.memory_registry = memory_registry
        self.evaluation_registry = evaluation_registry
        self.budget_registry = budget_registry
        self.takeover_registry = takeover_registry
        self._requests: dict[str, CoordinationRequest] = {}

    def _active_agent(self, agent_id: str):
        agent = self.identity_registry.get(str(agent_id))
        if agent is None:
            raise ValueError(CoordinationBlock.AGENT_NOT_FOUND.value)
        self.identity_registry.assert_integrity(agent)
        if agent.status != AgentStatus.ACTIVE:
            raise ValueError(CoordinationBlock.AGENT_NOT_ACTIVE.value)
        return agent

    @staticmethod
    def _validate_tasks(tasks: tuple[CoordinationTask, ...]) -> None:
        ids = [str(t.task_id).strip() for t in tasks]
        if not ids or any(not x for x in ids) or len(ids) != len(set(ids)):
            raise ValueError(CoordinationBlock.INVALID_COORDINATION.value)
        known = set(ids)
        graph = {}
        for task in tasks:
            if not str(task.title).strip() or not str(task.capability).strip():
                raise ValueError(CoordinationBlock.INVALID_COORDINATION.value)
            if any(dep not in known for dep in task.depends_on) or task.task_id in task.depends_on:
                raise ValueError(CoordinationBlock.INVALID_COORDINATION.value)
            graph[task.task_id] = set(task.depends_on)
        visiting, visited = set(), set()
        def visit(node: str):
            if node in visiting:
                raise ValueError(CoordinationBlock.INVALID_COORDINATION.value)
            if node in visited:
                return
            visiting.add(node)
            for dep in graph[node]:
                visit(dep)
            visiting.remove(node)
            visited.add(node)
        for node in graph:
            visit(node)

    def create(self, *, coordination_id: str, coordinator_agent_id: str,
               target_agent_id: str, objective: str,
               tasks: tuple[CoordinationTask, ...], version: str = "1") -> CoordinationRequest:
        cid = str(coordination_id or "").strip()
        coordinator = str(coordinator_agent_id or "").strip()
        target = str(target_agent_id or "").strip()
        obj = str(objective or "").strip()
        if not cid or cid in self._requests:
            raise ValueError(CoordinationBlock.DUPLICATE_COORDINATION.value)
        if not obj or not str(version or "").strip():
            raise ValueError(CoordinationBlock.INVALID_COORDINATION.value)
        if not coordinator or not target or coordinator == target:
            raise ValueError(CoordinationBlock.SELF_DELEGATION.value)
        self._active_agent(coordinator)
        self._active_agent(target)
        normalized = tuple(tasks)
        self._validate_tasks(normalized)
        request = CoordinationRequest(cid, self.workspace_id, coordinator, target, obj,
                                      normalized, CoordinationStatus.PROPOSED, str(version), "")
        request = CoordinationRequest(**{**request.__dict__, "coordination_fingerprint": request.fingerprint()})
        self._requests[cid] = request
        return request

    def get(self, coordination_id: str) -> Optional[CoordinationRequest]:
        return self._requests.get(str(coordination_id))

    def assert_integrity(self, request: CoordinationRequest) -> None:
        if request.workspace_id != self.workspace_id:
            raise ValueError(CoordinationBlock.TENANT_MISMATCH.value)
        if request.coordination_fingerprint != request.fingerprint():
            raise ValueError(CoordinationBlock.COORDINATION_TAMPERED.value)

    def assess(self, coordination_id: str) -> CoordinationResult:
        request = self.get(coordination_id)
        if request is None:
            raise ValueError(CoordinationBlock.COORDINATION_NOT_FOUND.value)
        self.assert_integrity(request)
        self._active_agent(request.coordinator_agent_id)
        self._active_agent(request.target_agent_id)
        if request.status in TERMINAL:
            raise ValueError(CoordinationBlock.TERMINAL.value)

        dispositions: dict[str, CoordinationDisposition] = {}
        reasons: dict[str, tuple[str, ...]] = {}
        blocked = False
        for task in request.tasks:
            allowed, permission_reasons = self.permission_registry.check(
                agent_id=request.target_agent_id,
                capability=task.capability,
                requested_scope=task.requested_scope,
            )
            if allowed:
                disposition = CoordinationDisposition.ACCEPTED_FOR_GOVERNANCE
            elif any(r == "capability_not_declared" or r == "permission_not_found" for r in permission_reasons):
                disposition = CoordinationDisposition.CAPABILITY_UNAVAILABLE
                blocked = True
            else:
                disposition = CoordinationDisposition.ADVISORY_ONLY
                blocked = True
            dispositions[task.task_id] = disposition
            reasons[task.task_id] = tuple(permission_reasons)

        status = CoordinationStatus.BLOCKED if blocked else CoordinationStatus.ACCEPTED
        updated_tasks = tuple(
            CoordinationTask(**{**task.__dict__, "disposition": dispositions[task.task_id],
                                "metadata": {**dict(task.metadata), "permission_reasons": list(reasons[task.task_id])}})
            for task in request.tasks
        )
        updated = CoordinationRequest(**{**request.__dict__, "tasks": updated_tasks,
                                         "status": status, "coordination_fingerprint": ""})
        updated = CoordinationRequest(**{**updated.__dict__, "coordination_fingerprint": updated.fingerprint()})
        self._requests[request.coordination_id] = updated
        return CoordinationResult(request.coordination_id, status, dispositions, reasons, False, False)

    def complete(self, coordination_id: str) -> CoordinationRequest:
        request = self.get(coordination_id)
        if request is None:
            raise ValueError(CoordinationBlock.COORDINATION_NOT_FOUND.value)
        self.assert_integrity(request)
        if request.status in TERMINAL:
            raise ValueError(CoordinationBlock.TERMINAL.value)
        if request.status != CoordinationStatus.ACCEPTED:
            raise ValueError(CoordinationBlock.INVALID_TRANSITION.value)
        updated = CoordinationRequest(**{**request.__dict__, "status": CoordinationStatus.COMPLETED,
                                         "coordination_fingerprint": ""})
        updated = CoordinationRequest(**{**updated.__dict__, "coordination_fingerprint": updated.fingerprint()})
        self._requests[request.coordination_id] = updated
        return updated

    def cancel(self, coordination_id: str) -> CoordinationRequest:
        request = self.get(coordination_id)
        if request is None:
            raise ValueError(CoordinationBlock.COORDINATION_NOT_FOUND.value)
        self.assert_integrity(request)
        if request.status in TERMINAL:
            raise ValueError(CoordinationBlock.TERMINAL.value)
        updated = CoordinationRequest(**{**request.__dict__, "status": CoordinationStatus.CANCELLED,
                                         "coordination_fingerprint": ""})
        updated = CoordinationRequest(**{**updated.__dict__, "coordination_fingerprint": updated.fingerprint()})
        self._requests[request.coordination_id] = updated
        return updated

    def coordination_context(self, coordination_id: str) -> Mapping[str, Any]:
        request = self.get(coordination_id)
        if request is None:
            raise ValueError(CoordinationBlock.COORDINATION_NOT_FOUND.value)
        self.assert_integrity(request)
        return {
            "coordination_id": request.coordination_id,
            "coordinator_agent_id": request.coordinator_agent_id,
            "target_agent_id": request.target_agent_id,
            "objective": request.objective,
            "tasks": [t.canonical_payload() for t in request.tasks],
            "status": request.status.value,
            "advisory_only": True,
            "authority_granted": False,
            "execution_requested": False,
        }

    def list_requests(self, *, agent_id: Optional[str] = None) -> tuple[CoordinationRequest, ...]:
        values = tuple(self._requests.values())
        if agent_id is None:
            return values
        aid = str(agent_id)
        self._active_agent(aid)
        return tuple(r for r in values if r.coordinator_agent_id == aid or r.target_agent_id == aid)


__all__ = [
    "CoordinationStatus", "CoordinationDisposition", "CoordinationBlock",
    "CoordinationTask", "CoordinationRequest", "CoordinationResult",
    "MultiAgentCoordinationRegistry",
]
