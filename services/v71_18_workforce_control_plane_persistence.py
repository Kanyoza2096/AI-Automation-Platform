"""V71.18 — Workforce control-plane persistence and restart convergence.

Rehydrates the existing V71 control registries from their authoritative tables at
workspace runtime creation. This module does not create authority or execution;
it restores the same identity/permission/plan/budget/takeover/coordination/run
objects before a recovered run is admitted.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Mapping
import json, logging

logger = logging.getLogger("kanyoza.v71.18")

@dataclass(frozen=True)
class PersistenceReport:
    workspace_id: str
    hydrated: Mapping[str, int]
    integrity_failures: tuple[str, ...] = ()
    durable: bool = True
    restart_safe: bool = False

class WorkforceControlPlanePersistence:
    VERSION = "v71.18"
    TABLES = (
        "agent_identities", "agent_capability_permissions", "agent_memories",
        "agent_plans", "agent_evaluations", "agent_budgets",
        "agent_human_takeover_cases", "agent_coordination_requests", "agent_autonomous_runs",
    )
    def __init__(self, *, workspace_id: str, db: Any):
        self.workspace_id = str(workspace_id or "").strip()
        if not self.workspace_id: raise ValueError("workspace_id is required")
        if db is None: raise RuntimeError("durable workforce control-plane store required")
        self.db = db

    def _rows(self, table: str):
        result = self.db.table(table).select("*").eq("workspace_id", self.workspace_id).execute()
        return getattr(result, "data", None) or []

    @staticmethod
    def _json(value, default):
        if value is None: return default
        if isinstance(value, str):
            try: return json.loads(value or json.dumps(default))
            except Exception: return default
        return value

    def hydrate(self, runtime: Mapping[str, Any]) -> PersistenceReport:
        """Load durable state into the *existing* V71 registries and verify fingerprints."""
        from services.v71_1_agent_identity import AgentIdentity, AgentStatus
        from services.v71_2_agent_capability_permissions import AgentCapabilityPermission, PermissionEffect
        from services.v71_3_agent_memory import AgentMemory, MemoryKind, MemoryStatus
        from services.v71_4_agent_planning import AgentPlan, PlanStep, PlanStatus, PlanDisposition
        from services.v71_5_agent_evaluation import AgentEvaluation, EvaluationMetrics, EvaluationStatus
        from services.v71_6_agent_budgets import AgentBudget, BudgetStatus
        from services.v71_7_human_takeover import HumanTakeoverCase, TakeoverStatus, TakeoverDisposition
        from services.v71_8_multi_agent_coordination import CoordinationRequest, CoordinationTask, CoordinationStatus, CoordinationDisposition
        from services.v71_9_bounded_autonomous_operations import AutonomousRun, AutonomousRunStatus, AutonomousRunDisposition

        hydrated = {}; failures=[]
        identity = runtime["agent_identity"]
        for r in self._rows("agent_identities"):
            try:
                obj=AgentIdentity(str(r["agent_id"]), self.workspace_id, str(r["name"]), str(r["purpose"]), str(r.get("agent_class") or "business_worker"), str(r.get("version") or "1"), AgentStatus(str(r["status"])), tuple(self._json(r.get("declared_capabilities"), [])), self._json(r.get("metadata"), {}), str(r["identity_fingerprint"]))
                identity.assert_integrity(obj); identity._agents[obj.agent_id]=obj
            except Exception as e: failures.append(f"agent_identity:{r.get('agent_id')}:{type(e).__name__}")
        hydrated["agent_identities"]=len(identity._agents)

        permissions=runtime["agent_permissions"]
        for r in self._rows("agent_capability_permissions"):
            try:
                obj=AgentCapabilityPermission(str(r["permission_id"]), self.workspace_id, str(r["agent_id"]), str(r["capability"]), PermissionEffect(str(r["effect"])), self._json(r.get("scope_json"), {}), str(r.get("version") or "1"), str(r["permission_fingerprint"]))
                permissions.assert_integrity(obj); permissions._permissions[obj.permission_id]=obj
            except Exception as e: failures.append(f"permission:{r.get('permission_id')}:{type(e).__name__}")
        hydrated["agent_capability_permissions"]=len(permissions._permissions)

        memory=runtime["agent_memory"]
        for r in self._rows("agent_memories"):
            try:
                obj=AgentMemory(str(r["memory_id"]), self.workspace_id, str(r["agent_id"]), MemoryKind(str(r["kind"])), str(r["subject"]), str(r["content"]), str(r["source"]), float(r.get("confidence",1.0)), str(r.get("created_at") or ""), r.get("expires_at"), str(r.get("sensitivity") or "normal"), MemoryStatus(str(r["status"])), r.get("supersedes_memory_id"), self._json(r.get("metadata"), {}), str(r["memory_fingerprint"]))
                memory.assert_integrity(obj); memory._memories[obj.memory_id]=obj
            except Exception as e: failures.append(f"memory:{r.get('memory_id')}:{type(e).__name__}")
        hydrated["agent_memories"]=len(memory._memories)

        planning=runtime["agent_planning"]
        for r in self._rows("agent_plans"):
            try:
                steps=[]
                for s in self._json(r.get("steps"), []):
                    steps.append(PlanStep(str(s["step_id"]), str(s["title"]), str(s["capability"]), str(s.get("rationale") or ""), self._json(s.get("requested_scope"), {}), tuple(s.get("depends_on") or []), PlanDisposition(str(s.get("disposition") or "advisory_only")), self._json(s.get("metadata"), {})))
                obj=AgentPlan(str(r["plan_id"]), self.workspace_id, str(r["agent_id"]), str(r["objective"]), tuple(self._json(r.get("constraints"), [])), tuple(steps), PlanStatus(str(r["status"])), str(r.get("version") or "1"), str(r["plan_fingerprint"]))
                if obj.plan_fingerprint != obj.fingerprint(): raise ValueError("plan_tampered")
                planning._plans[obj.plan_id]=obj
            except Exception as e: failures.append(f"plan:{r.get('plan_id')}:{type(e).__name__}")
        hydrated["agent_plans"]=len(planning._plans)

        evaluation=runtime["agent_evaluation"]
        for r in self._rows("agent_evaluations"):
            try:
                metrics=self._json(r.get("metrics"), {})
                obj=AgentEvaluation(str(r["evaluation_id"]), self.workspace_id, str(r["agent_id"]), str(r["plan_id"]), EvaluationStatus(str(r["status"])), EvaluationMetrics(metrics.get("execution_success"), metrics.get("outcome_alignment"), metrics.get("policy_compliance"), metrics.get("reliability"), metrics.get("recovery_efficiency")), self._json(r.get("evidence"), {}), tuple(self._json(r.get("findings"), [])), str(r.get("version") or "1"), str(r["evaluation_fingerprint"]))
                evaluation.assert_integrity(obj); evaluation._evaluations[obj.evaluation_id]=obj
            except Exception as e: failures.append(f"evaluation:{r.get('evaluation_id')}:{type(e).__name__}")
        hydrated["agent_evaluations"]=len(evaluation._evaluations)

        budgets=runtime["agent_budgets"]
        for r in self._rows("agent_budgets"):
            try:
                obj=AgentBudget(str(r["budget_id"]), self.workspace_id, str(r["agent_id"]), None if r.get("max_actions") is None else int(r["max_actions"]), None if r.get("max_cost") is None else float(r["max_cost"]), None if r.get("max_risk_units") is None else float(r["max_risk_units"]), BudgetStatus(str(r["status"])), str(r.get("version") or "1"), self._json(r.get("metadata"), {}), int(r.get("used_actions") or 0), float(r.get("used_cost") or 0), float(r.get("used_risk_units") or 0), str(r["budget_fingerprint"]))
                budgets.assert_integrity(obj); budgets._budgets[obj.budget_id]=obj
            except Exception as e: failures.append(f"budget:{r.get('budget_id')}:{type(e).__name__}")
        hydrated["agent_budgets"]=len(budgets._budgets)

        takeover=runtime["human_takeover"]
        for r in self._rows("agent_human_takeover_cases"):
            try:
                obj=HumanTakeoverCase(str(r["case_id"]), self.workspace_id, str(r["agent_id"]), str(r["reason"]), str(r["trigger"]), self._json(r.get("context_json"), {}), TakeoverStatus(str(r["status"])), r.get("claimed_by"), None if r.get("disposition") is None else TakeoverDisposition(str(r["disposition"])), r.get("human_note"), str(r.get("version") or "1"), str(r["case_fingerprint"]))
                takeover.assert_integrity(obj); takeover._cases[obj.case_id]=obj
            except Exception as e: failures.append(f"takeover:{r.get('case_id')}:{type(e).__name__}")
        hydrated["agent_human_takeover_cases"]=len(takeover._cases)

        coordination=runtime["multi_agent_coordination"]
        for r in self._rows("agent_coordination_requests"):
            try:
                tasks=[]
                for t in self._json(r.get("tasks_json"), []):
                    tasks.append(CoordinationTask(str(t["task_id"]), str(t["title"]), str(t["capability"]), self._json(t.get("requested_scope"), {}), str(t.get("rationale") or ""), tuple(t.get("depends_on") or []), CoordinationDisposition(str(t.get("disposition") or "advisory_only")), self._json(t.get("metadata"), {})))
                obj=CoordinationRequest(str(r["coordination_id"]), self.workspace_id, str(r["coordinator_agent_id"]), str(r["target_agent_id"]), str(r["objective"]), tuple(tasks), CoordinationStatus(str(r["status"])), str(r.get("version") or "1"), str(r["coordination_fingerprint"]))
                coordination.assert_integrity(obj); coordination._requests[obj.coordination_id]=obj
            except Exception as e: failures.append(f"coordination:{r.get('coordination_id')}:{type(e).__name__}:{str(e)[:120]}")
        hydrated["agent_coordination_requests"]=len(coordination._requests)

        operations=runtime["bounded_autonomy"]
        for r in self._rows("agent_autonomous_runs"):
            try:
                obj=AutonomousRun(str(r["run_id"]), self.workspace_id, str(r["agent_id"]), str(r["plan_id"]), str(r["objective"]), str(r["workflow_id"]), str(r["trigger"]), self._json(r.get("event_json"), {}), r.get("budget_id"), r.get("coordination_id"), r.get("takeover_case_id"), AutonomousRunStatus(str(r["status"])), AutonomousRunDisposition(str(r["disposition"])), str(r.get("version") or "1"), self._json(r.get("result_summary_json"), {}), str(r["run_fingerprint"]))
                operations.assert_integrity(obj); operations._runs[obj.run_id]=obj
            except Exception as e: failures.append(f"autonomous_run:{r.get('run_id')}:{type(e).__name__}:{str(e)[:120]}")
        hydrated["agent_autonomous_runs"]=len(operations._runs)

        safe = not failures
        return PersistenceReport(self.workspace_id, hydrated, tuple(failures), True, safe)

    def status(self, report: PersistenceReport | None = None) -> dict[str, Any]:
        return {"version": self.VERSION, "durable": True, "restart_safe": bool(report.restart_safe) if report else False,
                "workspace_id": self.workspace_id, "tables": list(self.TABLES),
                "authority_granted": False, "execution_requested": False,
                "note": "Persistence restores control-plane state; it grants no authority and does not execute providers.",
                "report": report.__dict__ if report else None}


def install_workforce_control_plane_persistence(app: Any, container: Any):
    runtime = getattr(app.state, "production_runtime", None)
    if runtime is None: raise RuntimeError("V71.13 production runtime is required")
    store = None
    try:
        from services.supabase import supabase_service
        db = supabase_service.service_client or supabase_service.client
    except Exception:
        db = None
    # Workspace hydration is intentionally lazy because the workspace/tenant is
    # only known when a runtime is requested. Keep boot metadata separate from
    # the per-workspace persistence object.
    app.state.workforce_control_plane_persistence_installed = bool(db is not None and callable(getattr(db, "table", None)))
    return app.state.workforce_control_plane_persistence_installed
