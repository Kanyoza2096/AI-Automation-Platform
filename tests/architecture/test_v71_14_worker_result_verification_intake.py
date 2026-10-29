import pytest
from services.v71_13_worker_task_progress import WorkerTaskProgressRegistry
from services.v71_14_worker_result_verification_intake import (
    WorkerResultVerificationIntakeRegistry, IntakeState, IntakeBlock,
)


def setup(tmp_path):
    from test_v71_13_worker_task_progress import setup as setup_progress
    progress, handoff = setup_progress(tmp_path)
    progress.submit(progress_id="p-submit", handoff_id=handoff.handoff_id,
                    message="worker_result", evidence={"receipt_ref": "local-evidence"})
    intake = WorkerResultVerificationIntakeRegistry("ws-1", progress, str(tmp_path / "intake.db"))
    return intake, handoff


def test_only_submitted_result_enters_verification_intake(tmp_path):
    intake, handoff = setup(tmp_path)
    item = intake.prepare(intake_id="i1", handoff_id=handoff.handoff_id)
    assert item.state is IntakeState.PREPARED
    assert item.verification_requested is True
    assert item.business_outcome_asserted is False


def test_handoff_to_verification_is_explicit(tmp_path):
    intake, handoff = setup(tmp_path)
    intake.prepare(intake_id="i1", handoff_id=handoff.handoff_id)
    item = intake.hand_to_verification(intake_id="i1")
    assert item.state is IntakeState.HANDED_TO_VERIFICATION


def test_worker_cannot_skip_submission(tmp_path):
    intake, handoff = setup(tmp_path)
    with pytest.raises(ValueError, match=IntakeBlock.PROGRESS_MISSING.value):
        intake.prepare(intake_id="bad", handoff_id="missing")


def test_terminal_intake_blocks_repeated_handoff(tmp_path):
    intake, handoff = setup(tmp_path)
    intake.prepare(intake_id="i1", handoff_id=handoff.handoff_id)
    intake.hand_to_verification(intake_id="i1")
    with pytest.raises(ValueError, match=IntakeBlock.INVALID_TRANSITION.value):
        intake.hand_to_verification(intake_id="i1")


def test_return_requires_reason(tmp_path):
    intake, handoff = setup(tmp_path)
    intake.prepare(intake_id="i1", handoff_id=handoff.handoff_id)
    with pytest.raises(ValueError, match=IntakeBlock.REASON_REQUIRED.value):
        intake.return_for_clarification(intake_id="i1", message="")


def test_restart_and_fingerprint_chain(tmp_path):
    intake, handoff = setup(tmp_path)
    db = str(tmp_path / "restart.db")
    persistent = WorkerResultVerificationIntakeRegistry("ws-1", intake.progress, db)
    item = persistent.prepare(intake_id="persist", handoff_id=handoff.handoff_id)
    persistent.close()
    restored = WorkerResultVerificationIntakeRegistry("ws-1", intake.progress, db)
    found = restored.get("persist")
    assert found and found.intake_fingerprint == item.intake_fingerprint
    restored.assert_integrity(found)
    restored.close()


def test_tamper_cannot_promote_business_outcome(tmp_path):
    intake, handoff = setup(tmp_path)
    item = intake.prepare(intake_id="i1", handoff_id=handoff.handoff_id)
    tampered = type(item)(**{**item.__dict__, "business_outcome_asserted": True})
    with pytest.raises(ValueError, match=IntakeBlock.INTAKE_TAMPERED.value):
        intake.assert_integrity(tampered)


def test_verification_context_preserves_no_authority(tmp_path):
    intake, handoff = setup(tmp_path)
    intake.prepare(intake_id="i1", handoff_id=handoff.handoff_id)
    ctx = intake.verification_context("i1")
    assert ctx["verification_requested"] is True
    assert ctx["authorization_granted"] is False
    assert ctx["execution_requested"] is False
    assert ctx["provider_called"] is False
    assert ctx["business_outcome_asserted"] is False
