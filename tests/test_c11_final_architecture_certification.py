from pathlib import Path
from services.c11_final_architecture_certification import certify_repository

ROOT = Path(__file__).resolve().parents[1]

def test_c11_repository_architecture_has_no_blockers():
    result = certify_repository(ROOT)
    assert result.status == "REVIEW"
    assert all(c.status != "BLOCKED" for c in result.checks)

def test_c11_live_evidence_remains_explicitly_unproven():
    result = certify_repository(ROOT)
    live = next(c for c in result.checks if c.name == "live_system_evidence")
    assert live.status == "NOT_PROVEN"

def test_c11_uncertain_budget_recovery_does_not_strand_reserved_capacity():
    result = certify_repository(ROOT)
    check = next(c for c in result.checks if c.name == "uncertain_budget_recovery_is_conservative")
    assert check.status == "PASS"
