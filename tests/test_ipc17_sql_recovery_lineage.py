from pathlib import Path


def test_ipc17_migration_has_durable_objective_lineage():
    sql = Path("backend/migrations/102_ipc17_objective_recovery_lineage.sql").read_text()
    assert "autonomous_exceptions" in sql
    assert "autonomous_recovery_plans" in sql
    assert "objective_id" in sql
    assert "cycle_id" in sql
    assert "run_id" in sql


def test_ipc17_release_function_fails_closed_on_lineage_mismatch():
    sql = Path("backend/migrations/102_ipc17_objective_recovery_lineage.sql").read_text()
    assert "v_plan_objective IS DISTINCT FROM v_exception_objective" in sql
    assert "v_plan_cycle IS DISTINCT FROM v_exception_cycle" in sql
    assert "v_plan_run IS DISTINCT FROM v_exception_run" in sql
    assert "RETURN FALSE;" in sql


def test_ipc17_release_remains_service_role_only():
    sql = Path("backend/migrations/102_ipc17_objective_recovery_lineage.sql").read_text()
    assert "REVOKE ALL ON FUNCTION public.release_action_outbox_for_recovery" in sql
    assert "GRANT EXECUTE ON FUNCTION public.release_action_outbox_for_recovery" in sql
    assert "TO service_role" in sql
