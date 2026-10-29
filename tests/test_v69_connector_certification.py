from services.business_integration_platform import ConnectorContract, ConnectionState, IntegrationConnection
from services.connectors.capabilities import ConnectorCapabilities
from services.v69_connector_certification import CertificationStatus, CertificationFailure, certify_connector
from services.v69_integration_foundation import CredentialBinding, CredentialKind

class FakeConnector:
    platform = "demo"
    CAPABILITIES = ConnectorCapabilities(native_idempotency=False, authoritative_lookup=True, safe_retry_after_ambiguous=False)
    RATE_LIMIT_POLICY = {"strategy": "backoff"}
    def publish(self, content, **kwargs): return {"ok": True, "post_id": "x"}
    def edit(self, post_id, content): return {"ok": True, "post_id": post_id}
    def delete(self, post_id): return {"ok": True, "post_id": post_id}

class Governed:
    executor = object(); ledger = object()
    publish = edit = delete = lambda *a, **k: None

def make_conn(state=ConnectionState.SUGGEST_ENABLED):
    return IntegrationConnection("tenant-a", "demo", state, ("publish",), connection_id="int-test")

def make_contract(**kw):
    data = dict(connector_key="demo", provider="demo", version="1.2.0", capabilities=("publish",), outbound_actions=("publish",), requires_reconciliation=True)
    data.update(kw); return ConnectorContract(**data)

def credential(active=True, tenant="tenant-a"):
    return CredentialBinding(tenant, "int-test", CredentialKind.OAUTH, "vault://secret/1", "acct-1", active=active)

def certify(**kw):
    params = dict(governed_adapter=Governed(), rate_limit_contract=True, expected_version="1.2.0", credential=credential())
    params.update(kw)
    return certify_connector(FakeConnector(), make_contract(), make_conn(), **params)

def test_offline_certification_passes_without_live_claim():
    r = certify()
    assert r.status == CertificationStatus.PASS
    assert not r.live_provider_verified
    assert any(c.status == CertificationStatus.NOT_PROVEN for c in r.checks)

def test_cross_tenant_credential_fails():
    r = certify(credential=credential(tenant="tenant-b"))
    assert r.status == CertificationStatus.FAIL
    assert CertificationFailure.AUTH_IDENTITY.value in r.failure_classes

def test_ambiguous_side_effect_without_lookup_fails():
    class Weak:
        platform = "demo"; CAPABILITIES = ConnectorCapabilities()
        publish = lambda self, content, **kwargs: None
    r = certify_connector(Weak(), make_contract(), make_conn(), governed_adapter=Governed(), rate_limit_contract=True, expected_version="1.2.0")
    assert r.status == CertificationStatus.FAIL
    assert CertificationFailure.AMBIGUOUS_SIDE_EFFECT.value in r.failure_classes

def test_missing_governed_adapter_fails_closed():
    r = certify_connector(FakeConnector(), make_contract(), make_conn(), expected_version="1.2.0")
    assert r.status == CertificationStatus.FAIL
    assert CertificationFailure.GOVERNED_EXECUTION.value in r.failure_classes

def test_revoked_credential_fails():
    r = certify(credential=credential(active=False))
    assert r.status == CertificationStatus.FAIL
    assert CertificationFailure.AUTH_IDENTITY.value in r.failure_classes
    assert CertificationFailure.REVOCATION.value in r.failure_classes

def test_version_mismatch_fails():
    r = certify(expected_version="2.0.0")
    assert r.status == CertificationStatus.FAIL
    assert CertificationFailure.VERSION_COMPATIBILITY.value in r.failure_classes
