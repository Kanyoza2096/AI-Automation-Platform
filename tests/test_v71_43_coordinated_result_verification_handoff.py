import pytest

from services.v71_12_worker_task_handoff import WorkerTaskHandoffRegistry
from services.v71_13_worker_task_progress import WorkerTaskProgressRegistry
from services.v71_14_worker_result_verification_intake import WorkerResultVerificationIntakeRegistry
from services.v71_43_coordinated_result_verification_handoff import (
    CoordinatedWorkerResultVerificationHandoff, ResultAggregationBlock,
    ResultAggregationState,
)
from tests.test_v71_42_coordinated_worker_progress_synchronization import _sync_stack


def _stack(tmp_path):
    lifecycle, handoffs, progress, intake, sync = _sync_stack(tmp_path)
    handoff = handoffs
    result = CoordinatedWorkerResultVerificationHandoff("ws", lifecycle, sync, intake, str(tmp_path / "aggregate.db"))
    return lifecycle, handoffs, progress, intake, sync, result


def _submit_and_hand_to_verification(lifecycle, handoffs, progress, intake, task_id, suffix):
    task = lifecycle._task(lifecycle.get("cl1"), task_id)
    lifecycle.start_task(coordination_lifecycle_id="cl1", task_id=task_id)
    h = handoffs.offer(handoff_id=f"h-{suffix}", assignment_id=task.assignment_id)
    h = handoffs.acknowledge(handoff_id=f"h-{suffix}-ack", assignment_id=task.assignment_id)
    progress.start(progress_id=f"p-{suffix}-1", handoff_id=h.handoff_id)
    progress.submit(progress_id=f"p-{suffix}-2", handoff_id=h.handoff_id,
                    message="submitted", evidence={"task": task_id, "evidence": suffix})
    item = intake.prepare(intake_id=f"int-{suffix}", handoff_id=h.handoff_id)
    return intake.hand_to_verification(intake_id=item.intake_id)


def test_version_and_authority_boundary(tmp_path):
    *_, result = _stack(tmp_path)
    assert result.VERSION == "phase7.32"
    from services.v71_43_coordinated_result_verification_handoff import CoordinatedVerificationPackage
    pkg = CoordinatedVerificationPackage("p", "ws", "cl", "plan", "snap", ResultAggregationState.PARTIAL, (), ())
    assert pkg.coordination_only is True
    assert pkg.verification_requested is True
    assert pkg.authorization_granted is False
    assert pkg.execution_requested is False
    assert pkg.provider_called is False
    assert pkg.action_kernel_invoked is False
    assert pkg.business_outcome_asserted is False


def test_partial_package_when_results_are_missing(tmp_path):
    lifecycle, handoffs, progress, intake, sync, result = _stack(tmp_path)
    sync_snapshot = sync.synchronize(snapshot_id="s1", coordination_lifecycle_id="cl1")
    pkg = result.aggregate(package_id="pkg1", coordination_lifecycle_id="cl1", snapshot_id=sync_snapshot.snapshot_id)
    assert pkg.state is ResultAggregationState.PARTIAL
    assert pkg.results == ()
    assert set(pkg.missing_task_ids) == {"a", "b"}


def test_all_handed_results_become_ready_for_verification(tmp_path):
    lifecycle, handoffs, progress, intake, sync, result = _stack(tmp_path)
    # b depends on a, so release a before starting b.
    lifecycle.start_task(coordination_lifecycle_id="cl1", task_id="a")
    a = lifecycle._task(lifecycle.get("cl1"), "a")
    h1 = handoffs.offer(handoff_id="h-a", assignment_id=a.assignment_id)
    h1 = handoffs.acknowledge(handoff_id="h-a-ack", assignment_id=a.assignment_id)
    progress.start(progress_id="p-a-1", handoff_id=h1.handoff_id)
    progress.submit(progress_id="p-a-2", handoff_id=h1.handoff_id, message="submitted", evidence={"a": 1})
    i1 = intake.prepare(intake_id="int-a", handoff_id=h1.handoff_id)
    intake.hand_to_verification(intake_id=i1.intake_id)
    lifecycle.release_task(coordination_lifecycle_id="cl1", task_id="a", reason="worker_result_recorded")

    lifecycle.start_task(coordination_lifecycle_id="cl1", task_id="b")
    b = lifecycle._task(lifecycle.get("cl1"), "b")
    h2 = handoffs.offer(handoff_id="h-b", assignment_id=b.assignment_id)
    h2 = handoffs.acknowledge(handoff_id="h-b-ack", assignment_id=b.assignment_id)
    progress.start(progress_id="p-b-1", handoff_id=h2.handoff_id)
    progress.submit(progress_id="p-b-2", handoff_id=h2.handoff_id, message="submitted", evidence={"b": 2})
    i2 = intake.prepare(intake_id="int-b", handoff_id=h2.handoff_id)
    intake.hand_to_verification(intake_id=i2.intake_id)

    snapshot = sync.synchronize(snapshot_id="s2", coordination_lifecycle_id="cl1")
    pkg = result.aggregate(package_id="pkg2", coordination_lifecycle_id="cl1", snapshot_id=snapshot.snapshot_id)
    assert pkg.state is ResultAggregationState.READY_FOR_VERIFICATION
    assert [x.task_id for x in pkg.results] == ["a", "b"]
    assert pkg.missing_task_ids == ()


def test_prepared_but_not_handed_is_not_ready(tmp_path):
    lifecycle, handoffs, progress, intake, sync, result = _stack(tmp_path)
    _submit_and_hand_to_verification(lifecycle, handoffs, progress, intake, "a", "a")
    # b remains unsubmitted; a alone must remain partial.
    snapshot = sync.synchronize(snapshot_id="s3", coordination_lifecycle_id="cl1")
    pkg = result.aggregate(package_id="pkg3", coordination_lifecycle_id="cl1", snapshot_id=snapshot.snapshot_id)
    assert pkg.state is ResultAggregationState.PARTIAL
    assert "b" in pkg.missing_task_ids


def test_snapshot_lifecycle_mismatch_is_rejected(tmp_path):
    lifecycle, handoffs, progress, intake, sync, result = _stack(tmp_path)
    lifecycle.create(coordination_lifecycle_id="cl2", admission_id="adm1")
    snapshot = sync.synchronize(snapshot_id="s4", coordination_lifecycle_id="cl2")
    with pytest.raises(ValueError, match=ResultAggregationBlock.SNAPSHOT_INVALID.value):
        result.aggregate(package_id="pkg4", coordination_lifecycle_id="cl1", snapshot_id=snapshot.snapshot_id)


def test_duplicate_and_tamper_are_rejected(tmp_path):
    lifecycle, handoffs, progress, intake, sync, result = _stack(tmp_path)
    snapshot = sync.synchronize(snapshot_id="s5", coordination_lifecycle_id="cl1")
    pkg = result.aggregate(package_id="pkg5", coordination_lifecycle_id="cl1", snapshot_id=snapshot.snapshot_id)
    with pytest.raises(ValueError, match=ResultAggregationBlock.DUPLICATE.value):
        result.aggregate(package_id="pkg5", coordination_lifecycle_id="cl1", snapshot_id=snapshot.snapshot_id)
    bad = type(pkg)(**{**pkg.__dict__, "business_outcome_asserted": True})
    with pytest.raises(ValueError, match=ResultAggregationBlock.AUTHORITY_VIOLATION.value):
        result.assert_integrity(bad)


def test_package_context_never_declares_business_outcome(tmp_path):
    *_, result = _stack(tmp_path)
    snapshot = result.synchronization.synchronize(snapshot_id="s6", coordination_lifecycle_id="cl1")
    pkg = result.aggregate(package_id="pkg6", coordination_lifecycle_id="cl1", snapshot_id=snapshot.snapshot_id)
    ctx = result.context(pkg.package_id)
    assert ctx["verification_requested"] is True
    assert ctx["authorization_granted"] is False
    assert ctx["execution_requested"] is False
    assert ctx["provider_called"] is False
    assert ctx["action_kernel_invoked"] is False
    assert ctx["business_outcome_asserted"] is False


def test_result_evidence_is_bound_to_original_progress_fingerprint(tmp_path):
    lifecycle, handoffs, progress, intake, sync, result = _stack(tmp_path)
    _submit_and_hand_to_verification(lifecycle, handoffs, progress, intake, "a", "a")
    snapshot = sync.synchronize(snapshot_id="s7", coordination_lifecycle_id="cl1")
    pkg = result.aggregate(package_id="pkg7", coordination_lifecycle_id="cl1", snapshot_id=snapshot.snapshot_id)
    evidence = next(x for x in pkg.results if x.task_id == "a")
    original = progress.get(evidence.submitted_progress_id)
    assert original is not None
    assert evidence.submitted_progress_fingerprint == original.progress_fingerprint
