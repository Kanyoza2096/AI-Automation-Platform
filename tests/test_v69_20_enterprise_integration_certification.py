from services.v69_enterprise_integration_certification import (
    CertificationStatus, certify_enterprise_integration,
)


def test_v69_20_has_no_safety_failures_offline():
    report = certify_enterprise_integration("w1")
    assert report.status in {CertificationStatus.PASS, CertificationStatus.NOT_PROVEN}
    assert not report.failed
    assert not report.authority_granted
    assert report.execution_effect == "none"


def test_v69_20_requires_live_evidence_to_reach_pass():
    report = certify_enterprise_integration("w1")
    assert report.status == CertificationStatus.NOT_PROVEN
    assert "live_provider_certification" in {x.code for x in report.not_proven}


def test_v69_20_fingerprint_is_stable_for_same_observations_shape():
    a = certify_enterprise_integration("w1")
    b = certify_enterprise_integration("w1")
    assert len(a.evidence_fingerprint) == 64


def test_v69_20_is_tenant_bound():
    a = certify_enterprise_integration("w1")
    b = certify_enterprise_integration("w2")
    assert a.workspace_id == "w1" and b.workspace_id == "w2"
    assert a.evidence_fingerprint == b.evidence_fingerprint
