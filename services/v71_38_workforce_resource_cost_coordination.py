"""Phase 7.27 — Resource & Cost-Aware Workforce Coordination.

Adds bounded resource/cost feasibility to the existing Phase 7.26 multi-worker
planner. Canonical worker selection remains authoritative for eligibility and
ranking; this layer only constrains/annotates a plan. It never authorizes,
assigns capacity, executes work, calls providers, or invokes the Action Kernel.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional
import hashlib, json, sqlite3

from services.v71_37_workforce_multi_worker_planning import (
    CoordinationTaskSpec, PlannedWorkerTask, MultiWorkerPlan, PlanState,
    WorkforceMultiWorkerPlanner, PlanningBlock,
)

class ResourceCostBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    INVALID_POLICY = "invalid_policy"
    INVALID_ESTIMATE = "invalid_estimate"
    TASK_COST_EXCEEDED = "task_cost_exceeded"
    COST_OVERCOMMIT = "cost_overcommit"
    RESOURCE_OVERCOMMIT = "resource_overcommit"
    PLAN_TAMPERED = "plan_tampered"
    AUTHORITY_VIOLATION = "authority_violation"
    DUPLICATE_PLAN = "duplicate_plan"

@dataclass(frozen=True)
class ResourceCostEstimate:
    task_id: str
    estimated_cost: float = 0.0
    resource_units: int = 1
    duration_minutes: int = 0
    risk_units: float = 0.0

    def payload(self) -> dict[str, Any]:
        return {"task_id": self.task_id, "estimated_cost": round(self.estimated_cost, 6),
                "resource_units": self.resource_units, "duration_minutes": self.duration_minutes,
                "risk_units": round(self.risk_units, 6)}

@dataclass(frozen=True)
class ResourceCostPolicy:
    max_total_cost: float
    max_total_resource_units: int
    max_total_duration_minutes: int = 0
    max_total_risk_units: float = 0.0
    max_task_cost: Optional[float] = None
    cost_tolerance: float = 0.0
    version: str = "1"

    def validate(self) -> None:
        if self.max_total_cost < 0 or self.max_total_resource_units < 1 or self.max_total_duration_minutes < 0 or self.max_total_risk_units < 0 or self.cost_tolerance < 0:
            raise ValueError(ResourceCostBlock.INVALID_POLICY.value)
        if self.max_task_cost is not None and self.max_task_cost < 0:
            raise ValueError(ResourceCostBlock.INVALID_POLICY.value)

@dataclass(frozen=True)
class ResourceCostPlan:
    plan_id: str
    workspace_id: str
    base_plan_id: str
    policy_version: str
    estimates: tuple[ResourceCostEstimate, ...]
    total_cost: float
    total_resource_units: int
    total_duration_minutes: int
    total_risk_units: float
    feasible: bool
    advisory_only: bool = True
    authority_granted: bool = False
    execution_requested: bool = False
    provider_called: bool = False
    action_kernel_invoked: bool = False
    fingerprint: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return {"plan_id": self.plan_id, "workspace_id": self.workspace_id, "base_plan_id": self.base_plan_id,
                "policy_version": self.policy_version, "estimates": [e.payload() for e in self.estimates],
                "total_cost": round(self.total_cost, 6), "total_resource_units": self.total_resource_units,
                "total_duration_minutes": self.total_duration_minutes, "total_risk_units": round(self.total_risk_units, 6),
                "feasible": self.feasible, "advisory_only": self.advisory_only,
                "authority_granted": self.authority_granted, "execution_requested": self.execution_requested,
                "provider_called": self.provider_called, "action_kernel_invoked": self.action_kernel_invoked}

    def calculate_fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()

class WorkforceResourceCostCoordinator:
    VERSION = "phase7.27"
    def __init__(self, workspace_id: str, planner: WorkforceMultiWorkerPlanner, db_path: str = ":memory:"):
        ws = str(workspace_id or "").strip()
        if not ws or planner.workspace_id != ws:
            raise ValueError(ResourceCostBlock.TENANT_MISMATCH.value)
        self.workspace_id = ws
        self.planner = planner
        self._db = sqlite3.connect(db_path)
        self._db.execute("""CREATE TABLE IF NOT EXISTS workforce_resource_cost_plans(
            plan_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, payload TEXT NOT NULL,
            fingerprint TEXT NOT NULL, UNIQUE(workspace_id, plan_id))""")
        self._db.commit()

    def evaluate(self, *, plan: MultiWorkerPlan, policy: ResourceCostPolicy,
                 estimates: tuple[ResourceCostEstimate, ...]) -> ResourceCostPlan:
        policy.validate()
        self.planner.assert_integrity(plan)
        if plan.workspace_id != self.workspace_id:
            raise ValueError(ResourceCostBlock.TENANT_MISMATCH.value)
        by_id = {e.task_id: e for e in estimates}
        task_ids = {t.task_id for t in plan.tasks}
        if set(by_id) != task_ids:
            raise ValueError(ResourceCostBlock.INVALID_ESTIMATE.value)
        for e in estimates:
            if e.estimated_cost < 0 or e.resource_units < 1 or e.duration_minutes < 0 or e.risk_units < 0:
                raise ValueError(ResourceCostBlock.INVALID_ESTIMATE.value)
            if policy.max_task_cost is not None and e.estimated_cost > policy.max_task_cost + policy.cost_tolerance:
                raise ValueError(ResourceCostBlock.TASK_COST_EXCEEDED.value)
        total_cost = sum(e.estimated_cost for e in estimates)
        total_resource = sum(e.resource_units for e in estimates)
        total_duration = sum(e.duration_minutes for e in estimates)
        total_risk = sum(e.risk_units for e in estimates)
        feasible = (total_cost <= policy.max_total_cost + policy.cost_tolerance and
                    total_resource <= policy.max_total_resource_units and
                    (policy.max_total_duration_minutes == 0 or total_duration <= policy.max_total_duration_minutes) and
                    (policy.max_total_risk_units == 0 or total_risk <= policy.max_total_risk_units))
        if not feasible:
            # Return an auditable bounded result instead of silently mutating/replanning.
            pass
        result = ResourceCostPlan(plan.plan_id, self.workspace_id, plan.plan_id, policy.version, tuple(sorted(estimates, key=lambda x: x.task_id)),
                                  round(total_cost, 6), total_resource, total_duration, round(total_risk, 6), feasible)
        result = ResourceCostPlan(**{**result.__dict__, "fingerprint": result.calculate_fingerprint()})
        if self._db.execute("SELECT 1 FROM workforce_resource_cost_plans WHERE plan_id=?", (plan.plan_id,)).fetchone():
            raise ValueError(ResourceCostBlock.DUPLICATE_PLAN.value)
        self._db.execute("INSERT INTO workforce_resource_cost_plans VALUES (?,?,?,?)",
                         (result.plan_id, result.workspace_id, json.dumps(result.canonical_payload(), sort_keys=True), result.fingerprint))
        self._db.commit()
        return result

    def get(self, plan_id: str) -> Optional[ResourceCostPlan]:
        row = self._db.execute("SELECT payload,fingerprint FROM workforce_resource_cost_plans WHERE plan_id=? AND workspace_id=?", (str(plan_id), self.workspace_id)).fetchone()
        if not row:
            return None
        p = json.loads(row[0]); p["estimates"] = tuple(ResourceCostEstimate(**e) for e in p["estimates"]); p["fingerprint"] = row[1]
        return ResourceCostPlan(**p)

    def assert_integrity(self, result: ResourceCostPlan) -> None:
        if result.workspace_id != self.workspace_id or result.fingerprint != result.calculate_fingerprint():
            raise ValueError(ResourceCostBlock.PLAN_TAMPERED.value)
        stored = self.get(result.plan_id)
        if stored is None or stored.fingerprint != result.fingerprint:
            raise ValueError(ResourceCostBlock.PLAN_TAMPERED.value)
        if not result.advisory_only or result.authority_granted or result.execution_requested or result.provider_called or result.action_kernel_invoked:
            raise ValueError(ResourceCostBlock.AUTHORITY_VIOLATION.value)
        self.planner.assert_integrity(self.planner.get(result.base_plan_id))

    def context(self, plan_id: str) -> Mapping[str, Any]:
        result = self.get(plan_id)
        if result is None:
            raise ValueError(ResourceCostBlock.PLAN_TAMPERED.value)
        self.assert_integrity(result)
        return {"plan_id": result.plan_id, "base_plan_id": result.base_plan_id,
                "feasible": result.feasible, "total_cost": result.total_cost,
                "total_resource_units": result.total_resource_units,
                "total_duration_minutes": result.total_duration_minutes,
                "total_risk_units": result.total_risk_units,
                "advisory_only": True, "authority": "none", "execution_requested": False}

    def close(self):
        self._db.close()

__all__ = ["ResourceCostBlock", "ResourceCostEstimate", "ResourceCostPolicy", "ResourceCostPlan", "WorkforceResourceCostCoordinator"]
