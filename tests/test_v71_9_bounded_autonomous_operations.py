import pytest
from services.v71_1_agent_identity import AgentIdentityRegistry, AgentStatus
from services.v71_2_agent_capability_permissions import AgentCapabilityPermissionRegistry
from services.v71_4_agent_planning import AgentPlanningRegistry, PlanStep
from services.v71_5_agent_evaluation import AgentEvaluationRegistry
from services.v71_6_agent_budgets import AgentBudgetRegistry
from services.v71_7_human_takeover import HumanTakeoverRegistry
from services.v71_9_bounded_autonomous_operations import (
    BoundedAutonomousOperationsRegistry, AutonomousRunStatus,
    AutonomousRunDisposition, AutonomousRunBlock,
)
from services.business_autonomy_envelope import BusinessAutonomyEnvelope, AutonomyMode, SuccessMetric, RiskBoundary, EnvelopeState


def setup():
    ids = AgentIdentityRegistry("ws-1")
    ids.register(agent_id="a1", name="worker", purpose="business work", declared_capabilities=("crm.read",))
    ids.transition(agent_id="a1", status=AgentStatus.ACTIVE)
    perms = AgentCapabilityPermissionRegistry("ws-1", ids)
    perms.grant(permission_id="p1", agent_id="a1", capability="crm.read", scope={"workspace_id": "ws-1"})
    plans = AgentPlanningRegistry("ws-1", ids, perms)
    plan = plans.create_plan(plan_id="p1", agent_id="a1", objective="inspect", steps=(
        PlanStep("s1", "Read CRM", "crm.read", "inspect CRM state", requested_scope={"workspace_id": "ws-1"}),
    ))
    # Planning assessment marks the executable candidate and makes the plan READY.
    plans.assess("p1")
    evals = AgentEvaluationRegistry("ws-1", ids, plans)
    budgets = AgentBudgetRegistry("ws-1", ids)
    budgets.create(budget_id="b1", agent_id="a1", max_actions=2)
    takeovers = HumanTakeoverRegistry("ws-1", ids)
    reg = BoundedAutonomousOperationsRegistry("ws-1", ids, perms, plans, evals, budgets, takeovers)
    envelope = BusinessAutonomyEnvelope(
        workspace_id="ws-1", objective="inspect", success_metrics=(SuccessMetric("inspection", ">=", 1),), allowed_actions=("crm.read",), risk_boundary=RiskBoundary(max_risk="high"), mode=AutonomyMode.EXECUTE, state=EnvelopeState.ACTIVE,
    )
    workflow = {"id": "sales.qualified-lead-status", "action": "crm.read", "risk": "low", "trigger": "lead.qualified"}
    return reg, envelope, workflow, budgets, takeovers, plan


def test_admission_is_bounded_and_does_not_grant_authority():
    reg, envelope, workflow, *_ = setup()
    reg.create(run_id="r1", agent_id="a1", plan_id="p1", objective="inspect", workflow_id=workflow["id"], trigger=workflow["trigger"], event={"event_id": "e1"}, budget_id="b1")
    result = reg.admit("r1", envelope=envelope, workflow=workflow, risk="low")
    assert result.status == AutonomousRunStatus.READY
    assert result.disposition == AutonomousRunDisposition.EXECUTION_CANDIDATE
    assert result.authority_granted is False
    assert result.execution_requested is False
    assert reg.context("r1")["authority_granted"] is False


def test_missing_capability_blocks_instead_of_inventing_work():
    reg, envelope, workflow, *_ = setup()
    workflow = {**workflow, "action": "email.send"}
    reg.create(run_id="r2", agent_id="a1", plan_id="p1", objective="send", workflow_id=workflow["id"], trigger=workflow["trigger"], budget_id="b1")
    result = reg.admit("r2", envelope=envelope, workflow=workflow, risk="low")
    assert result.status == AutonomousRunStatus.BLOCKED
    assert "action_not_declared" in result.reasons or "workflow" in " ".join(result.reasons)


def test_pending_human_takeover_blocks_autonomy():
    reg, envelope, workflow, _, takeovers, _ = setup()
    takeovers.create(case_id="t1", agent_id="a1", reason="needs review", trigger="risk")
    reg.create(run_id="r3", agent_id="a1", plan_id="p1", objective="inspect", workflow_id=workflow["id"], trigger=workflow["trigger"], budget_id="b1")
    result = reg.admit("r3", envelope=envelope, workflow=workflow, risk="low")
    assert result.status == AutonomousRunStatus.BLOCKED
    assert AutonomousRunBlock.TAKEOVER_PENDING.value in result.reasons


def test_high_risk_never_becomes_autonomously_admitted():
    reg, envelope, workflow, *_ = setup()
    workflow = {**workflow, "risk": "high"}
    reg.create(run_id="r4", agent_id="a1", plan_id="p1", objective="inspect", workflow_id=workflow["id"], trigger=workflow["trigger"], budget_id="b1")
    result = reg.admit("r4", envelope=envelope, workflow=workflow, risk="high")
    assert result.status == AutonomousRunStatus.BLOCKED
    assert result.disposition == AutonomousRunDisposition.GOVERNANCE_REVIEW_REQUIRED


def test_tamper_and_terminal_replay_are_blocked():
    reg, envelope, workflow, *_ = setup()
    reg.create(run_id="r5", agent_id="a1", plan_id="p1", objective="inspect", workflow_id=workflow["id"], trigger=workflow["trigger"], budget_id="b1")
    run = reg.get("r5")
    object.__setattr__(run, "objective", "tampered")
    with pytest.raises(ValueError, match=AutonomousRunBlock.RUN_TAMPERED.value):
        reg.admit("r5", envelope=envelope, workflow=workflow, risk="low")

    reg2, *_ = setup()
    reg2.create(run_id="r6", agent_id="a1", plan_id="p1", objective="inspect", workflow_id=workflow["id"], trigger=workflow["trigger"], budget_id="b1")
    reg2.cancel("r6")
    with pytest.raises(ValueError, match=AutonomousRunBlock.TERMINAL.value):
        reg2.cancel("r6")
