import pytest
from services.v71_27_worker_learning_feedback import WorkerLearningFeedbackRegistry
from services.v71_45_workforce_coordination_feedback import WorkforceCoordinationFeedbackRegistry, CoordinationFeedbackBlock
from services.v71_44_coordinated_verification_outcome_closure import CanonicalVerificationOutcome
from services.v71_15_worker_outcome_feedback_closure import WorkerOutcomeFeedbackClosureRegistry, VerifiedDisposition
from tests.test_v71_44_coordinated_verification_outcome_closure import _service as _closure_service, _submit


def _service(tmp_path):
    lifecycle, handoffs, progress, intake, sync, aggregate, closure = _closure_service(tmp_path)
    learning = WorkerLearningFeedbackRegistry("ws", closure.worker_closure, str(tmp_path / "learning.db"))
    feedback = WorkforceCoordinationFeedbackRegistry("ws", closure, learning, str(tmp_path / "coord-feedback.db"))
    return lifecycle, handoffs, progress, intake, sync, aggregate, closure, learning, feedback


def test_version_and_authority_boundary(tmp_path):
    *_, feedback = _service(tmp_path)
    assert feedback.VERSION == "phase7.34"


def test_verified_coordination_creates_durable_worker_and_aggregate_learning(tmp_path):
    lifecycle, handoffs, progress, intake, sync, aggregate, closure, learning, feedback = _service(tmp_path)
    lifecycle.start_task(coordination_lifecycle_id="cl1", task_id="a")
    item = _submit(lifecycle, handoffs, progress, intake, "a", "a")
    snap = sync.synchronize(snapshot_id="s1", coordination_lifecycle_id="cl1")
    pkg = aggregate.aggregate(package_id="pkg1", coordination_lifecycle_id="cl1", snapshot_id=snap.snapshot_id)
    outcome = CanonicalVerificationOutcome("a", item.intake_id, "ver-a", "vf-a", VerifiedDisposition.VERIFIED_SUCCESS)
    c = closure.close_from_verification(closure_id="c1", package_id=pkg.package_id, outcomes=(outcome,))
    rec = feedback.record(feedback_id="cf1", coordinated_closure_id=c.closure_id)
    assert rec.worker_count == 1
    assert rec.coordination_performance_score == 1.0
    assert len(rec.worker_feedback_ids) == 1
    feedback.assert_integrity(rec)
    learning.assert_integrity(learning.get(rec.worker_feedback_ids[0]))


def test_duplicate_and_historical_tamper_are_rejected(tmp_path):
    lifecycle, handoffs, progress, intake, sync, aggregate, closure, learning, feedback = _service(tmp_path)
    lifecycle.start_task(coordination_lifecycle_id="cl1", task_id="a")
    item = _submit(lifecycle, handoffs, progress, intake, "a", "a")
    snap = sync.synchronize(snapshot_id="s1", coordination_lifecycle_id="cl1")
    pkg = aggregate.aggregate(package_id="pkg1", coordination_lifecycle_id="cl1", snapshot_id=snap.snapshot_id)
    c = closure.close_from_verification(closure_id="c1", package_id=pkg.package_id, outcomes=(CanonicalVerificationOutcome("a", item.intake_id, "ver-a", "vf-a", VerifiedDisposition.VERIFIED_SUCCESS),))
    feedback.record(feedback_id="cf1", coordinated_closure_id=c.closure_id)
    with pytest.raises(ValueError, match=CoordinationFeedbackBlock.DUPLICATE.value):
        feedback.record(feedback_id="cf2", coordinated_closure_id=c.closure_id)
    bad = type(c)(**{**c.__dict__, "business_outcome_asserted": True})
    with pytest.raises(ValueError):
        closure.assert_integrity(bad)


def test_escalated_outcomes_produce_bounded_non_authoritative_signal(tmp_path):
    lifecycle, handoffs, progress, intake, sync, aggregate, closure, learning, feedback = _service(tmp_path)
    lifecycle.start_task(coordination_lifecycle_id="cl1", task_id="a")
    item = _submit(lifecycle, handoffs, progress, intake, "a", "a")
    snap = sync.synchronize(snapshot_id="s1", coordination_lifecycle_id="cl1")
    pkg = aggregate.aggregate(package_id="pkg1", coordination_lifecycle_id="cl1", snapshot_id=snap.snapshot_id)
    c = closure.close_from_verification(closure_id="c1", package_id=pkg.package_id, outcomes=(CanonicalVerificationOutcome("a", item.intake_id, "ver-a", "vf-a", VerifiedDisposition.VERIFIED_UNCERTAIN),))
    rec = feedback.record(feedback_id="cf1", coordinated_closure_id=c.closure_id)
    assert rec.coordination_performance_score == 0.5
    assert rec.coordination_reliability_score == 0.0
    assert feedback.context("cf1")["authorization_granted"] is False
