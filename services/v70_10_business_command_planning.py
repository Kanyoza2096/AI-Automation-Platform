"""V70.10 — Business Command & Planning Operating System.

Builds explainable, tenant-scoped plans from explicit business objectives,
constraints, capacity and evidence. Planning is advisory: it never executes
providers or mutates business systems. Any consequential command is handed to
the existing BusinessOperatingLayer/Action Kernel through an explicit governed workflow.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional
import hashlib, json


class PlanStatus(str, Enum):
    DRAFT = "draft"
    READY = "ready"
    APPROVED = "approved"
    EXECUTING = "executing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

class CommandBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    MISSING_OBJECTIVE = "missing_objective_id"
    MISSING_PLAN = "missing_plan_id"
    MISSING_COMMAND = "missing_command_id"
    DUPLICATE_COMMAND = "duplicate_command"
    INVALID_TARGET = "invalid_target"
    INVALID_PRIORITY = "invalid_priority"
    INVALID_CAPACITY = "invalid_capacity"
    INVALID_CONSTRAINT = "invalid_constraint"
    TERMINAL = "terminal"
    ADVISORY_ONLY = "advisory_only"

PRIORITIES = ("low", "medium", "high", "critical")
TERMINAL = frozenset({PlanStatus.COMPLETED, PlanStatus.CANCELLED})

@dataclass(frozen=True)
class PlanningConstraint:
    name: str
    value: Any
    hard: bool = True

@dataclass(frozen=True)
class BusinessObjective:
    objective_id: str
    workspace_id: str
    title: str
    target: float
    unit: str
    priority: str = "medium"
    horizon_days: int = 30
    constraints: tuple[PlanningConstraint, ...] = ()

@dataclass(frozen=True)
class PlanItem:
    item_id: str
    objective_id: str
    workspace_id: str
    title: str
    priority: str
    rationale: str
    expected_impact: float
    effort: float
    risk: str
    dependencies: tuple[str, ...] = ()
    governed_workflow_id: Optional[str] = None
    executable: bool = False

    def fingerprint(self) -> str:
        payload = {**self.__dict__}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()

@dataclass(frozen=True)
class BusinessPlan:
    plan_id: str
    workspace_id: str
    objective_id: str
    status: PlanStatus
    items: tuple[PlanItem, ...]
    capacity_budget: float
    capacity_used: float
    unmet_capacity: float
    tradeoffs: tuple[str, ...]
    evidence: tuple[str, ...]
    version: int = 1

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps({
            "plan_id": self.plan_id, "workspace_id": self.workspace_id,
            "objective_id": self.objective_id, "status": self.status.value,
            "items": [i.fingerprint() for i in self.items],
            "capacity_budget": self.capacity_budget, "capacity_used": self.capacity_used,
            "unmet_capacity": self.unmet_capacity, "tradeoffs": self.tradeoffs,
            "evidence": self.evidence, "version": self.version,
        }, sort_keys=True, default=str).encode()).hexdigest()

@dataclass(frozen=True)
class PlanningResult:
    accepted: bool
    plan: BusinessPlan
    reasons: tuple[str, ...] = ()
    execution: Any = None

class BusinessCommandPlanning:
    VERSION = "v70.10"
    def __init__(self, workspace_id: str, *, operating_layer=None):
        ws = str(workspace_id or "").strip()
        if not ws: raise ValueError("workspace_id is required")
        if operating_layer is not None and getattr(operating_layer, "workspace_id", ws) != ws:
            raise ValueError(CommandBlock.TENANT_MISMATCH.value)
        self.workspace_id = ws
        self.operating_layer = operating_layer
        self._plans: dict[str, BusinessPlan] = {}
        self._commands: set[str] = set()

    def create_plan(self, *, objective: BusinessObjective, signals: Mapping[str, Any] = None,
                    capacity_budget: float, evidence: tuple[str, ...] = ()) -> PlanningResult:
        self._validate_objective(objective)
        if capacity_budget < 0: raise ValueError(CommandBlock.INVALID_CAPACITY.value)
        signals = dict(signals or {})
        items = self._derive_items(objective, signals)
        selected = []
        used = 0.0
        tradeoffs = []
        for item in sorted(items, key=lambda x: (-self._priority_weight(x.priority), -x.expected_impact, x.item_id)):
            if used + item.effort <= capacity_budget:
                selected.append(item); used += item.effort
            else:
                tradeoffs.append(f"deferred:{item.item_id}:capacity")
        plan_id = "kdo:plan:v70.10:" + hashlib.sha256(
            json.dumps({"workspace_id": self.workspace_id, "objective_id": objective.objective_id,
                        "signals": signals, "capacity_budget": capacity_budget,
                        "items": [x.item_id for x in selected]}, sort_keys=True, default=str).encode()
        ).hexdigest()[:32]
        plan = BusinessPlan(plan_id, self.workspace_id, objective.objective_id, PlanStatus.READY,
                            tuple(selected), float(capacity_budget), used, max(0.0, sum(x.effort for x in items)-used),
                            tuple(tradeoffs), tuple(evidence), 1)
        self._plans[plan_id] = plan
        reasons = [f"objective:{objective.objective_id}", f"items:{len(selected)}"]
        if tradeoffs: reasons.append("capacity_tradeoffs_present")
        reasons.append("planning_is_advisory")
        return PlanningResult(True, plan, tuple(reasons))

    async def command(self, *, plan_id: str, command_id: str, item_id: str,
                      approved: bool = False, now=None) -> PlanningResult:
        if command_id in self._commands: raise ValueError(CommandBlock.DUPLICATE_COMMAND.value)
        plan = self._plans.get(str(plan_id))
        if plan is None: raise ValueError(CommandBlock.MISSING_PLAN.value)
        item = next((x for x in plan.items if x.item_id == item_id), None)
        if item is None: raise ValueError(CommandBlock.INVALID_TARGET.value)
        if plan.status in TERMINAL: raise ValueError(CommandBlock.TERMINAL.value)
        if not item.executable or not item.governed_workflow_id or self.operating_layer is None:
            raise ValueError(CommandBlock.ADVISORY_ONLY.value)
        # Authority remains with BusinessOperatingLayer; this method merely hands off
        # an explicitly selected item after a command identity is recorded.
        event = {"event_id": command_id, "event_name": "business.plan.command",
                 "workspace_id": self.workspace_id, "plan_id": plan.plan_id,
                 "objective_id": plan.objective_id, "item_id": item.item_id,
                 "command_title": item.title, "expected_impact": item.expected_impact,
                 "risk": item.risk}
        self._commands.add(command_id)
        run = await self.operating_layer.handle_event(plan.objective_id, item.governed_workflow_id,
                                                       event=event, approved=approved, now=now)
        return PlanningResult(True, plan, ("governed_command_dispatched",), run)

    def get_plan(self, plan_id: str) -> Optional[BusinessPlan]:
        return self._plans.get(str(plan_id))

    def _derive_items(self, objective: BusinessObjective, signals: Mapping[str, Any]) -> list[PlanItem]:
        raw = signals.get("actions") or []
        if not isinstance(raw, (list, tuple)): raise ValueError(CommandBlock.INVALID_TARGET.value)
        items=[]
        for idx, raw_item in enumerate(raw):
            if not isinstance(raw_item, Mapping): raise ValueError(CommandBlock.INVALID_TARGET.value)
            title=str(raw_item.get("title") or "").strip()
            if not title: raise ValueError(CommandBlock.INVALID_TARGET.value)
            priority=str(raw_item.get("priority") or objective.priority).lower()
            if priority not in PRIORITIES: raise ValueError(CommandBlock.INVALID_PRIORITY.value)
            effort=float(raw_item.get("effort", 1))
            impact=float(raw_item.get("expected_impact", 0))
            if effort <= 0: raise ValueError(CommandBlock.INVALID_CAPACITY.value)
            risk=str(raw_item.get("risk") or "medium").lower()
            workflow=raw_item.get("governed_workflow_id")
            executable=bool(raw_item.get("executable", False))
            # Planning cannot manufacture authority: executable requires an explicit governed workflow.
            if executable and not workflow: raise ValueError(CommandBlock.INVALID_TARGET.value)
            item_id=f"{objective.objective_id}:item:{idx+1}"
            items.append(PlanItem(item_id, objective.objective_id, self.workspace_id, title, priority,
                                  str(raw_item.get("rationale") or "Explicit planning input."), impact, effort, risk,
                                  tuple(str(x) for x in (raw_item.get("dependencies") or ())),
                                  str(workflow) if workflow else None, executable))
        return items

    def _validate_objective(self, objective: BusinessObjective):
        if not objective.objective_id.strip(): raise ValueError(CommandBlock.MISSING_OBJECTIVE.value)
        if objective.workspace_id != self.workspace_id: raise ValueError(CommandBlock.TENANT_MISMATCH.value)
        if not objective.title.strip() or objective.horizon_days <= 0: raise ValueError(CommandBlock.INVALID_TARGET.value)
        if objective.priority not in PRIORITIES: raise ValueError(CommandBlock.INVALID_PRIORITY.value)
        for c in objective.constraints:
            if not str(c.name).strip(): raise ValueError(CommandBlock.INVALID_CONSTRAINT.value)

    @staticmethod
    def _priority_weight(p: str) -> int:
        return {"low": 1, "medium": 2, "high": 3, "critical": 4}[p]

__all__=["BusinessCommandPlanning","BusinessObjective","PlanningConstraint","PlanItem","BusinessPlan","PlanningResult","PlanStatus","CommandBlock"]
