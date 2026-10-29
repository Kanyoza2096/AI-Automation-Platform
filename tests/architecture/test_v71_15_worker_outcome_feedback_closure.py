import pytest
from services.v71_14_worker_result_verification_intake import WorkerResultVerificationIntakeRegistry
from services.v71_15_worker_outcome_feedback_closure import (
    WorkerOutcomeFeedbackClosureRegistry, VerifiedDisposition, ClosureState, ClosureBlock,
)
from services.v71_13_worker_task_progress import WorkerTaskProgressRegistry


def setup(tmp_path):
    from test_v71_12_worker_task_handoff import setup as setup_handoff
    handoff, assignment = setup_handoff(tmp_path)
    handoff.offer(handoff_id="h1", assignment_id=assignment.assignment_id)
    handoff.acknowledge(handoff_id="h2", assignment_id=assignment.assignment_id)
    progress = WorkerTaskProgressRegistry("ws-1", handoff, str(tmp_path / "progress.db"))
    active_handoff_id = handoff.latest_for_assignment(assignment.assignment_id).handoff_id
    progress.start(progress_id="p1", handoff_id=active_handoff_id, message="started")
    progress.submit(progress_id="p2", handoff_id=active_handoff_id, message="result", evidence={"receipt_ref": "e1"})
    intake = WorkerResultVerificationIntakeRegistry("ws-1", progress, str(tmp_path / "intake.db"))
    intake.prepare(intake_id="i1", handoff_id=active_handoff_id)
    intake.hand_to_verification(intake_id="i1")
    closure = WorkerOutcomeFeedbackClosureRegistry("ws-1", intake, handoff.lifecycle, str(tmp_path / "closure.db"))
    return closure, intake, handoff.lifecycle, assignment


def test_verified_success_closes_and_releases_capacity(tmp_path):
    closure, intake, life, assignment = setup(tmp_path)
    record = closure.close_from_verification(
        closure_id="c1", intake_id="i1", verification_id="v1",
        verification_fingerprint="vf1", disposition=VerifiedDisposition.VERIFIED_SUCCESS,
    )
    assert record.state is ClosureState.CLOSED
    assert life.latest_for_assignment(assignment.assignment_id).state.value == "released"
    closure.assert_integrity(record)


def test_uncertain_is_escalated_but_capacity_is_released(tmp_path):
    closure, _, life, assignment = setup(tmp_path)
    record = closure.close_from_verification(
        closure_id="c1", intake_id="i1", verification_id="v1",
        verification_fingerprint="vf1", disposition="verified_uncertain",
    )
    assert record.state is ClosureState.ESCALATED
    assert life.latest_for_assignment(assignment.assignment_id).state.value == "released"


def test_cannot_close_before_verification_handoff(tmp_path):
    from test_v71_12_worker_task_handoff import setup as setup_handoff
    handoff, assignment = setup_handoff(tmp_path)
    handoff.offer(handoff_id="h1", assignment_id=assignment.assignment_id)
    handoff.acknowledge(handoff_id="h2", assignment_id=assignment.assignment_id)
    progress = WorkerTaskProgressRegistry("ws-1", handoff, str(tmp_path / "p.db"))
    active_handoff_id = handoff.latest_for_assignment(assignment.assignment_id).handoff_id
    progress.start(progress_id="p1", handoff_id=active_handoff_id)
    progress.submit(progress_id="p2", handoff_id=active_handoff_id, message="result", evidence={"e": 1})
    intake = WorkerResultVerificationIntakeRegistry("ws-1", progress, str(tmp_path / "i.db"))
    intake.prepare(intake_id="i1", handoff_id=active_handoff_id)
    closure = WorkerOutcomeFeedbackClosureRegistry("ws-1", intake, handoff.lifecycle, str(tmp_path / "c.db"))
    with pytest.raises(ValueError, match=ClosureBlock.INTAKE_NOT_HANDED.value):
        closure.close_from_verification(closure_id="c1", intake_id="i1", verification_id="v1", verification_fingerprint="vf", disposition="verified_success")


def test_duplicate_intake_closure_is_rejected(tmp_path):
    closure, _, _, _ = setup(tmp_path)
    closure.close_from_verification(closure_id="c1", intake_id="i1", verification_id="v1", verification_fingerprint="vf1", disposition="verified_failure")
    with pytest.raises(ValueError, match=ClosureBlock.DUPLICATE.value):
        closure.close_from_verification(closure_id="c2", intake_id="i1", verification_id="v2", verification_fingerprint="vf2", disposition="verified_success")


def test_restart_and_tamper_protection(tmp_path):
    closure, intake, life, _ = setup(tmp_path)
    db = str(tmp_path / "restart.db")
    closure.close()
    closure = WorkerOutcomeFeedbackClosureRegistry("ws-1", intake, life, db)
    record = closure.close_from_verification(closure_id="c1", intake_id="i1", verification_id="v1", verification_fingerprint="vf1", disposition="verified_failure")
    closure.close()
    restored = WorkerOutcomeFeedbackClosureRegistry("ws-1", intake, life, db)
    found = restored.get("c1")
    assert found and found.closure_fingerprint == record.closure_fingerprint
    restored.assert_integrity(found)
    tampered = type(found)(**{**found.__dict__, "business_outcome_owned": True})
    with pytest.raises(ValueError, match=ClosureBlock.CLOSURE_TAMPERED.value):
        restored.assert_integrity(tampered)


def test_no_authority_or_business_outcome_ownership(tmp_path):
    closure, _, _, assignment = setup(tmp_path)
    record = closure.close_from_verification(closure_id="c1", intake_id="i1", verification_id="v1", verification_fingerprint="vf1", disposition="verified_success")
    ctx = closure.context(assignment.assignment_id)
    assert record.coordination_only is True
    assert record.authorization_granted is False
    assert record.execution_requested is False
    assert record.provider_called is False
    assert record.business_outcome_owned is False
    assert ctx["business_outcome_owned"] is False
