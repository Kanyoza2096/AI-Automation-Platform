from datetime import datetime, timezone, timedelta

from services.business_integration_platform import ConnectionState, IntegrationConnection
from services.v69_connector_activation_registry import (
    RegistryState, RegistryFailure, register_certification, transition_registry,
    evaluate_activation,
)
from services.v69_reference_connector_certification import certify_facebook_reference


def conn(state=ConnectionState.SUGGEST_ENABLED, tenant="tenant-a"):
    return IntegrationConnection(tenant, "facebook", state,
                                 ("publish", "lookup", "edit", "delete", "comments"), connection_id="c1")


def test_certification_becomes_registry_evidence_without_authority():
    c = conn()
    r = register_certification(c, certify_facebook_reference(workspace_id="tenant-a").result,
                               now=datetime(2026, 9, 14, tzinfo=timezone.utc))
    assert r.state == RegistryState.CERTIFIED
    assert r.authority == "none"
    assert r.execution_effect == "none"
    assert not r.activation_eligible


def test_active_requires_valid_connection_state():
    c = conn()
    r = register_certification(c, certify_facebook_reference(workspace_id="tenant-a").result)
    active = transition_registry(r, RegistryState.ACTIVE, connection=c)
    assert active.activation_eligible
    assert evaluate_activation(active, c)["eligible"] is True


def test_expiry_blocks_activation():
    now = datetime(2026, 9, 14, tzinfo=timezone.utc)
    c = conn()
    r = register_certification(c, certify_facebook_reference(workspace_id="tenant-a").result, now=now, ttl_days=1)
    active = transition_registry(r, RegistryState.ACTIVE, connection=c, now=now)
    later = now + timedelta(days=2)
    decision = evaluate_activation(active, c, now=later)
    assert decision["eligible"] is False
    assert RegistryFailure.EXPIRED.value in decision["reasons"]


def test_version_drift_supersedes_record():
    c = conn()
    r = register_certification(c, certify_facebook_reference(workspace_id="tenant-a").result)
    superseded = transition_registry(r, RegistryState.ACTIVE, connection=c, observed_connector_version="2.0.0")
    assert superseded.state == RegistryState.SUPERSEDED
    assert superseded.reason == RegistryFailure.VERSION_DRIFT.value


def test_cross_tenant_activation_fails_closed():
    c = conn(tenant="tenant-a")
    r = register_certification(c, certify_facebook_reference(workspace_id="tenant-a").result)
    other = conn(tenant="tenant-b")
    try:
        transition_registry(r, RegistryState.ACTIVE, connection=other)
    except ValueError as exc:
        assert str(exc) == RegistryFailure.TENANT_MISMATCH.value
    else:
        raise AssertionError("cross-tenant transition must fail")


def test_revocation_requires_explicit_confirmation():
    c = conn()
    r = register_certification(c, certify_facebook_reference(workspace_id="tenant-a").result)
    try:
        transition_registry(r, RegistryState.REVOKED, connection=c)
    except ValueError:
        pass
    else:
        raise AssertionError("revocation must require explicit confirmation")
    revoked = transition_registry(r, RegistryState.REVOKED, connection=c, revocation_confirmed=True)
    assert revoked.state == RegistryState.REVOKED
    assert evaluate_activation(revoked, c)["eligible"] is False
