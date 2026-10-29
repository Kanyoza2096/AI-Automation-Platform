import pytest
from services.v71_15_worker_outcome_feedback_closure import WorkerOutcomeFeedbackClosureRegistry, VerifiedDisposition
from services.v71_44_coordinated_verification_outcome_closure import (
    CoordinatedVerificationOutcomeClosure, CanonicalVerificationOutcome,
    CoordinatedClosureState, OutcomePropagationBlock,
)
from tests.test_v71_43_coordinated_result_verification_handoff import _stack


def _submit(lifecycle, handoffs, progress, intake, task_id, suffix):
    task = lifecycle._task(lifecycle.get("cl1"), task_id)
    h = handoffs.offer(handoff_id=f"h-{suffix}", assignment_id=task.assignment_id)
    h = handoffs.acknowledge(handoff_id=f"h-{suffix}-ack", assignment_id=task.assignment_id)
    progress.start(progress_id=f"p-{suffix}-1", handoff_id=h.handoff_id)
    progress.submit(progress_id=f"p-{suffix}-2", handoff_id=h.handoff_id, message="submitted", evidence={"task": task_id})
    item = intake.prepare(intake_id=f"int-{suffix}", handoff_id=h.handoff_id)
    return intake.hand_to_verification(intake_id=item.intake_id)


def _service(tmp_path):
    lifecycle, handoffs, progress, intake, sync, aggregate = _stack(tmp_path)
    worker_closure = WorkerOutcomeFeedbackClosureRegistry("ws", intake, lifecycle.lifecycle, str(tmp_path / "closure.db"))
    service = CoordinatedVerificationOutcomeClosure("ws", aggregate, lifecycle, worker_closure, str(tmp_path / "coord-closure.db"))
    return lifecycle, handoffs, progress, intake, sync, aggregate, service


def test_version_and_authority_boundary(tmp_path):
    *_, service = _service(tmp_path)
    assert service.VERSION == "phase7.33"
    assert service.workspace_id == "ws"


def test_incremental_upstream_verification_releases_task_without_claiming_business_outcome(tmp_path):
    lifecycle, handoffs, progress, intake, sync, aggregate, service = _service(tmp_path)
    lifecycle.start_task(coordination_lifecycle_id="cl1", task_id="a")
    item = _submit(lifecycle, handoffs, progress, intake, "a", "a")
    snapshot = sync.synchronize(snapshot_id="s1", coordination_lifecycle_id="cl1")
    package = aggregate.aggregate(package_id="pkg1", coordination_lifecycle_id="cl1", snapshot_id=snapshot.snapshot_id)
    # b is not yet started, so package is partial; a is still legitimately verifiable.
    assert package.state.value == "partial"
    outcome = CanonicalVerificationOutcome("a", item.intake_id, "ver-a", "vf-a", VerifiedDisposition.VERIFIED_SUCCESS)
    closure = service.close_from_verification(closure_id="c1", package_id=package.package_id, outcomes=(outcome,))
    assert closure.state is CoordinatedClosureState.PROPAGATED
    assert service.context("c1")["objective_closed"] is False
    assert lifecycle._task(lifecycle.get("cl1"), "a").state.value == "released"
    assert lifecycle.context("cl1")["authorization_granted"] is False


def test_downstream_can_then_verify_and_objective_closes(tmp_path):
    lifecycle, handoffs, progress, intake, sync, aggregate, service = _service(tmp_path)
    lifecycle.start_task(coordination_lifecycle_id="cl1", task_id="a")
    a = _submit(lifecycle, handoffs, progress, intake, "a", "a")
    s1 = sync.synchronize(snapshot_id="s1", coordination_lifecycle_id="cl1")
    p1 = aggregate.aggregate(package_id="pkg1", coordination_lifecycle_id="cl1", snapshot_id=s1.snapshot_id)
    service.close_from_verification(closure_id="c1", package_id="pkg1", outcomes=(CanonicalVerificationOutcome("a", a.intake_id, "ver-a", "vf-a", VerifiedDisposition.VERIFIED_SUCCESS),))

    lifecycle.start_task(coordination_lifecycle_id="cl1", task_id="b")
    b = _submit(lifecycle, handoffs, progress, intake, "b", "b")
    s2 = sync.synchronize(snapshot_id="s2", coordination_lifecycle_id="cl1")
    p2 = aggregate.aggregate(package_id="pkg2", coordination_lifecycle_id="cl1", snapshot_id=s2.snapshot_id)
    # p2 contains both a and b. a is idempotently recognized; b is newly closed.
    c2 = service.close_from_verification(closure_id="c2", package_id="pkg2", outcomes=(
        CanonicalVerificationOutcome("a", a.intake_id, "ver-a", "vf-a", VerifiedDisposition.VERIFIED_SUCCESS),
        CanonicalVerificationOutcome("b", b.intake_id, "ver-b", "vf-b", VerifiedDisposition.VERIFIED_SUCCESS),
    ))
    assert c2.state is CoordinatedClosureState.CLOSED
    assert service.context("c2")["objective_closed"] is True
    assert {t.state.value for t in lifecycle.get("cl1").tasks} == {"released"}


def test_uncertain_verification_escalates_but_does_not_rewrite_truth(tmp_path):
    lifecycle, handoffs, progress, intake, sync, aggregate, service = _service(tmp_path)
    lifecycle.start_task(coordination_lifecycle_id="cl1", task_id="a")
    item = _submit(lifecycle, handoffs, progress, intake, "a", "a")
    snap = sync.synchronize(snapshot_id="s1", coordination_lifecycle_id="cl1")
    pkg = aggregate.aggregate(package_id="pkg1", coordination_lifecycle_id="cl1", snapshot_id=snap.snapshot_id)
    closure = service.close_from_verification(closure_id="c1", package_id="pkg1", outcomes=(CanonicalVerificationOutcome("a", item.intake_id, "ver-a", "vf-a", VerifiedDisposition.VERIFIED_UNCERTAIN),))
    assert closure.state is CoordinatedClosureState.ESCALATED
    assert closure.disposition_counts[VerifiedDisposition.VERIFIED_UNCERTAIN.value] == 1
    assert service.context("c1")["business_outcome_asserted"] is False


def test_identity_mismatch_and_missing_outcome_fail_closed(tmp_path):
    lifecycle, handoffs, progress, intake, sync, aggregate, service = _service(tmp_path)
    lifecycle.start_task(coordination_lifecycle_id="cl1", task_id="a")
    item = _submit(lifecycle, handoffs, progress, intake, "a", "a")
    snap = sync.synchronize(snapshot_id="s1", coordination_lifecycle_id="cl1")
    pkg = aggregate.aggregate(package_id="pkg1", coordination_lifecycle_id="cl1", snapshot_id=snap.snapshot_id)
    with pytest.raises(ValueError, match=OutcomePropagationBlock.OUTCOME_MISSING.value):
        service.close_from_verification(closure_id="c1", package_id=pkg.package_id, outcomes=())
    bad = CanonicalVerificationOutcome("a", "wrong-intake", "ver-a", "vf-a", VerifiedDisposition.VERIFIED_SUCCESS)
    with pytest.raises(ValueError, match=OutcomePropagationBlock.INTAKE_MISMATCH.value):
        service.close_from_verification(closure_id="c2", package_id=pkg.package_id, outcomes=(bad,))


def test_duplicate_and_tamper_are_rejected(tmp_path):
    lifecycle, handoffs, progress, intake, sync, aggregate, service = _service(tmp_path)
    lifecycle.start_task(coordination_lifecycle_id="cl1", task_id="a")
    item = _submit(lifecycle, handoffs, progress, intake, "a", "a")
    snap = sync.synchronize(snapshot_id="s1", coordination_lifecycle_id="cl1")
    pkg = aggregate.aggregate(package_id="pkg1", coordination_lifecycle_id="cl1", snapshot_id=snap.snapshot_id)
    out = CanonicalVerificationOutcome("a", item.intake_id, "ver-a", "vf-a", VerifiedDisposition.VERIFIED_SUCCESS)
    closure = service.close_from_verification(closure_id="c1", package_id="pkg1", outcomes=(out,))
    with pytest.raises(ValueError, match=OutcomePropagationBlock.DUPLICATE.value):
        service.close_from_verification(closure_id="c1", package_id="pkg1", outcomes=(out,))
    bad = type(closure)(**{**closure.__dict__, "business_outcome_asserted": True})
    with pytest.raises(ValueError, match=OutcomePropagationBlock.AUTHORITY_VIOLATION.value):
        service.assert_integrity(bad)
