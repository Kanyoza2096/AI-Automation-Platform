import pytest

from services.v69_connector_certification import CertificationStatus, CertificationFailure
from services.v69_reference_connector_certification import certify_facebook_reference


def test_facebook_reference_passes_offline_structural_certification():
    r = certify_facebook_reference()
    assert r.result.status == CertificationStatus.PASS
    assert not r.live_provider_verified
    assert any(c.name == "live_provider_behavior" and c.status.value == "not_proven" for c in r.result.checks)


def test_facebook_reference_proves_reconciliation_path():
    r = certify_facebook_reference()
    assert not any(c.failure_class == CertificationFailure.AMBIGUOUS_SIDE_EFFECT for c in r.result.failures)
    assert not any(c.failure_class == CertificationFailure.GOVERNED_EXECUTION for c in r.result.failures)


def test_facebook_reference_is_tenant_bound():
    r = certify_facebook_reference(workspace_id="tenant-b")
    assert r.result.status == CertificationStatus.PASS
    assert r.result.connector_key == "facebook"
