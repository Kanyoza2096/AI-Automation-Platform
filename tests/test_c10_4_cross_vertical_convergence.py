from pathlib import Path

from services.c10_4_cross_vertical_convergence import C104Status, certify_repository

ROOT = Path(__file__).resolve().parents[1]


def test_c10_4_repository_convergence_has_no_blockers():
    cert = certify_repository(ROOT)
    assert cert.status == C104Status.REVIEW
    assert all(c.status != C104Status.BLOCKED for c in cert.checks)


def test_c10_4_core_invariants_are_present():
    cert = certify_repository(ROOT)
    names = {c.name: c for c in cert.checks}
    for name in (
        "single_action_kernel_authority",
        "marketing_uses_canonical_kernel",
        "sales_uses_canonical_kernel",
        "autonomous_converges_through_workforce",
        "common_governance_admission",
        "common_durable_execution_substrate",
        "common_idempotency_boundary",
        "workspace_boundary_is_shared",
        "learning_boundary_is_shared",
        "no_vertical_direct_provider_calls",
    ):
        assert names[name].status.value == "pass"
