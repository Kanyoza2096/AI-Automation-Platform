from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_c94_certification_script_exists():
    assert (ROOT / "scripts/tenant_security_certification.py").exists()

def test_sales_durable_migrations_are_contiguous_after_106():
    assert (ROOT / "migrations/106_v72_9_business_cockpit_operating_loop.sql").exists()
    assert (ROOT / "migrations/107_v70_2_sales_event_durable_processing.sql").exists()
    assert (ROOT / "migrations/108_v70_2_sales_atomic_state_transition.sql").exists()
    assert (ROOT / "migrations/109_v70_8_scheduled_post_claim_reconciliation.sql").exists()

def test_sales_claim_function_is_fail_closed():
    text = (ROOT / "migrations/107_v70_2_sales_event_durable_processing.sql").read_text()
    assert "status IN ('received','retryable')" in text
    assert "service_role" in text
    assert "REVOKE ALL" in text
