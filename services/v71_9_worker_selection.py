"""V71.9 — Governed Worker Selection & Capacity Coordination.

Selects the best eligible worker for a bounded task. Selection is advisory:
it never grants authorization, execution rights, provider access, or Action Kernel access.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional
import hashlib, json

from services.v71_1_agent_identity import AgentIdentityRegistry, AgentStatus
from services.v71_2_agent_capability_permissions import AgentCapabilityPermissionRegistry
from services.v71_6_agent_budgets import AgentBudgetRegistry


class SelectionBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    AGENT_NOT_FOUND = "agent_not_found"
    AGENT_NOT_ACTIVE = "agent_not_active"
    TASK_INVALID = "task_invalid"
    NO_ELIGIBLE_WORKER = "no_eligible_worker"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    PERMISSION_DENIED = "permission_denied"
    SCOPE_MISMATCH = "scope_mismatch"
    CAPACITY_UNAVAILABLE = "capacity_unavailable"
    SAFETY_HOLD = "safety_hold"
    BUDGET_UNAVAILABLE = "budget_unavailable"
    SELECTION_TAMPERED = "selection_tampered"
    CAPACITY_TAMPERED = "capacity_tampered"
    DUPLICATE_CAPACITY = "duplicate_capacity"
    INVALID_CAPACITY = "invalid_capacity"


class WorkerAvailability(str, Enum):
    AVAILABLE = "available"
    BUSY = "busy"
    DRAINING = "draining"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class WorkerCapacity:
    agent_id: str
    workspace_id: str
    max_concurrent_tasks: int = 1
    active_tasks: int = 0
    availability: WorkerAvailability = WorkerAvailability.AVAILABLE
    safety_posture: str = "normal"
    autonomy_level: int = 0
    load_score: float = 0.0
    capacity_fingerprint: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id, "workspace_id": self.workspace_id,
            "max_concurrent_tasks": self.max_concurrent_tasks,
            "active_tasks": self.active_tasks, "availability": self.availability.value,
            "safety_posture": self.safety_posture, "autonomy_level": self.autonomy_level,
            "load_score": round(self.load_score, 6),
        }

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()

    @property
    def available_slots(self) -> int:
        return max(0, self.max_concurrent_tasks - self.active_tasks)


@dataclass(frozen=True)
class WorkerCandidate:
    agent_id: str
    capability_match: float
    capacity_score: float
    safety_score: float
    autonomy_score: float
    budget_score: float
    total_score: float
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkerSelection:
    selection_id: str
    workspace_id: str
    task_id: str
    capability: str
    requested_scope: Mapping[str, Any]
    selected_agent_id: Optional[str]
    candidates: tuple[WorkerCandidate, ...]
    advisory_only: bool = True
    authority_granted: bool = False
    execution_requested: bool = False
    selection_fingerprint: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "selection_id": self.selection_id, "workspace_id": self.workspace_id,
            "task_id": self.task_id, "capability": self.capability,
            "requested_scope": dict(self.requested_scope),
            "selected_agent_id": self.selected_agent_id,
            "candidates": [c.__dict__ for c in self.candidates],
            "advisory_only": self.advisory_only,
            "authority_granted": self.authority_granted,
            "execution_requested": self.execution_requested,
        }

    def fingerprint(self) -> str:
        raw = json.dumps(self.canonical_payload(), sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()


class WorkerCapacityRegistry:
    VERSION = "v71.9-capacity"

    def __init__(self, workspace_id: str, identity_registry: AgentIdentityRegistry):
        ws = str(workspace_id or "").strip()
        if not ws or identity_registry.workspace_id != ws:
            raise ValueError(SelectionBlock.TENANT_MISMATCH.value)
        self.workspace_id = ws
        self.identity_registry = identity_registry
        self._states: dict[str, WorkerCapacity] = {}

    def register(self, *, agent_id: str, max_concurrent_tasks: int = 1,
                 active_tasks: int = 0, availability: WorkerAvailability = WorkerAvailability.AVAILABLE,
                 safety_posture: str = "normal", autonomy_level: int = 0,
                 load_score: float = 0.0) -> WorkerCapacity:
        aid = str(agent_id or "").strip()
        if aid in self._states:
            raise ValueError(SelectionBlock.DUPLICATE_CAPACITY.value)
        agent = self.identity_registry.get(aid)
        if agent is None:
            raise ValueError(SelectionBlock.AGENT_NOT_FOUND.value)
        self.identity_registry.assert_integrity(agent)
        try:
            max_c, active, autonomy, load = int(max_concurrent_tasks), int(active_tasks), int(autonomy_level), float(load_score)
        except (TypeError, ValueError):
            raise ValueError(SelectionBlock.INVALID_CAPACITY.value)
        if max_c < 1 or active < 0 or active > max_c or not 0 <= autonomy <= 5 or not 0 <= load <= 1:
            raise ValueError(SelectionBlock.INVALID_CAPACITY.value)
        posture = str(safety_posture or "").strip().lower()
        if posture not in {"normal", "degraded", "hold", "suspended", "emergency"}:
            raise ValueError(SelectionBlock.INVALID_CAPACITY.value)
        state = WorkerCapacity(aid, self.workspace_id, max_c, active, WorkerAvailability(availability), posture, autonomy, round(load, 6), "")
        state = WorkerCapacity(**{**state.__dict__, "capacity_fingerprint": state.fingerprint()})
        self._states[aid] = state
        return state

    def get(self, agent_id: str) -> Optional[WorkerCapacity]:
        return self._states.get(str(agent_id))

    def assert_integrity(self, state: WorkerCapacity) -> None:
        if state.workspace_id != self.workspace_id:
            raise ValueError(SelectionBlock.TENANT_MISMATCH.value)
        if state.capacity_fingerprint != state.fingerprint():
            raise ValueError(SelectionBlock.CAPACITY_TAMPERED.value)

    def update_load(self, agent_id: str, *, active_tasks: int, load_score: float, availability: Optional[WorkerAvailability] = None) -> WorkerCapacity:
        state = self.get(agent_id)
        if state is None:
            raise ValueError(SelectionBlock.AGENT_NOT_FOUND.value)
        self.assert_integrity(state)
        active, load = int(active_tasks), float(load_score)
        if active < 0 or active > state.max_concurrent_tasks or not 0 <= load <= 1:
            raise ValueError(SelectionBlock.INVALID_CAPACITY.value)
        updated = WorkerCapacity(**{**state.__dict__, "active_tasks": active, "load_score": round(load, 6),
                                    "availability": state.availability if availability is None else WorkerAvailability(availability),
                                    "capacity_fingerprint": ""})
        updated = WorkerCapacity(**{**updated.__dict__, "capacity_fingerprint": updated.fingerprint()})
        self._states[agent_id] = updated
        return updated


class WorkerSelectionRegistry:
    VERSION = "v71.9"

    def __init__(self, workspace_id: str, identity_registry: AgentIdentityRegistry,
                 permission_registry: AgentCapabilityPermissionRegistry,
                 capacity_registry: WorkerCapacityRegistry,
                 budget_registry: Optional[AgentBudgetRegistry] = None,
                 worker_registry: Any = None):
        ws = str(workspace_id or "").strip()
        regs = (identity_registry, permission_registry, capacity_registry, budget_registry)
        if not ws or any(r is not None and r.workspace_id != ws for r in regs):
            raise ValueError(SelectionBlock.TENANT_MISMATCH.value)
        self.workspace_id = ws
        self.identity_registry = identity_registry
        self.permission_registry = permission_registry
        self.capacity_registry = capacity_registry
        self.budget_registry = budget_registry
        self.worker_registry = worker_registry
        self._selections: dict[str, WorkerSelection] = {}

    @staticmethod
    def _scope_reasons(permission_check: Any) -> tuple[str, ...]:
        return tuple(str(x) for x in (permission_check[1] if isinstance(permission_check, tuple) and len(permission_check) > 1 else ()))

    def select(self, *, selection_id: str, task_id: str, capability: str,
               requested_scope: Optional[Mapping[str, Any]] = None,
               candidate_agent_ids: Optional[tuple[str, ...]] = None,
               minimum_autonomy_level: int = 0,
               budget_ids: Optional[Mapping[str, str]] = None,
               selection_policy: Optional[Mapping[str, float]] = None) -> WorkerSelection:
        sid, tid, cap = str(selection_id or "").strip(), str(task_id or "").strip(), str(capability or "").strip()
        if not sid or not tid or not cap or sid in self._selections:
            raise ValueError(SelectionBlock.TASK_INVALID.value)
        try: min_auto = int(minimum_autonomy_level)
        except (TypeError, ValueError): raise ValueError(SelectionBlock.TASK_INVALID.value)
        if not 0 <= min_auto <= 5:
            raise ValueError(SelectionBlock.TASK_INVALID.value)
        scope = dict(requested_scope or {})
        ids = tuple(candidate_agent_ids) if candidate_agent_ids is not None else tuple(a.agent_id for a in self.identity_registry.list_agents())
        candidates: list[WorkerCandidate] = []
        weights = {"capability": 0.30, "capacity": 0.30, "safety": 0.20, "autonomy": 0.10, "budget": 0.10}
        if selection_policy is not None:
            for key in tuple(weights):
                if key in selection_policy:
                    weights[key] = float(selection_policy[key])
            if any(v < 0 for v in weights.values()) or abs(sum(weights.values()) - 1.0) > 1e-9:
                raise ValueError(SelectionBlock.TASK_INVALID.value)
        for aid in sorted(set(str(x) for x in ids)):
            agent = self.identity_registry.get(aid)
            if agent is None:
                continue
            self.identity_registry.assert_integrity(agent)
            if agent.status != AgentStatus.ACTIVE:
                continue
            # Optional V71.21 lifecycle fence: identity alone is never enough.
            # Use duck typing to avoid a circular import from the registry module.
            if self.worker_registry is not None:
                worker = self.worker_registry.get(aid)
                if worker is None or str(getattr(worker, "lifecycle", "")) not in {"WorkerLifecycle.ACTIVE", "active"}:
                    continue
                if str(getattr(worker, "health", "")) in {"WorkerHealth.UNAVAILABLE", "unavailable"}:
                    continue
                self.worker_registry.assert_integrity(worker)
            if cap not in agent.declared_capabilities:
                continue
            state = self.capacity_registry.get(aid)
            if state is None:
                continue
            self.capacity_registry.assert_integrity(state)
            if state.availability != WorkerAvailability.AVAILABLE or state.available_slots <= 0:
                continue
            if state.safety_posture in {"hold", "suspended", "emergency"}:
                continue
            allowed, reasons = self.permission_registry.check(agent_id=aid, capability=cap, requested_scope=scope)
            if not allowed:
                continue
            budget_score = 1.0
            if self.budget_registry is not None and budget_ids and aid in budget_ids:
                check = self.budget_registry.check(budget_ids[aid])
                if not check.allowed:
                    continue
                rem = check.remaining
                finite = [float(v) for v in rem.values() if v is not None]
                if finite:
                    budget_score = min(1.0, max(0.0, min(finite) / max(1.0, min(finite) + 1.0)))
            capability_score = 1.0
            capacity_score = round(min(1.0, state.available_slots / max(1, state.max_concurrent_tasks)) * (1.0 - state.load_score), 6)
            safety_score = {"normal": 1.0, "degraded": 0.65}.get(state.safety_posture, 0.0)
            autonomy_score = 1.0 if state.autonomy_level >= min_auto else 0.0
            if autonomy_score == 0:
                continue
            total = round(weights["capability"] * capability_score + weights["capacity"] * capacity_score + weights["safety"] * safety_score + weights["autonomy"] * autonomy_score + weights["budget"] * budget_score, 6)
            candidates.append(WorkerCandidate(aid, capability_score, capacity_score, safety_score, autonomy_score, budget_score, total, tuple(reasons)))
        candidates.sort(key=lambda c: (-c.total_score, c.agent_id))
        selected = candidates[0].agent_id if candidates else None
        result = WorkerSelection(sid, self.workspace_id, tid, cap, scope, selected, tuple(candidates), True, False, False, "")
        result = WorkerSelection(**{**result.__dict__, "selection_fingerprint": result.fingerprint()})
        self._selections[sid] = result
        return result

    def get(self, selection_id: str) -> Optional[WorkerSelection]:
        return self._selections.get(str(selection_id))

    def assert_integrity(self, selection: WorkerSelection) -> None:
        if selection.workspace_id != self.workspace_id:
            raise ValueError(SelectionBlock.TENANT_MISMATCH.value)
        if selection.selection_fingerprint != selection.fingerprint():
            raise ValueError(SelectionBlock.SELECTION_TAMPERED.value)

    def context(self, selection_id: str) -> Mapping[str, Any]:
        selection = self.get(selection_id)
        if selection is None:
            raise ValueError(SelectionBlock.NO_ELIGIBLE_WORKER.value)
        self.assert_integrity(selection)
        return {"selection_id": selection.selection_id, "selected_agent_id": selection.selected_agent_id,
                "candidate_count": len(selection.candidates), "advisory_only": True,
                "authority_granted": False, "execution_requested": False}


__all__ = ["SelectionBlock", "WorkerAvailability", "WorkerCapacity", "WorkerCandidate", "WorkerSelection", "WorkerCapacityRegistry", "WorkerSelectionRegistry"]
