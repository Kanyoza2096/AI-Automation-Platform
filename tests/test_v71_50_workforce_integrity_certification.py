import pytest
from services.v71_50_workforce_integrity_certification import WorkforceIntegrityCertification


def test_end_to_end_integrity_certification_is_architecture_certified():
    root = "/mnt/data/kdo_phase7/work741"
    report = WorkforceIntegrityCertification(root).run()
    assert report.status == "CERTIFIED_ARCHITECTURE_INTEGRITY"
    assert not any(f.status == "FAIL" for f in report.findings)
    assert sum(f.status == "NOT_PROVEN" for f in report.findings) == 4
    assert WorkforceIntegrityCertification.context(report)["authority"] == "none"


def test_report_is_fingerprint_bound_and_tamper_detected():
    root = "/mnt/data/kdo_phase7/work741"
    report = WorkforceIntegrityCertification(root).run()
    WorkforceIntegrityCertification.assert_integrity(report)
    bad = type(report)(**{**report.__dict__, "status": "TAMPERED"})
    with pytest.raises(ValueError, match="tampered_report"):
        WorkforceIntegrityCertification.assert_integrity(bad)


def test_required_chain_has_no_direct_provider_authority():
    report = WorkforceIntegrityCertification("/mnt/data/kdo_phase7/work741").run()
    controls = {f.control: f for f in report.findings}
    assert controls["external_side_effect_boundary"].status == "PASS"
    assert controls["authority_boundary"].status == "PASS"
    assert controls["chain_control_evidence"].status == "PASS"
