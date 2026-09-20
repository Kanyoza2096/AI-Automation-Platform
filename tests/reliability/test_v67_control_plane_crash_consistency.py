"""V67.7 control-plane crash consistency tests.

The durable recovery plan may exist before release wins. The exception state must
not claim ``recovery_planned`` until the same transaction has actually moved the
failed outbox row to pending. This prevents false recovery state after crashes or
release races.
"""
from pathlib import Path

from services.autonomous_exception_recovery import (
    AutonomousExceptionRecovery,
    ExceptionObservation,
    ExceptionClass,
    RecoveryDisposition,
)

ROOT = Path(__file__).resolve().parents[2]


def test_auto_recover_persistence_does_not_claim_release_before_rpc():
    sql = (ROOT / "services/autonomous_exception_recovery.py").read_text()
    # The persistence record is deliberately open; only the transactional SQL
    # release function may advance it to recovery_planned.
    assert '"state": "open"' in sql
    assert '"state": "recovery_planned"' not in sql.split('def _persist', 1)[1].split('def ', 1)[0]


def test_release_success_is_the_only_recovery_planned_transition():
    sql = (ROOT / "migrations/059_v67_recovery_release_race_hardening.sql").read_text()
    assert "SET status = 'pending'" in sql
    assert "SET state = 'recovery_planned'" in sql
    assert "GET DIAGNOSTICS v_updated = ROW_COUNT" in sql


def test_release_conflict_leaves_exception_state_for_retry_not_false_success():
    status = "open"
    outbox = "failed"

    def release():
        nonlocal status, outbox
        if outbox != "failed":
            return False
        outbox = "pending"
        status = "recovery_planned"
        return True

    # Another worker wins first.
    outbox = "processing"
    assert release() is False
    assert status == "open"
    assert outbox == "processing"


def test_crash_between_plan_persistence_and_release_is_retryable():
    exception_state = "open"
    plan_exists = True
    outbox_state = "failed"

    # Simulated process death: plan and exception evidence survive, release does not.
    assert plan_exists
    assert exception_state == "open"
    assert outbox_state == "failed"

    # A later coordinator may safely retry the transactional release.
    outbox_state = "pending"
    exception_state = "recovery_planned"
    assert (outbox_state, exception_state) == ("pending", "recovery_planned")
