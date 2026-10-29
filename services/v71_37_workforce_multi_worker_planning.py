"""Phase 7.26 — Governed Workforce Coordination & Multi-Worker Planning.

Creates an objective-bound, dependency-aware coordination plan for multiple
workers. It reuses the canonical qualification-aware selector for each task;
it never authorizes, assigns capacity, executes work, calls providers, or
invokes the Action Kernel.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional
import hashlib, json, sqlite3

from services.v71_29_reputation_aware_worker_selection import ReputationAwareWorkerSelectionRegistry
from services.v71_35_worker_capability_qualification import WorkerCapabilityQualificationRegistry

class PlanningBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    INVALID_OBJECTIVE = "invalid_objective"
    INVALID_TASK = "invalid_task"
    DUPLICATE_TASK = "duplicate_task"
    DEPENDENCY_MISSING = "dependency_missing"
    DEPENDENCY_CYCLE = "dependency_cycle"
    RESOURCE_OVERCOMMIT = "resource_overcommit"
    NO_QUALIFIED_WORKER = "no_qualified_worker"
    SELECTION_TAMPERED = "selection_tampered"
    PLAN_TAMPERED = "plan_tampered"
    DUPLICATE_PLAN = "duplicate_plan"
    AUTHORITY_VIOLATION = "authority_violation"

class PlanState(str, Enum):
    PROPOSED = "proposed"
    READY = "ready"
    BLOCKED = "blocked"

@dataclass(frozen=True)
class CoordinationTaskSpec:
    task_id: str
    capability: str
    title: str
    depends_on: tuple[str, ...] = ()
    requested_scope: Mapping[str, Any] = field(default_factory=dict)
    resource_units: int = 1
    candidate_worker_ids: tuple[str, ...] = ()

    def payload(self) -> dict[str, Any]:
        return {"task_id":self.task_id,"capability":self.capability,"title":self.title,
                "depends_on":list(self.depends_on),"requested_scope":dict(self.requested_scope),
                "resource_units":self.resource_units,"candidate_worker_ids":list(self.candidate_worker_ids)}

@dataclass(frozen=True)
class PlannedWorkerTask:
    task_id: str
    worker_id: str
    capability: str
    title: str
    depends_on: tuple[str, ...]
    resource_units: int
    selection_id: str
    selection_fingerprint: str
    qualification_id: str
    state: PlanState = PlanState.PROPOSED

    def payload(self) -> dict[str, Any]:
        return {"task_id":self.task_id,"worker_id":self.worker_id,"capability":self.capability,
                "title":self.title,"depends_on":list(self.depends_on),"resource_units":self.resource_units,
                "selection_id":self.selection_id,"selection_fingerprint":self.selection_fingerprint,
                "qualification_id":self.qualification_id,"state":self.state.value}

@dataclass(frozen=True)
class MultiWorkerPlan:
    plan_id: str
    workspace_id: str
    objective_id: str
    cycle_id: str
    coordinator_worker_id: str
    tasks: tuple[PlannedWorkerTask, ...]
    resource_budget: int
    state: PlanState
    advisory_only: bool = True
    authority_granted: bool = False
    execution_requested: bool = False
    provider_called: bool = False
    action_kernel_invoked: bool = False
    plan_fingerprint: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return {"plan_id":self.plan_id,"workspace_id":self.workspace_id,"objective_id":self.objective_id,
                "cycle_id":self.cycle_id,"coordinator_worker_id":self.coordinator_worker_id,
                "tasks":[t.payload() for t in self.tasks],"resource_budget":self.resource_budget,
                "state":self.state.value,"advisory_only":self.advisory_only,"authority_granted":self.authority_granted,
                "execution_requested":self.execution_requested,"provider_called":self.provider_called,
                "action_kernel_invoked":self.action_kernel_invoked}
    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.canonical_payload(),sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()

class WorkforceMultiWorkerPlanner:
    VERSION = "phase7.26"
    def __init__(self, workspace_id: str, selector: ReputationAwareWorkerSelectionRegistry,
                 qualification: WorkerCapabilityQualificationRegistry, db_path: str = ":memory:"):
        ws=str(workspace_id or "").strip()
        if not ws or selector.workspace_id != ws or qualification.workspace_id != ws:
            raise ValueError(PlanningBlock.TENANT_MISMATCH.value)
        self.workspace_id=ws; self.selector=selector; self.qualification=qualification
        self._db=sqlite3.connect(db_path)
        self._db.execute("""CREATE TABLE IF NOT EXISTS workforce_multi_worker_plans(
            plan_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, payload TEXT NOT NULL,
            fingerprint TEXT NOT NULL, UNIQUE(workspace_id, plan_id))""")
        self._db.commit()

    def _validate_specs(self, specs: tuple[CoordinationTaskSpec,...], budget: int) -> None:
        if not specs or not str(budget).isdigit() or int(budget) < 1: raise ValueError(PlanningBlock.INVALID_OBJECTIVE.value)
        ids=[s.task_id for s in specs]
        if any(not str(i).strip() for i in ids): raise ValueError(PlanningBlock.INVALID_TASK.value)
        if len(ids)!=len(set(ids)): raise ValueError(PlanningBlock.DUPLICATE_TASK.value)
        known=set(ids); graph={s.task_id:set(s.depends_on) for s in specs}
        for s in specs:
            if not s.capability.strip() or not s.title.strip() or s.resource_units<1: raise ValueError(PlanningBlock.INVALID_TASK.value)
            if any(d not in known or d==s.task_id for d in s.depends_on): raise ValueError(PlanningBlock.DEPENDENCY_MISSING.value)
        visiting=set(); visited=set()
        def visit(n):
            if n in visiting: raise ValueError(PlanningBlock.DEPENDENCY_CYCLE.value)
            if n in visited:return
            visiting.add(n)
            for d in graph[n]:visit(d)
            visiting.remove(n);visited.add(n)
        for n in graph:visit(n)
        if sum(s.resource_units for s in specs)>int(budget): raise ValueError(PlanningBlock.RESOURCE_OVERCOMMIT.value)

    def plan(self, *, plan_id: str, objective_id: str, cycle_id: str, coordinator_worker_id: str,
             specs: tuple[CoordinationTaskSpec,...], resource_budget: int) -> MultiWorkerPlan:
        if not str(objective_id or "").strip() or not str(cycle_id or "").strip() or not str(coordinator_worker_id or "").strip():
            raise ValueError(PlanningBlock.INVALID_OBJECTIVE.value)
        if self._db.execute("SELECT 1 FROM workforce_multi_worker_plans WHERE plan_id=?",(str(plan_id),)).fetchone():
            raise ValueError(PlanningBlock.DUPLICATE_PLAN.value)
        self._validate_specs(specs,int(resource_budget))
        planned=[]
        for spec in sorted(specs,key=lambda s:s.task_id):
            candidates=tuple(sorted(set(spec.candidate_worker_ids))) if spec.candidate_worker_ids else None
            try:
                sel=self.selector.select(selection_id=f"{plan_id}:{spec.task_id}",task_id=spec.task_id,capability=spec.capability,
                    requested_scope=spec.requested_scope,candidate_agent_ids=candidates)
            except ValueError as exc:
                raise ValueError(PlanningBlock.NO_QUALIFIED_WORKER.value) from exc
            if not sel.selected_agent_id: raise ValueError(PlanningBlock.NO_QUALIFIED_WORKER.value)
            self.selector.assert_integrity(sel)
            q=self.qualification.get_for_worker(sel.selected_agent_id,spec.capability)
            if q is None or not self.qualification.is_currently_qualified(sel.selected_agent_id,spec.capability):
                raise ValueError(PlanningBlock.NO_QUALIFIED_WORKER.value)
            planned.append(PlannedWorkerTask(spec.task_id,sel.selected_agent_id,spec.capability,spec.title,spec.depends_on,
                spec.resource_units,sel.selection_id,sel.selection_fingerprint,q.qualification_id,PlanState.PROPOSED))
        state=PlanState.READY if planned else PlanState.BLOCKED
        rec=MultiWorkerPlan(str(plan_id),self.workspace_id,str(objective_id),str(cycle_id),str(coordinator_worker_id),tuple(planned),int(resource_budget),state)
        rec=MultiWorkerPlan(**{**rec.__dict__,"plan_fingerprint":rec.fingerprint()})
        self._db.execute("INSERT INTO workforce_multi_worker_plans VALUES (?,?,?,?)",(rec.plan_id,rec.workspace_id,json.dumps(rec.canonical_payload(),sort_keys=True),rec.plan_fingerprint));self._db.commit()
        return rec

    def get(self, plan_id: str) -> Optional[MultiWorkerPlan]:
        row=self._db.execute("SELECT payload,fingerprint FROM workforce_multi_worker_plans WHERE plan_id=? AND workspace_id=?",(str(plan_id),self.workspace_id)).fetchone()
        if not row:return None
        p=json.loads(row[0]); p["state"]=PlanState(p["state"]); p["tasks"]=tuple(PlannedWorkerTask(**{**t,"depends_on":tuple(t["depends_on"]),"state":PlanState(t["state"])}) for t in p["tasks"]); p["plan_fingerprint"]=row[1]
        return MultiWorkerPlan(**p)

    def assert_integrity(self, plan: MultiWorkerPlan) -> None:
        if plan.workspace_id!=self.workspace_id: raise ValueError(PlanningBlock.TENANT_MISMATCH.value)
        if plan.plan_fingerprint!=plan.fingerprint(): raise ValueError(PlanningBlock.PLAN_TAMPERED.value)
        if not plan.advisory_only or plan.authority_granted or plan.execution_requested or plan.provider_called or plan.action_kernel_invoked:
            raise ValueError(PlanningBlock.AUTHORITY_VIOLATION.value)
        stored=self.get(plan.plan_id)
        if stored is None or stored.plan_fingerprint!=plan.plan_fingerprint: raise ValueError(PlanningBlock.PLAN_TAMPERED.value)
        for t in plan.tasks:
            sel=self.selector.get(t.selection_id)
            if sel is None or sel.selection_fingerprint!=t.selection_fingerprint or sel.selected_agent_id!=t.worker_id:
                raise ValueError(PlanningBlock.SELECTION_TAMPERED.value)
            if not self.qualification.is_currently_qualified(t.worker_id,t.capability):
                raise ValueError(PlanningBlock.NO_QUALIFIED_WORKER.value)

    def context(self, plan_id: str) -> Mapping[str,Any]:
        p=self.get(plan_id)
        if p is None: raise ValueError(PlanningBlock.PLAN_TAMPERED.value)
        self.assert_integrity(p)
        return {"plan_id":p.plan_id,"objective_id":p.objective_id,"cycle_id":p.cycle_id,"task_count":len(p.tasks),
                "state":p.state.value,"advisory_only":True,"authority":"none","execution_requested":False}

    def close(self): self._db.close()

__all__=["PlanningBlock","PlanState","CoordinationTaskSpec","PlannedWorkerTask","MultiWorkerPlan","WorkforceMultiWorkerPlanner"]
