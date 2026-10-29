from pathlib import Path
from services.v71_12_workforce_certification import run_offline_certification, CertificationStatus


def test_offline_certification_proves_structure_but_not_live_world():
    root = Path(__file__).resolve().parents[1]
    report = run_offline_certification(root)
    assert report.status == CertificationStatus.NOT_PROVEN
    statuses = {f.control: f.status for f in report.findings}
    assert statuses["module_integrity"] == CertificationStatus.PASS
    assert statuses["execution_boundary"] == CertificationStatus.PASS
    assert statuses["single_governed_execution_path"] == CertificationStatus.PASS
    assert statuses["durability_and_reconciliation"] == CertificationStatus.PASS
    assert statuses["operator_control_boundary"] == CertificationStatus.PASS
    assert statuses["live_provider_execution"] == CertificationStatus.NOT_PROVEN


def test_certification_never_calls_provider_or_grants_authority():
    root = Path(__file__).resolve().parents[1]
    report = run_offline_certification(root)
    assert all(f.status != CertificationStatus.FAIL for f in report.findings)
    assert not any("authority" in f.detail.lower() and f.status == CertificationStatus.PASS for f in report.findings)
