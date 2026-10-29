import pytest
from services.v71_46_workforce_adaptive_planning import WorkforceAdaptivePlanner, WorkforceCoordinationPolicy
from services.v71_47_workforce_coordination_policy_governance import WorkforceCoordinationPolicyGovernance
from services.v71_48_workforce_coordination_policy_enforcement import WorkforceCoordinationPolicyEnforcement, CoordinationPolicyEnforcementBlock
from tests.test_v71_45_workforce_coordination_feedback import _service as _feedback_service
from tests.test_phase7_26_multi_worker_planning import planner as _plan_factory


def _stack(tmp_path):
    planner = _plan_factory()
    *_, learning, feedback = _feedback_service(tmp_path)
    adaptive = WorkforceAdaptivePlanner("ws", planner, feedback, str(tmp_path / "adaptive.db"))
    governance = WorkforceCoordinationPolicyGovernance("ws", str(tmp_path / "governance.db"))
    enforcement = WorkforceCoordinationPolicyEnforcement("ws", governance, adaptive, str(tmp_path / "enforcement.db"))
    return planner, adaptive, governance, enforcement


def _active(governance, policy):
    governance.propose(change_id="chg1", policy_id="adaptive", version=int(policy.version), requested_by="owner", policy_fingerprint=policy.fingerprint())
    governance.transition("chg1", actor="reviewer", role="policy_reviewer", target="REVIEWED")
    governance.transition("chg1", actor="approver", role="policy_approver", target="APPROVED")
    return governance.transition("chg1", actor="activator", role="policy_approver", target="ACTIVE")


def _plan(planner, plan_id="p37"):
    from services.v71_37_workforce_multi_worker_planning import CoordinationTaskSpec
    return planner.plan(plan_id=plan_id, objective_id="o37", cycle_id="c37", coordinator_worker_id="coord", resource_budget=1,
                        specs=(CoordinationTaskSpec("a", "security", "task a"),))


def test_governed_assessment_requires_exact_active_policy(tmp_path):
    planner, adaptive, governance, enforcement = _stack(tmp_path)
    policy = WorkforceCoordinationPolicy(require_verified_feedback=False)
    _active(governance, policy)
    assessment = enforcement.assess_governed(assessment_id="a37", binding_id="b37", policy_id="adaptive", plan=_plan(planner), policy=policy, feedback_ids=())
    binding = enforcement.assert_consumable(policy_id="adaptive", policy=policy, assessment=assessment)
    assert binding.governance_state == "ACTIVE"
    assert binding.governance_change_id == "chg1"
    assert enforcement.context("a37")["authority"] == "none"


def test_missing_active_policy_fails_closed(tmp_path):
    planner, adaptive, governance, enforcement = _stack(tmp_path)
    policy = WorkforceCoordinationPolicy(require_verified_feedback=False)
    with pytest.raises(ValueError, match="policy_missing"):
        enforcement.assess_governed(assessment_id="a37", binding_id="b37", policy_id="adaptive", plan=_plan(planner), policy=policy, feedback_ids=())


def test_stale_version_and_fingerprint_fail_closed(tmp_path):
    planner, adaptive, governance, enforcement = _stack(tmp_path)
    policy = WorkforceCoordinationPolicy(version="1", require_verified_feedback=False)
    _active(governance, policy)
    stale = WorkforceCoordinationPolicy(version="2", require_verified_feedback=False)
    with pytest.raises(ValueError, match="stale_policy"):
        enforcement.assess_governed(assessment_id="a37", binding_id="b37", policy_id="adaptive", plan=_plan(planner), policy=stale, feedback_ids=())
    mismatch = WorkforceCoordinationPolicy(version="1", max_priority_adjustment=0.09, learning_influence_ceiling=0.10, require_verified_feedback=False)
    with pytest.raises(ValueError, match="policy_mismatch"):
        enforcement.assess_governed(assessment_id="a38", binding_id="b38", policy_id="adaptive", plan=_plan(planner, "p38"), policy=mismatch, feedback_ids=())


def test_binding_tamper_and_governance_tamper_fail_closed(tmp_path):
    planner, adaptive, governance, enforcement = _stack(tmp_path)
    policy = WorkforceCoordinationPolicy(require_verified_feedback=False)
    _active(governance, policy)
    assessment = enforcement.assess_governed(assessment_id="a37", binding_id="b37", policy_id="adaptive", plan=_plan(planner), policy=policy, feedback_ids=())
    b = enforcement.binding_for_assessment("a37")
    bad = type(b)(**{**b.__dict__, "policy_version": "99"})
    with pytest.raises(ValueError, match="binding_tampered"):
        enforcement.assert_binding_integrity(bad)
    active = governance.active("adaptive")
    bad_change = type(active)(**{**active.__dict__, "policy_fingerprint": "tampered"})
    with pytest.raises(ValueError, match="tampered"):
        governance.assert_integrity(bad_change)


def test_duplicate_and_workspace_fencing(tmp_path):
    planner, adaptive, governance, enforcement = _stack(tmp_path)
    policy = WorkforceCoordinationPolicy(require_verified_feedback=False)
    _active(governance, policy)
    plan = _plan(planner)
    assessment = enforcement.assess_governed(assessment_id="a37", binding_id="b37", policy_id="adaptive", plan=plan, policy=policy, feedback_ids=())
    with pytest.raises(ValueError, match="duplicate"):
        enforcement.bind_assessment(binding_id="b38", assessment=assessment, policy_id="adaptive", policy=policy)
    other_governance = WorkforceCoordinationPolicyGovernance("other")
    with pytest.raises(ValueError, match="tenant_mismatch"):
        WorkforceCoordinationPolicyEnforcement("ws", other_governance, adaptive)
