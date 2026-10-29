import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_v68_13_migration_rebinds_reservation_and_validates_tenant():
    sql = (ROOT / "migrations/063_v68_13_tenant_bound_execution_integrity.sql").read_text()
    assert "business automation rule not found" in sql
    assert "rule_row.workspace_id <> ws" in sql
    assert "rule_row.action <> action_name" in sql
    assert "validate_business_automation_execution_tenant" in sql
    assert "rule_ws <> NEW.workspace_id" in sql
    assert "rule_action <> NEW.action" in sql


def test_v68_13_service_role_only_reservation():
    sql = (ROOT / "migrations/063_v68_13_tenant_bound_execution_integrity.sql").read_text()
    assert "REVOKE EXECUTE ON FUNCTION public.reserve_business_automation_execution(JSONB) FROM PUBLIC, anon, authenticated;" in sql
    assert "GRANT EXECUTE ON FUNCTION public.reserve_business_automation_execution(JSONB) TO service_role;" in sql
