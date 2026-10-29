"""V71.4 — Governed AI Workforce: Agent Planning Plane.

Planning constructs bounded, inspectable plans for business objectives. A plan is
never execution authority: every step is advisory until independently admitted by
KDO governance and the Action Kernel.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional
import hashlib, json

from services.v71_1_agent_identity import AgentIdentityRegistry, AgentStatus
from services.v71_2_agent_capability_permissions import AgentCapabilityPermissionRegistry
from services.v71_3_agent_memory import AgentMemoryRegistry


class PlanStatus(str, Enum):
    DRAFT = "draft"
    READY = "ready"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class PlanDisposition(str, Enum):
    EXECUTABLE_CANDIDATE = "executable_candidate"
    ADVISORY_ONLY = "advisory_only"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"


class PlanningBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    AGENT_NOT_FOUND = "agent_not_found"
    AGENT_NOT_ACTIVE = "agent_not_active"
    INVALID_PLAN = "invalid_plan"
    DUPLICATE_PLAN = "duplicate_plan"
    PLAN_TAMPERED = "plan_tampered"
    PLAN_NOT_FOUND = "plan_not_found"
    TERMINAL = "plan_terminal"


@dataclass(frozen=True)
class PlanStep:
    step_id: str
    title: str
    capability: str
    rationale: str
    requested_scope: Mapping[str, Any] = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()
    disposition: PlanDisposition = PlanDisposition.ADVISORY_ONLY
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def canonical_payload(self) -> dict[str, Any]:
        return {"step_id": self.step_id, "title": self.title, "capability": self.capability,
                "rationale": self.rationale, "requested_scope": dict(self.requested_scope),
                "depends_on": list(self.depends_on), "disposition": self.disposition.value,
                "metadata": dict(self.metadata)}


@dataclass(frozen=True)
class AgentPlan:
    plan_id: str
    workspace_id: str
    agent_id: str
    objective: str
    constraints: tuple[str, ...]
    steps: tuple[PlanStep, ...]
    status: PlanStatus = PlanStatus.DRAFT
    version: str = "1"
    plan_fingerprint: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return {"plan_id": self.plan_id, "workspace_id": self.workspace_id, "agent_id": self.agent_id,
                "objective": self.objective, "constraints": list(self.constraints),
                "steps": [s.canonical_payload() for s in self.steps],
                "status": self.status.value, "version": self.version}

    def fingerprint(self) -> str:
        raw = json.dumps(self.canonical_payload(), sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()


class AgentPlanningRegistry:
    VERSION = "v71.4"

    def __init__(self, workspace_id: str, identity_registry: AgentIdentityRegistry,
                 permission_registry: AgentCapabilityPermissionRegistry,
                 memory_registry: Optional[AgentMemoryRegistry] = None):
        ws = str(workspace_id or "").strip()
        if not ws or identity_registry.workspace_id != ws or permission_registry.workspace_id != ws:
            raise ValueError(PlanningBlock.TENANT_MISMATCH.value)
        if memory_registry is not None and memory_registry.workspace_id != ws:
            raise ValueError(PlanningBlock.TENANT_MISMATCH.value)
        self.workspace_id = ws
        self.identity_registry = identity_registry
        self.permission_registry = permission_registry
        self.memory_registry = memory_registry
        self._plans: dict[str, AgentPlan] = {}

    def _validate_agent(self, agent_id: str):
        agent = self.identity_registry.get(agent_id)
        if agent is None:
            raise ValueError(PlanningBlock.AGENT_NOT_FOUND.value)
        self.identity_registry.assert_integrity(agent)
        if agent.status != AgentStatus.ACTIVE:
            raise ValueError(PlanningBlock.AGENT_NOT_ACTIVE.value)
        return agent

    @staticmethod
    def _validate_steps(steps: tuple[PlanStep, ...]) -> None:
        ids = [s.step_id for s in steps]
        if not ids or any(not str(x).strip() for x in ids) or len(ids) != len(set(ids)):
            raise ValueError(PlanningBlock.INVALID_PLAN.value)
        known = set(ids)
        for step in steps:
            if not step.title.strip() or not step.capability.strip() or not step.rationale.strip():
                raise ValueError(PlanningBlock.INVALID_PLAN.value)
            if step.step_id in step.depends_on or any(dep not in known for dep in step.depends_on):
                raise ValueError(PlanningBlock.INVALID_PLAN.value)
        # Dependency cycle detection.
        visiting, visited = set(), set()
        graph = {s.step_id: set(s.depends_on) for s in steps}
        def visit(node: str):
            if node in visiting:
                raise ValueError(PlanningBlock.INVALID_PLAN.value)
            if node in visited:
                return
            visiting.add(node)
            for dep in graph[node]: visit(dep)
            visiting.remove(node); visited.add(node)
        for node in graph: visit(node)

    def create_plan(self, *, plan_id: str, agent_id: str, objective: str,
                    steps: tuple[PlanStep, ...], constraints: tuple[str, ...] = (),
                    version: str = "1") -> AgentPlan:
        pid, obj = str(plan_id or "").strip(), str(objective or "").strip()
        if not pid or not obj or not str(version or "").strip() or pid in self._plans:
            raise ValueError(PlanningBlock.DUPLICATE_PLAN.value if pid in self._plans else PlanningBlock.INVALID_PLAN.value)
        self._validate_agent(agent_id)
        normalized = tuple(steps)
        self._validate_steps(normalized)
        plan = AgentPlan(pid, self.workspace_id, str(agent_id), obj,
                         tuple(str(c).strip() for c in constraints if str(c).strip()), normalized,
                         PlanStatus.DRAFT, str(version), "")
        plan = AgentPlan(**{**plan.__dict__, "plan_fingerprint": plan.fingerprint()})
        self._plans[pid] = plan
        return plan

    def assess(self, plan_id: str) -> AgentPlan:
        plan = self._plans.get(str(plan_id))
        if plan is None: raise ValueError(PlanningBlock.PLAN_NOT_FOUND.value)
        if plan.plan_fingerprint != plan.fingerprint(): raise ValueError(PlanningBlock.PLAN_TAMPERED.value)
        self._validate_agent(plan.agent_id)
        steps = []
        blocked = False
        for step in plan.steps:
            allowed, reasons = self.permission_registry.check(agent_id=plan.agent_id,
                                                               capability=step.capability,
                                                               requested_scope=step.requested_scope)
            if allowed:
                disposition = PlanDisposition.EXECUTABLE_CANDIDATE
            elif any(r in {"capability_not_declared", "permission_not_found"} for r in reasons):
                disposition = PlanDisposition.CAPABILITY_UNAVAILABLE
                blocked = True
            else:
                disposition = PlanDisposition.ADVISORY_ONLY
                blocked = True
            steps.append(PlanStep(**{**step.__dict__, "disposition": disposition,
                                     "metadata": {**dict(step.metadata), "permission_reasons": list(reasons)}}))
        status = PlanStatus.BLOCKED if blocked else PlanStatus.READY
        updated = AgentPlan(**{**plan.__dict__, "steps": tuple(steps), "status": status, "plan_fingerprint": ""})
        updated = AgentPlan(**{**updated.__dict__, "plan_fingerprint": updated.fingerprint()})
        self._plans[plan.plan_id] = updated
        return updated

    def get(self, plan_id: str) -> Optional[AgentPlan]:
        return self._plans.get(str(plan_id))

    def cancel(self, plan_id: str) -> AgentPlan:
        plan = self.get(plan_id)
        if plan is None: raise ValueError(PlanningBlock.PLAN_NOT_FOUND.value)
        if plan.plan_fingerprint != plan.fingerprint(): raise ValueError(PlanningBlock.PLAN_TAMPERED.value)
        if plan.status == PlanStatus.CANCELLED: return plan
        updated = AgentPlan(**{**plan.__dict__, "status": PlanStatus.CANCELLED, "plan_fingerprint": ""})
        updated = AgentPlan(**{**updated.__dict__, "plan_fingerprint": updated.fingerprint()})
        self._plans[plan.plan_id] = updated
        return updated

    def planning_context(self, *, agent_id: str, objective: str) -> Mapping[str, Any]:
        """Context for a planner; contains no approval, authority or execution token."""
        self._validate_agent(agent_id)
        context: dict[str, Any] = {"objective": str(objective), "agent_id": str(agent_id), "advisory": True}
        if self.memory_registry is not None:
            context["memory"] = list(self.memory_registry.usable_context(agent_id=agent_id))
        return context

    def list_plans(self, agent_id: Optional[str] = None) -> tuple[AgentPlan, ...]:
        values = tuple(self._plans.values())
        return values if agent_id is None else tuple(p for p in values if p.agent_id == str(agent_id))


__all__ = ["PlanStatus", "PlanDisposition", "PlanningBlock", "PlanStep", "AgentPlan", "AgentPlanningRegistry"]
