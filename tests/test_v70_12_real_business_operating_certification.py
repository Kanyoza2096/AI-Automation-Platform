from services.v70_12_real_business_operating_certification import *


def test_offline_certification_has_no_failures():
    report = run_offline_certification()
    assert report.failed == 0
    assert report.passed >= 7
    assert report.not_proven == 2
    assert report.status == CertificationStatus.NOT_PROVEN


def test_certification_is_deterministic():
    a = run_offline_certification()
    b = run_offline_certification()
    assert a.fingerprint() == b.fingerprint()
