"""V71.9 — Governed AI Workforce: Bounded Autonomous Business Operations.

This is the bridge between the workforce control planes (identity, permissions,
memory, planning, evaluation, budgets, takeover and coordination) and KDO's
existing governed business-workflow runtime.

It is deliberately an ORCHESTRATOR, not a second executor or governance plane.
Real side effects remain behind BusinessWorkflowAutonomy -> BusinessAutomation
-> Action Kernel -> durable outbox -> governed connector/provider.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional
import hashlib
import json

from services.v71_1_agent_identity import AgentIdentityRegistry, AgentStatus
from services.v71_2_agent_capability_permissions import AgentCapabilityPermissionRegistry
from services.v71_4_agent_planning import AgentPlanningRegistry, PlanStatus, PlanDisposition
from services.v71_5_agent_evaluation import AgentEvaluationRegistry
from services.v71_6_agent_budgets import AgentBudgetRegistry, DurableAgentBudgetReservations, BudgetReservation
from services.v71_7_human_takeover import HumanTakeoverRegistry, TakeoverStatus
from services.v71_8_multi_agent_coordination import MultiAgentCoordinationRegistry
from services.business_autonomy_envelope import BusinessAutonomyEnvelope
from services.business_workflow_autonomy import evaluate_workflow_admission, run_catalog_workflow, WorkflowAdmission


class AutonomousRunStatus(str, Enum):
    PROPOSED = "proposed"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AutonomousRunDisposition(str, Enum):
    EXECUTION_CANDIDATE = "execution_candidate"
    GOVERNANCE_REVIEW_REQUIRED = "governance_review_required"
    ADVISORY_ONLY = "advisory_only"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    BLOCKED = "blocked"


class AutonomousRunBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    AGENT_NOT_FOUND = "agent_not_found"
    AGENT_NOT_ACTIVE = "agent_not_active"
    PLAN_NOT_FOUND = "plan_not_found"
    PLAN_NOT_READY = "plan_not_ready"
    PLAN_BLOCKED = "plan_blocked"
    PLAN_TAMPERED = "plan_tampered"
    RUN_NOT_FOUND = "run_not_found"
    DUPLICATE_RUN = "duplicate_run"
    RUN_TAMPERED = "run_tampered"
    TERMINAL = "autonomous_run_terminal"
    INVALID_RUN = "invalid_autonomous_run"
    WORKFLOW_NOT_FOUND = "workflow_not_found"
    WORKFLOW_BLOCKED = "workflow_blocked"
    GOVERNANCE_REVIEW_REQUIRED = "governance_review_required"
    BUDGET_NOT_FOUND = "budget_not_found"
    BUDGET_BLOCKED = "budget_blocked"
    TAKEOVER_PENDING = "human_takeover_pending"
    COORDINATION_BLOCKED = "coordination_blocked"
    INVALID_TRANSITION = "invalid_autonomous_run_transition"
    EXECUTION_FAILURE = "execution_failure"


TERMINAL = frozenset({AutonomousRunStatus.COMPLETED, AutonomousRunStatus.FAILED, AutonomousRunStatus.CANCELLED})


@dataclass(frozen=True)
class AutonomousRun:
    run_id: str
    workspace_id: str
    agent_id: str
    plan_id: str
    objective: str
    workflow_id: str
    trigger: str
    event: Mapping[str, Any] = field(default_factory=dict)
    budget_id: Optional[str] = None
    coordination_id: Optional[str] = None
    takeover_case_id: Optional[str] = None
    status: AutonomousRunStatus = AutonomousRunStatus.PROPOSED
    disposition: AutonomousRunDisposition = AutonomousRunDisposition.BLOCKED
    version: str = "1"
    result_summary: Mapping[str, Any] = field(default_factory=dict)
    run_fingerprint: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id, "workspace_id": self.workspace_id,
            "agent_id": self.agent_id, "plan_id": self.plan_id,
            "objective": self.objective, "workflow_id": self.workflow_id,
            "trigger": self.trigger, "event": dict(self.event),
            "budget_id": self.budget_id, "coordination_id": self.coordination_id,
            "takeover_case_id": self.takeover_case_id, "status": self.status.value,
            "disposition": self.disposition.value, "version": self.version,
            "result_summary": dict(self.result_summary),
        }

    def fingerprint(self) -> str:
        raw = json.dumps(self.canonical_payload(), sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class AutonomousAdmission:
    run_id: str
    status: AutonomousRunStatus
    disposition: AutonomousRunDisposition
    reasons: tuple[str, ...] = ()
    workflow_admission: Optional[WorkflowAdmission] = None
    authority_granted: bool = False
    execution_requested: bool = False


class BoundedAutonomousOperationsRegistry:
    """Stateful admission/orchestration facade; no provider execution surface."""

    VERSION = "v71.9"

    def __init__(
        self,
        workspace_id: str,
        identity_registry: AgentIdentityRegistry,
        permission_registry: AgentCapabilityPermissionRegistry,
        planning_registry: AgentPlanningRegistry,
        evaluation_registry: Optional[AgentEvaluationRegistry] = None,
        budget_registry: Optional[AgentBudgetRegistry] = None,
        takeover_registry: Optional[HumanTakeoverRegistry] = None,
        coordination_registry: Optional[MultiAgentCoordinationRegistry] = None,
        db: Any = None,
    ):
        ws = str(workspace_id or "").strip()
        registries = (identity_registry, permission_registry, planning_registry,
                      evaluation_registry, budget_registry, takeover_registry, coordination_registry)
        if not ws or any(r is not None and r.workspace_id != ws for r in registries):
            raise ValueError(AutonomousRunBlock.TENANT_MISMATCH.value)
        self.workspace_id = ws
        self.identity_registry = identity_registry
        self.permission_registry = permission_registry
        self.planning_registry = planning_registry
        self.evaluation_registry = evaluation_registry
        self.budget_registry = budget_registry
        self.takeover_registry = takeover_registry
        self.coordination_registry = coordination_registry
        self.db = db
        self.durable_budget_reservations = (DurableAgentBudgetReservations(db=db, workspace_id=ws) if db is not None else None)
        self._budget_reservations: dict[str, BudgetReservation] = {}
        self._runs: dict[str, AutonomousRun] = {}

    def _active_agent(self, agent_id: str):
        agent = self.identity_registry.get(str(agent_id))
        if agent is None:
            raise ValueError(AutonomousRunBlock.AGENT_NOT_FOUND.value)
        self.identity_registry.assert_integrity(agent)
        if agent.status != AgentStatus.ACTIVE:
            raise ValueError(AutonomousRunBlock.AGENT_NOT_ACTIVE.value)
        return agent

    def get(self, run_id: str) -> Optional[AutonomousRun]:
        return self._runs.get(str(run_id))

    def assert_integrity(self, run: AutonomousRun) -> None:
        if run.workspace_id != self.workspace_id:
            raise ValueError(AutonomousRunBlock.TENANT_MISMATCH.value)
        if run.run_fingerprint != run.fingerprint():
            raise ValueError(AutonomousRunBlock.RUN_TAMPERED.value)

    def create(
        self, *, run_id: str, agent_id: str, plan_id: str, objective: str,
        workflow_id: str, trigger: str, event: Optional[Mapping[str, Any]] = None,
        budget_id: Optional[str] = None, coordination_id: Optional[str] = None,
        takeover_case_id: Optional[str] = None, version: str = "1",
    ) -> AutonomousRun:
        rid = str(run_id or "").strip()
        if not rid or rid in self._runs:
            raise ValueError(AutonomousRunBlock.DUPLICATE_RUN.value)
        if not str(objective or "").strip() or not str(workflow_id or "").strip() or not str(trigger or "").strip():
            raise ValueError(AutonomousRunBlock.INVALID_RUN.value)
        self._active_agent(agent_id)
        plan = self.planning_registry.get(plan_id)
        if plan is None:
            raise ValueError(AutonomousRunBlock.PLAN_NOT_FOUND.value)
        if plan.workspace_id != self.workspace_id or plan.agent_id != str(agent_id):
            raise ValueError(AutonomousRunBlock.TENANT_MISMATCH.value)
        if plan.plan_fingerprint != plan.fingerprint():
            raise ValueError(AutonomousRunBlock.PLAN_TAMPERED.value)
        if plan.status not in {PlanStatus.READY}:
            raise ValueError(AutonomousRunBlock.PLAN_NOT_READY.value)
        if any(s.disposition == PlanDisposition.CAPABILITY_UNAVAILABLE for s in plan.steps):
            raise ValueError(AutonomousRunBlock.PLAN_BLOCKED.value)
        run = AutonomousRun(
            run_id=rid, workspace_id=self.workspace_id, agent_id=str(agent_id),
            plan_id=str(plan_id), objective=str(objective).strip(),
            workflow_id=str(workflow_id).strip(), trigger=str(trigger).strip(),
            event=dict(event or {}), budget_id=None if budget_id is None else str(budget_id),
            coordination_id=None if coordination_id is None else str(coordination_id),
            takeover_case_id=None if takeover_case_id is None else str(takeover_case_id),
        )
        run = AutonomousRun(**{**run.__dict__, "run_fingerprint": run.fingerprint()})
        self._runs[rid] = run
        return run

    def _replace(self, run: AutonomousRun, **changes: Any) -> AutonomousRun:
        updated = AutonomousRun(**{**run.__dict__, **changes, "run_fingerprint": ""})
        updated = AutonomousRun(**{**updated.__dict__, "run_fingerprint": updated.fingerprint()})
        self._runs[run.run_id] = updated
        return updated

    def _pending_takeover(self, agent_id: str) -> bool:
        if not self.takeover_registry:
            return False
        return bool(self.takeover_registry.pending(agent_id=agent_id))

    def admit(
        self, run_id: str, *, envelope: BusinessAutonomyEnvelope,
        workflow: Mapping[str, Any], risk: str,
        budget_actions: int = 1, budget_cost: float = 0.0, budget_risk_units: float = 0.0,
    ) -> AutonomousAdmission:
        run = self.get(run_id)
        if run is None:
            raise ValueError(AutonomousRunBlock.RUN_NOT_FOUND.value)
        self.assert_integrity(run)
        if run.status in TERMINAL:
            raise ValueError(AutonomousRunBlock.TERMINAL.value)
        self._active_agent(run.agent_id)
        reasons: list[str] = []

        plan = self.planning_registry.get(run.plan_id)
        if plan is None:
            reasons.append(AutonomousRunBlock.PLAN_NOT_FOUND.value)
        else:
            if plan.workspace_id != self.workspace_id or plan.agent_id != run.agent_id:
                reasons.append(AutonomousRunBlock.TENANT_MISMATCH.value)
            if plan.plan_fingerprint != plan.fingerprint():
                reasons.append(AutonomousRunBlock.PLAN_TAMPERED.value)
            if plan.status != PlanStatus.READY:
                reasons.append(AutonomousRunBlock.PLAN_NOT_READY.value)
            if any(s.disposition == PlanDisposition.CAPABILITY_UNAVAILABLE for s in plan.steps):
                reasons.append(AutonomousRunBlock.PLAN_BLOCKED.value)

        if self._pending_takeover(run.agent_id):
            reasons.append(AutonomousRunBlock.TAKEOVER_PENDING.value)

        if self.coordination_registry and run.coordination_id:
            coordination = self.coordination_registry.get(run.coordination_id)
            if coordination is None or coordination.status.value != "accepted":
                reasons.append(AutonomousRunBlock.COORDINATION_BLOCKED.value)

        if self.budget_registry and run.budget_id:
            try:
                budget_check = self.budget_registry.check(
                    run.budget_id, actions=budget_actions, cost=budget_cost, risk_units=budget_risk_units
                )
                if not budget_check.allowed:
                    reasons.extend(AutonomousRunBlock.BUDGET_BLOCKED.value + ":" + r for r in budget_check.reasons)
            except ValueError as exc:
                reasons.append(str(exc))
        elif run.budget_id:
            reasons.append(AutonomousRunBlock.BUDGET_NOT_FOUND.value)

        workflow_admission = evaluate_workflow_admission(
            envelope, workflow, workspace_id=self.workspace_id
        )
        if workflow_admission.admission == WorkflowAdmission.BLOCKED:
            reasons.extend(workflow_admission.reasons)
        if workflow_admission.admission in {WorkflowAdmission.REVIEW_REQUIRED, WorkflowAdmission.SUGGESTED, WorkflowAdmission.OBSERVED}:
            reasons.append(workflow_admission.admission.value)

        if reasons:
            disposition = (AutonomousRunDisposition.GOVERNANCE_REVIEW_REQUIRED
                            if workflow_admission.admission == WorkflowAdmission.REVIEW_REQUIRED and
                            all(r in {"review_required", "high_impact_workflow_requires_existing_governance", "approval_required_by_envelope"} or r.startswith("budget_blocked:") for r in reasons)
                            else AutonomousRunDisposition.BLOCKED)
            self._replace(run, status=AutonomousRunStatus.BLOCKED, disposition=disposition,
                          result_summary={"reasons": list(dict.fromkeys(reasons))})
            return AutonomousAdmission(run_id, AutonomousRunStatus.BLOCKED, disposition,
                                       tuple(dict.fromkeys(reasons)), workflow_admission.admission, False, False)

        self._replace(run, status=AutonomousRunStatus.READY,
                      disposition=AutonomousRunDisposition.EXECUTION_CANDIDATE)
        return AutonomousAdmission(run_id, AutonomousRunStatus.READY,
                                   AutonomousRunDisposition.EXECUTION_CANDIDATE, (),
                                   workflow_admission.admission, False, False)

    async def execute(
        self, run_id: str, *, envelope: BusinessAutonomyEnvelope,
        workflow: Mapping[str, Any], engine: Any, approved: bool = False,
        budget_actions: int = 1, budget_cost: float = 0.0, budget_risk_units: float = 0.0,
    ) -> Any:
        """Enter the existing governed workflow engine; never call a provider here."""
        run = self.get(run_id)
        if run is None:
            raise ValueError(AutonomousRunBlock.RUN_NOT_FOUND.value)
        self.assert_integrity(run)
        admission = self.admit(
            run_id, envelope=envelope, workflow=workflow, risk=str(workflow.get("risk") or "low"),
            budget_actions=budget_actions, budget_cost=budget_cost, budget_risk_units=budget_risk_units,
        )
        if admission.status != AutonomousRunStatus.READY:
            raise ValueError(AutonomousRunBlock.WORKFLOW_BLOCKED.value)
        run = self.get(run_id)
        self._replace(run, status=AutonomousRunStatus.RUNNING)
        reservation = None
        try:
            if self.durable_budget_reservations and run.budget_id:
                reservation = await self.durable_budget_reservations.reserve(
                    budget_id=run.budget_id, run_id=run.run_id, actions=budget_actions,
                    cost=budget_cost, risk_units=budget_risk_units
                )
                self._budget_reservations[run.run_id] = reservation
            decision, result = await run_catalog_workflow(
                envelope, workflow, engine=engine, event=run.event,
                workspace_id=self.workspace_id, approved=approved,
            )
            if decision.admission != WorkflowAdmission.ADMITTED:
                self._replace(self.get(run_id), status=AutonomousRunStatus.BLOCKED,
                              disposition=AutonomousRunDisposition.GOVERNANCE_REVIEW_REQUIRED,
                              result_summary={"workflow_admission": decision.to_dict()})
                raise ValueError(AutonomousRunBlock.GOVERNANCE_REVIEW_REQUIRED.value)
            if reservation is not None:
                await self.durable_budget_reservations.finalize(reservation, commit=True)
                self._budget_reservations.pop(run.run_id, None)
            elif self.budget_registry and run.budget_id:
                self.budget_registry.record_usage(
                    run.budget_id, actions=budget_actions, cost=budget_cost, risk_units=budget_risk_units
                )
            summary = {"workflow_admission": decision.to_dict(), "execution_result": _safe_summary(result)}
            self._replace(self.get(run_id), status=AutonomousRunStatus.COMPLETED,
                          disposition=AutonomousRunDisposition.EXECUTION_CANDIDATE,
                          result_summary=summary)
            return result
        except Exception as exc:
            # A failed/uncertain execution consumes a reservation conservatively;
            # only pre-execution governance rejection should release resources.
            if reservation is not None:
                try:
                    await self.durable_budget_reservations.finalize(reservation, commit=True)
                finally:
                    self._budget_reservations.pop(run.run_id, None)
            current = self.get(run_id)
            if current and current.status not in TERMINAL:
                self._replace(current, status=AutonomousRunStatus.FAILED,
                              result_summary={"error_type": type(exc).__name__, "error": str(exc)})
            raise

    def pause_for_takeover(self, run_id: str, *, case_id: str) -> AutonomousRun:
        run = self.get(run_id)
        if run is None:
            raise ValueError(AutonomousRunBlock.RUN_NOT_FOUND.value)
        self.assert_integrity(run)
        if run.status in TERMINAL:
            raise ValueError(AutonomousRunBlock.TERMINAL.value)
        if not self.takeover_registry:
            raise ValueError(AutonomousRunBlock.TAKEOVER_PENDING.value)
        case = self.takeover_registry.get(case_id)
        if case is None or case.workspace_id != self.workspace_id or case.agent_id != run.agent_id:
            raise ValueError(AutonomousRunBlock.TAKEOVER_PENDING.value)
        if case.status not in {TakeoverStatus.REQUESTED, TakeoverStatus.CLAIMED}:
            raise ValueError(AutonomousRunBlock.INVALID_TRANSITION.value)
        return self._replace(run, status=AutonomousRunStatus.PAUSED,
                             takeover_case_id=str(case_id),
                             result_summary={"takeover_case_id": str(case_id)})

    def cancel(self, run_id: str) -> AutonomousRun:
        run = self.get(run_id)
        if run is None:
            raise ValueError(AutonomousRunBlock.RUN_NOT_FOUND.value)
        self.assert_integrity(run)
        if run.status in TERMINAL:
            raise ValueError(AutonomousRunBlock.TERMINAL.value)
        return self._replace(run, status=AutonomousRunStatus.CANCELLED)

    def context(self, run_id: str) -> Mapping[str, Any]:
        run = self.get(run_id)
        if run is None:
            raise ValueError(AutonomousRunBlock.RUN_NOT_FOUND.value)
        self.assert_integrity(run)
        return {
            "run_id": run.run_id, "workspace_id": run.workspace_id, "agent_id": run.agent_id,
            "plan_id": run.plan_id, "objective": run.objective, "workflow_id": run.workflow_id,
            "trigger": run.trigger, "status": run.status.value, "disposition": run.disposition.value,
            "takeover_case_id": run.takeover_case_id,
            "authority_granted": False, "execution_requested": False,
            "advisory_only": True,
        }

    def list_runs(self, *, agent_id: Optional[str] = None) -> tuple[AutonomousRun, ...]:
        if agent_id is not None:
            self._active_agent(agent_id)
        return tuple(r for r in self._runs.values()
                     if agent_id is None or r.agent_id == str(agent_id))


def _safe_summary(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(k): _safe_summary(v) for k, v in list(value.items())[:50]}
    if isinstance(value, (list, tuple)):
        return [_safe_summary(v) for v in value[:50]]
    return str(value)


__all__ = [
    "AutonomousRunStatus", "AutonomousRunDisposition", "AutonomousRunBlock",
    "AutonomousRun", "AutonomousAdmission", "BoundedAutonomousOperationsRegistry",
]
