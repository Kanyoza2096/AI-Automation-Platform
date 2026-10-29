import pytest
from services.v71_46_workforce_adaptive_planning import WorkforceAdaptivePlanner, WorkforceCoordinationPolicy, AdaptivePlanningBlock
from tests.test_v71_45_workforce_coordination_feedback import _service as _feedback_service
from tests.test_phase7_26_multi_worker_planning import planner as _plan_factory


def _stack(tmp_path):
    # Reuse the real Phase 7.26 planner fixture, then attach 7.34 feedback services.
    planner = _plan_factory()
    *_, learning, feedback = _feedback_service(tmp_path)
    adaptive = WorkforceAdaptivePlanner("ws", planner, feedback, str(tmp_path / "adaptive.db"))
    return planner, feedback, adaptive


def _plan(tmp_path):
    planner, feedback, adaptive = _stack(tmp_path)
    from services.v71_37_workforce_multi_worker_planning import CoordinationTaskSpec
    plan = planner.plan(plan_id="p35", objective_id="o35", cycle_id="c35", coordinator_worker_id="coord",
                        resource_budget=2, specs=(CoordinationTaskSpec("a", "security", "task a"),
                                                  CoordinationTaskSpec("b", "supply", "task b", depends_on=("a",), candidate_worker_ids=("w2",))))
    return planner, feedback, adaptive, plan


def test_version_policy_and_authority(tmp_path):
    planner, feedback, adaptive = _stack(tmp_path)
    assert adaptive.VERSION == "phase7.35"
    policy = WorkforceCoordinationPolicy()
    policy.validate()
    assert policy.learning_influence_ceiling == 0.10


def test_learning_feedback_produces_bounded_adaptive_recommendations(tmp_path):
    planner, feedback, adaptive, plan = _plan(tmp_path)
    # No fabricated feedback: explicitly allow no historical evidence for this planning assessment.
    policy = WorkforceCoordinationPolicy(require_verified_feedback=False)
    rec = adaptive.assess(assessment_id="a35", plan=plan, policy=policy, feedback_ids=())
    assert rec.advisory_only is True
    assert rec.plan_mutated is False
    assert all(abs(x.bounded_adjustment) <= 0.10 for x in rec.recommendations)
    adaptive.assert_integrity(rec)


def test_missing_required_learning_is_rejected(tmp_path):
    planner, feedback, adaptive, plan = _plan(tmp_path)
    with pytest.raises(ValueError, match=AdaptivePlanningBlock.FEEDBACK_MISSING.value):
        adaptive.assess(assessment_id="a35", plan=plan, policy=WorkforceCoordinationPolicy(), feedback_ids=())


def test_policy_cannot_exceed_learning_ceiling(tmp_path):
    with pytest.raises(ValueError, match=AdaptivePlanningBlock.INFLUENCE_EXCEEDED.value):
        WorkforceCoordinationPolicy(learning_influence_ceiling=0.20).validate()

def test_verified_feedback_is_used_as_bounded_soft_signal_only(tmp_path):
    planner, _, _ = _stack(tmp_path)
    from services.v71_44_coordinated_verification_outcome_closure import CanonicalVerificationOutcome
    from services.v71_15_worker_outcome_feedback_closure import VerifiedDisposition
    from tests.test_v71_45_workforce_coordination_feedback import _service as feedback_service, _submit
    truth = tmp_path / "truth"
    truth.mkdir()
    lifecycle, handoffs, progress, intake, sync, aggregate, closure, learning, feedback = feedback_service(truth)
    lifecycle.start_task(coordination_lifecycle_id="cl1", task_id="a")
    item = _submit(lifecycle, handoffs, progress, intake, "a", "a")
    snap = sync.synchronize(snapshot_id="s35", coordination_lifecycle_id="cl1")
    pkg = aggregate.aggregate(package_id="pkg35", coordination_lifecycle_id="cl1", snapshot_id=snap.snapshot_id)
    outcome = CanonicalVerificationOutcome("a", item.intake_id, "ver35", "vf35", VerifiedDisposition.VERIFIED_SUCCESS)
    closed = closure.close_from_verification(closure_id="c35", package_id=pkg.package_id, outcomes=(outcome,))
    rec = feedback.record(feedback_id="cf35", coordinated_closure_id=closed.closure_id)
    adaptive = WorkforceAdaptivePlanner("ws", planner, feedback, str(tmp_path / "adaptive2.db"))
    plan = planner.plan(plan_id="p35", objective_id="o35", cycle_id="c35", coordinator_worker_id="coord", resource_budget=1, specs=(
        __import__('services.v71_37_workforce_multi_worker_planning', fromlist=['CoordinationTaskSpec']).CoordinationTaskSpec("a", "security", "task a"),))
    assessed = adaptive.assess(assessment_id="a35b", plan=plan, policy=WorkforceCoordinationPolicy(), feedback_ids=(rec.feedback_id,))
    assert assessed.learning_influence <= 0.10
    assert assessed.plan_mutated is False
    assert assessed.recommendations[0].signal == "bounded_positive_learning_bias"
    adaptive.assert_integrity(assessed)
