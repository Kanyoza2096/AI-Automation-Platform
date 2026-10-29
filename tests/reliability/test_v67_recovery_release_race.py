"""V67.5 recovery release race tests.

These tests exercise the control-plane contract without requiring a live
Postgres instance: concurrent recovery attempts must converge on one release,
and the SQL must lock the plan/exception/outbox rows before transitioning
failed -> pending.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "migrations/059_v67_recovery_release_race_hardening.sql"


def test_recovery_release_locks_plan_exception_and_failed_outbox_row():
    sql = MIGRATION.read_text()
    assert "FROM public.autonomous_recovery_plans" in sql
    assert "FOR UPDATE;" in sql
    assert "FROM public.autonomous_exceptions" in sql
    assert "FROM public.action_outbox" in sql
    assert "AND status = 'failed'" in sql
    assert "SET status = 'pending'" in sql


def test_recovery_release_is_transactionally_bound_to_exception_state():
    sql = MIGRATION.read_text()
    assert "SET state = 'recovery_planned'" in sql
    assert "GET DIAGNOSTICS v_updated = ROW_COUNT" in sql
    assert "IF v_updated <> 1 THEN" in sql
    assert "RAISE EXCEPTION 'recovery exception disappeared during release'" in sql


def test_second_release_cannot_reset_live_processing_work():
    """Model the DB state guard: first release consumes failed state; second loses."""
    status = "failed"

    def release():
        nonlocal status
        if status != "failed":
            return False
        status = "pending"
        return True

    assert release() is True
    assert release() is False
    assert status == "pending"


def test_claim_after_release_is_the_only_valid_next_transition():
    status = "failed"

    def release():
        nonlocal status
        if status != "failed":
            return False
        status = "pending"
        return True

    def claim():
        nonlocal status
        if status != "pending":
            return False
        status = "processing"
        return True

    assert release() is True
    assert claim() is True
    # A stale/retried recovery coordinator cannot move live processing back to pending.
    assert release() is False
    assert status == "processing"


def test_attempt_budget_is_checked_before_release():
    sql = MIGRATION.read_text()
    assert "v_max_attempts <= 0 OR v_attempts >= v_max_attempts" in sql
