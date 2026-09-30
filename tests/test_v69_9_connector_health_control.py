from datetime import datetime, timezone, timedelta

from services.business_integration_platform import ConnectionState, IntegrationConnection
from services.v69_connector_activation_registry import RegistryState, register_certification, transition_registry
from services.v69_connector_health_control import (
    HealthState, DriftKind, ControlAction, assess_health, apply_control_decision, health_observation_from,
)
from services.v69_reference_connector_certification import certify_facebook_reference

NOW = datetime(2026, 9, 14, tzinfo=timezone.utc)

def conn(tenant="tenant-a", state=ConnectionState.SUGGEST_ENABLED):
    return IntegrationConnection(tenant, "facebook", state, ("publish", "lookup", "edit", "delete", "comments"), connection_id="c1")

def active_record(c):
    r = register_certification(c, certify_facebook_reference(workspace_id=c.workspace_id).result, now=NOW)
    return transition_registry(r, RegistryState.ACTIVE, connection=c, now=NOW)

def test_healthy_active_connector_remains_eligible():
    c = conn(); r = active_record(c)
    o = health_observation_from(c, state=HealthState.HEALTHY, observed_at=NOW)
    d = assess_health(o, c, r, now=NOW)
    assert d.action == ControlAction.NO_CHANGE
    assert d.safe_for_execution is True

def test_api_version_drift_requires_recertification():
    c = conn(); r = active_record(c)
    o = health_observation_from(c, state=HealthState.HEALTHY, drift=[DriftKind.API_VERSION], observed_at=NOW)
    d = assess_health(o, c, r, now=NOW)
    assert d.action == ControlAction.REQUIRE_RECERTIFICATION
    assert d.safe_for_execution is False
    nr = apply_control_decision(r, c, d, now=NOW)
    assert nr.state == RegistryState.SUPERSEDED

def test_credential_revocation_is_hard_revoke():
    c = conn(); r = active_record(c)
    o = health_observation_from(c, state=HealthState.UNAVAILABLE, drift=[DriftKind.CREDENTIAL_REVOKED], observed_at=NOW)
    d = assess_health(o, c, r, now=NOW)
    assert d.action == ControlAction.REVOKE
    assert d.safe_for_execution is False
    assert apply_control_decision(r, c, d, now=NOW).state == RegistryState.REVOKED

def test_mapping_and_capability_drift_cannot_remain_active():
    c = conn(); r = active_record(c)
    o = health_observation_from(c, state=HealthState.HEALTHY, drift=[DriftKind.CAPABILITY, DriftKind.EVENT_MAPPING], observed_at=NOW)
    d = assess_health(o, c, r, now=NOW)
    assert d.action == ControlAction.REQUIRE_RECERTIFICATION

def test_provider_unavailable_suspends_without_authority():
    c = conn(); r = active_record(c)
    o = health_observation_from(c, state=HealthState.UNAVAILABLE, observed_at=NOW)
    d = assess_health(o, c, r, now=NOW)
    assert d.action == ControlAction.SUSPEND
    nr = apply_control_decision(r, c, d, now=NOW)
    assert nr.state == RegistryState.DEGRADED
    assert nr.authority == "none"

def test_cross_tenant_observation_fails_closed():
    c = conn(); r = active_record(c)
    other = conn("tenant-b")
    o = health_observation_from(other, state=HealthState.HEALTHY, observed_at=NOW)
    d = assess_health(o, c, r, now=NOW)
    assert d.action == ControlAction.REVOKE
    assert "tenant_mismatch" in d.reasons

def test_expired_evidence_requires_recertification():
    c = conn(); r = active_record(c)
    o = health_observation_from(c, state=HealthState.HEALTHY, observed_at=NOW + timedelta(days=31))
    d = assess_health(o, c, r, now=NOW + timedelta(days=31))
    assert d.action == ControlAction.REQUIRE_RECERTIFICATION
