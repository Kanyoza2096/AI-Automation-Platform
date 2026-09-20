from services.v71_1_agent_identity import AgentIdentityRegistry, AgentStatus
from services.v71_2_agent_capability_permissions import AgentCapabilityPermissionRegistry
from services.v71_4_agent_planning import AgentPlanningRegistry, PlanStep
from services.v71_5_agent_evaluation import AgentEvaluationRegistry
from services.v71_6_agent_budgets import AgentBudgetRegistry
from services.v71_7_human_takeover import HumanTakeoverRegistry
from services.v71_9_bounded_autonomous_operations import BoundedAutonomousOperationsRegistry
from services.v71_20_autonomous_execution_certification import CertificationStatus, certify_admission_boundaries
from services.business_autonomy_envelope import BusinessAutonomyEnvelope, AutonomyMode, EnvelopeState, RiskBoundary, SuccessMetric


def _setup():
    ids = AgentIdentityRegistry("ws-1")
    ids.register(agent_id="a1", name="worker", purpose="business work", declared_capabilities=("crm.read",))
    ids.transition(agent_id="a1", status=AgentStatus.ACTIVE)
    perms = AgentCapabilityPermissionRegistry("ws-1", ids)
    perms.grant(permission_id="p1", agent_id="a1", capability="crm.read", scope={"workspace_id": "ws-1"})
    plans = AgentPlanningRegistry("ws-1", ids, perms)
    plans.create_plan(plan_id="p1", agent_id="a1", objective="inspect", steps=(PlanStep("s1", "Read CRM", "crm.read", "inspect CRM", requested_scope={"workspace_id": "ws-1"}),))
    plans.assess("p1")
    evals = AgentEvaluationRegistry("ws-1", ids, plans)
    budgets = AgentBudgetRegistry("ws-1", ids)
    budgets.create(budget_id="b1", agent_id="a1", max_actions=2)
    takeovers = HumanTakeoverRegistry("ws-1", ids)
    reg = BoundedAutonomousOperationsRegistry("ws-1", ids, perms, plans, evals, budgets, takeovers)
    envelope = BusinessAutonomyEnvelope(workspace_id="ws-1", objective="inspect", success_metrics=(SuccessMetric("inspection", ">=", 1),), allowed_actions=("crm.read",), risk_boundary=RiskBoundary(max_risk="high"), mode=AutonomyMode.EXECUTE, state=EnvelopeState.ACTIVE)
    workflow = {"id": "sales.qualified-lead-status", "action": "crm.read", "risk": "low", "trigger": "lead.qualified"}
    reg.create(run_id="r1", agent_id="a1", plan_id="p1", objective="inspect", workflow_id=workflow["id"], trigger=workflow["trigger"], event={"event_id": "e1"}, budget_id="b1")
    return reg, envelope, workflow


def test_c84_admission_is_fail_closed_and_explicit_about_unproven_distributed_guarantees():
    reg, envelope, workflow = _setup()
    cert = certify_admission_boundaries(reg, run_id="r1", envelope=envelope, workflow=workflow)
    assert cert.status == CertificationStatus.NOT_PROVEN
    assert cert.passed is False
    names = {c.name: c for c in cert.checks}
    assert names["authority_is_not_granted_by_admission"].status == CertificationStatus.PASS
    assert names["high_risk_never_becomes_autonomous_execution"].status == CertificationStatus.PASS
    assert names["revoked_envelope_blocks_execution"].status == CertificationStatus.PASS
    assert names["budget_concurrency_reservation"].status == CertificationStatus.NOT_PROVEN
    assert names["real_worker_authority_under_concurrency"].status == CertificationStatus.NOT_PROVEN
