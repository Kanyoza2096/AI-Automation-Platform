from services.enterprise_production_certification import EnterpriseProductionCertificationService
from models.enterprise_production_certification import CertificationStatus


def _all_pass():
    checks = [
        "architecture_integrity", "security_controls", "business_operating_model",
        "agent_control_plane", "execution_governance", "universal_provenance",
        "business_reliability", "recovery_readiness", "external_runtime_evidence",
    ]
    return [{"workspace_id": "w1", "check_id": c, "status": "PASS", "source": "cert-runner", "evidence_id": c + "-1"} for c in checks]


def test_missing_external_proof_blocks_certification():
    evidence = [e for e in _all_pass() if e["check_id"] != "external_runtime_evidence"]
    report = EnterpriseProductionCertificationService().assess(workspace_id="w1", evidence=evidence)
    assert report.status is CertificationStatus.BLOCKED
    assert report.certifies_production is False
    assert any("external_runtime_evidence" in x for x in report.blocking_reasons)


def test_all_required_evidence_certifies():
    report = EnterpriseProductionCertificationService().assess(workspace_id="w1", evidence=_all_pass())
    assert report.status is CertificationStatus.CERTIFIED
    assert report.certifies_production is True
    assert len(report.checks) == 9


def test_failed_evidence_is_distinct_from_unproven():
    evidence = _all_pass()
    evidence[-1]["status"] = "FAIL"
    report = EnterpriseProductionCertificationService().assess(workspace_id="w1", evidence=evidence)
    assert report.status is CertificationStatus.FAILED
    assert "FAILED:external_runtime_evidence" in report.blocking_reasons


def test_foreign_workspace_evidence_is_ignored():
    evidence = _all_pass()
    for item in evidence:
        item["workspace_id"] = "other"
    report = EnterpriseProductionCertificationService().assess(workspace_id="w1", evidence=evidence)
    assert report.status is CertificationStatus.BLOCKED
    assert not report.evidence


def test_projection_is_deterministic_and_read_only():
    service = EnterpriseProductionCertificationService()
    a = service.assess(workspace_id="w1", evidence=list(reversed(_all_pass())))
    b = service.assess(workspace_id="w1", evidence=_all_pass())
    assert a.fingerprint == b.fingerprint
    assert a.read_only is True
    assert all(c.blocking_reason is None for c in a.checks)
