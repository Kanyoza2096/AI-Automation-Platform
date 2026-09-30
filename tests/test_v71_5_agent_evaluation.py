import pytest
from services.v71_1_agent_identity import AgentIdentityRegistry
from services.v71_2_agent_capability_permissions import AgentCapabilityPermissionRegistry
from services.v71_4_agent_planning import AgentPlanningRegistry, PlanStep
from services.v71_5_agent_evaluation import AgentEvaluationRegistry, EvaluationStatus, EvaluationBlock


def setup():
    ids = AgentIdentityRegistry("ws-1")
    ids.register(agent_id="a1", name="Worker", purpose="business work", declared_capabilities=("crm.read",))
    ids.transition(agent_id="a1", status=__import__('services.v71_1_agent_identity', fromlist=['AgentStatus']).AgentStatus.ACTIVE)
    perms = AgentCapabilityPermissionRegistry("ws-1", ids)
    perms.grant(permission_id="p1", agent_id="a1", capability="crm.read")
    planning = AgentPlanningRegistry("ws-1", ids, perms)
    planning.create_plan(plan_id="plan-1", agent_id="a1", objective="inspect", steps=(PlanStep("s1", "Read", "crm.read", "inspect"),))
    planning.assess("plan-1")
    return ids, planning, AgentEvaluationRegistry("ws-1", ids, planning)


def test_evaluation_is_evidence_based_and_does_not_grant_authority():
    _, _, evaluator = setup()
    result = evaluator.evaluate(
        evaluation_id="e1", plan_id="plan-1",
        execution_evidence={"successful_actions": 9, "attempted_actions": 10},
        outcome_evidence={"aligned_outcomes": 8, "measured_outcomes": 10},
        policy_evidence={"compliant_actions": 10, "reviewed_actions": 10},
    )
    assert result.status == EvaluationStatus.EVALUATED
    assert result.metrics.execution_success == 0.9
    assert result.metrics.outcome_alignment == 0.8
    assert result.metrics.policy_compliance == 1.0
    assert result.evidence["execution"]["successful_actions"] == 9
    assert result.fingerprint() == result.evaluation_fingerprint
    assert result.evidence.get("authority") is None


def test_insufficient_evidence_is_explicit():
    _, _, evaluator = setup()
    result = evaluator.evaluate(evaluation_id="e2", plan_id="plan-1")
    assert result.status == EvaluationStatus.INSUFFICIENT_EVIDENCE
    assert result.metrics.composite() is None
    assert "insufficient_observed_evidence" in result.findings


def test_invalid_counts_are_rejected():
    _, _, evaluator = setup()
    with pytest.raises(ValueError, match=EvaluationBlock.INVALID_EVIDENCE.value):
        evaluator.evaluate(evaluation_id="e3", plan_id="plan-1",
                           execution_evidence={"successful_actions": 11, "attempted_actions": 10})


def test_evaluation_tamper_and_tenant_protection():
    _, _, evaluator = setup()
    result = evaluator.evaluate(evaluation_id="e4", plan_id="plan-1", execution_evidence={"execution_success": .5})
    tampered = type(result)(**{**result.__dict__, "findings": ("evil",)})
    with pytest.raises(ValueError, match=EvaluationBlock.EVALUATION_TAMPERED.value):
        evaluator.assert_integrity(tampered)
    other_ids = AgentIdentityRegistry("ws-2")
    with pytest.raises(ValueError, match=EvaluationBlock.TENANT_MISMATCH.value):
        AgentEvaluationRegistry("ws-1", other_ids, evaluator.planning_registry)


def test_performance_summary_is_advisory_only():
    _, _, evaluator = setup()
    evaluator.evaluate(evaluation_id="e5", plan_id="plan-1", execution_evidence={"execution_success": .8})
    summary = evaluator.performance_summary("a1")
    assert summary["evaluation_count"] == 1
    assert summary["advisory"] is True
    assert summary["authority_granted"] is False
