import pytest

from services.v71_12_worker_task_handoff import WorkerTaskHandoffRegistry
from services.v71_13_worker_task_progress import WorkerTaskProgressRegistry, ProgressState
from services.v71_14_worker_result_verification_intake import WorkerResultVerificationIntakeRegistry
from services.v71_42_coordinated_worker_progress_synchronization import (
    CoordinatedWorkerProgressSynchronization, ProgressSyncBlock,
)
from tests.test_phase7_30_coordinated_assignment_lifecycle import setup2


def _sync_stack(tmp_path):
    lifecycle, _ = setup2(tmp_path)
    lifecycle.create(coordination_lifecycle_id="cl1", admission_id="adm1")
    handoffs = WorkerTaskHandoffRegistry("ws", lifecycle.admission.assignment_admission, lifecycle.lifecycle,
                                         str(tmp_path / "handoff.db"))
    progress = WorkerTaskProgressRegistry("ws", handoffs, str(tmp_path / "progress.db"))
    intake = WorkerResultVerificationIntakeRegistry("ws", progress, str(tmp_path / "intake.db"))
    sync = CoordinatedWorkerProgressSynchronization("ws", lifecycle, handoffs, progress, intake,
                                                    str(tmp_path / "sync.db"))
    return lifecycle, handoffs, progress, intake, sync


def test_version_and_authority_boundary():
    assert CoordinatedWorkerProgressSynchronization.VERSION == "phase7.31"
    from services.v71_42_coordinated_worker_progress_synchronization import CoordinatedProgressSnapshot, CoordinatedTaskProgressView
    task = CoordinatedTaskProgressView("t", "a", "w", "assigned")
    snap = CoordinatedProgressSnapshot("s", "ws", "cl", "p", (task,), ("t",))
    assert snap.coordination_only is True
    assert snap.authorization_granted is False
    assert snap.execution_requested is False
    assert snap.provider_called is False
    assert snap.action_kernel_invoked is False


def test_snapshot_reads_progress_without_granting_authority(tmp_path):
    lifecycle, handoffs, progress, intake, sync = _sync_stack(tmp_path)
    lifecycle.start_task(coordination_lifecycle_id="cl1", task_id="a")
    h = handoffs.offer(handoff_id="h-a", assignment_id=lifecycle._task(lifecycle.get("cl1"), "a").assignment_id)
    h = handoffs.acknowledge(handoff_id="h-a-ack", assignment_id=h.assignment_id)
    p = progress.start(progress_id="p-a-1", handoff_id=h.handoff_id, evidence={"observed": True})
    snap = sync.synchronize(snapshot_id="s1", coordination_lifecycle_id="cl1")
    view = next(x for x in snap.tasks if x.task_id == "a")
    assert view.progress_id == p.progress_id
    assert view.progress_state == ProgressState.STARTED.value
    assert view.evidence_available is True
    assert snap.execution_requested is False


def test_submitted_result_does_not_make_dependency_ready(tmp_path):
    lifecycle, handoffs, progress, intake, sync = _sync_stack(tmp_path)
    lifecycle.start_task(coordination_lifecycle_id="cl1", task_id="a")
    aid = lifecycle._task(lifecycle.get("cl1"), "a").assignment_id
    h = handoffs.offer(handoff_id="h-a", assignment_id=aid)
    h = handoffs.acknowledge(handoff_id="h-a-ack", assignment_id=aid)
    progress.start(progress_id="p-a-1", handoff_id=h.handoff_id)
    progress.submit(progress_id="p-a-2", handoff_id=h.handoff_id, message="submitted", evidence={"result": "candidate"})
    snap = sync.synchronize(snapshot_id="s1", coordination_lifecycle_id="cl1")
    assert "b" not in snap.eligible_next_tasks
    assert next(x for x in snap.tasks if x.task_id == "a").progress_state == ProgressState.SUBMITTED.value


def test_released_dependency_makes_downstream_task_eligible(tmp_path):
    lifecycle, handoffs, progress, intake, sync = _sync_stack(tmp_path)
    lifecycle.start_task(coordination_lifecycle_id="cl1", task_id="a")
    aid = lifecycle._task(lifecycle.get("cl1"), "a").assignment_id
    h = handoffs.offer(handoff_id="h-a", assignment_id=aid)
    h = handoffs.acknowledge(handoff_id="h-a-ack", assignment_id=aid)
    progress.start(progress_id="p-a-1", handoff_id=h.handoff_id)
    lifecycle.release_task(coordination_lifecycle_id="cl1", task_id="a", reason="completed")
    snap = sync.synchronize(snapshot_id="s2", coordination_lifecycle_id="cl1")
    assert snap.eligible_next_tasks == ("b",)


def test_result_intake_is_projected_after_submission(tmp_path):
    lifecycle, handoffs, progress, intake, sync = _sync_stack(tmp_path)
    lifecycle.start_task(coordination_lifecycle_id="cl1", task_id="a")
    aid = lifecycle._task(lifecycle.get("cl1"), "a").assignment_id
    h = handoffs.offer(handoff_id="h-a", assignment_id=aid)
    h = handoffs.acknowledge(handoff_id="h-a-ack", assignment_id=aid)
    progress.start(progress_id="p-a-1", handoff_id=h.handoff_id)
    progress.submit(progress_id="p-a-2", handoff_id=h.handoff_id, message="submitted", evidence={"x": 1})
    item = intake.prepare(intake_id="int-a", handoff_id=h.handoff_id)
    snap = sync.synchronize(snapshot_id="s3", coordination_lifecycle_id="cl1")
    view = next(x for x in snap.tasks if x.task_id == "a")
    assert view.verification_intake_id == item.intake_id
    assert view.verification_intake_state == item.state.value


def test_missing_handoff_is_valid_observation(tmp_path):
    _, _, _, _, sync = _sync_stack(tmp_path)
    snap = sync.synchronize(snapshot_id="s4", coordination_lifecycle_id="cl1")
    assert all(not x.handoff_id for x in snap.tasks)
    assert snap.eligible_next_tasks == ("a",)


def test_duplicate_snapshot_and_tamper_are_rejected(tmp_path):
    _, _, _, _, sync = _sync_stack(tmp_path)
    snap = sync.synchronize(snapshot_id="s5", coordination_lifecycle_id="cl1")
    with pytest.raises(ValueError, match=ProgressSyncBlock.DUPLICATE.value):
        sync.synchronize(snapshot_id="s5", coordination_lifecycle_id="cl1")
    bad = type(snap)(**{**snap.__dict__, "execution_requested": True})
    with pytest.raises(ValueError, match=ProgressSyncBlock.AUTHORITY_VIOLATION.value):
        sync.assert_integrity(bad)


def test_eligible_tasks_are_advisory_and_do_not_start_work(tmp_path):
    lifecycle, _, _, _, sync = _sync_stack(tmp_path)
    snap = sync.synchronize(snapshot_id="s6", coordination_lifecycle_id="cl1")
    assert snap.eligible_next_tasks == ("a",)
    current = lifecycle.get("cl1")
    assert next(t for t in current.tasks if t.task_id == "a").state.value == "assigned"
