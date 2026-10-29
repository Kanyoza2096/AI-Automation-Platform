from datetime import datetime, timezone, timedelta
import pytest

from services.business_integration_platform import AutonomyMode, ConnectionState, ConnectorContract, IntegrationConnection
from services.v69_connector_activation_registry import RegistryState, register_certification, transition_registry
from services.v69_connector_certification import CertificationStatus, ConnectorCertificationResult
from services.v69_connector_health_control import DriftKind, HealthState, health_observation_from
from services.v69_integration_lifecycle import IntegrationLifecycleOrchestrator


def contract(certified=True):
    return ConnectorContract(
        connector_key="facebook", provider="facebook", version="1",
        capabilities=("publish", "edit"), inbound_events=("message",),
        outbound_actions=("publish", "edit"), risk_by_action={"publish":"medium", "edit":"high"},
        certified=certified,
    )


def result():
    return ConnectorCertificationResult(
        certification_version="v69.6", connector_key="facebook", provider="facebook", version="1",
        status=CertificationStatus.PASS, checks=(), live_provider_verified=False,
    )


def test_lifecycle_forbids_direct_execution_jump():
    c = IntegrationConnection("w1", "facebook", ConnectionState.OBSERVE_ONLY, ("publish",), AutonomyMode.OBSERVE, "c1")
    o = IntegrationLifecycleOrchestrator("w1", c, contract())
    plan = o.plan(ConnectionState.EXECUTION_ENABLED, certification_ok=True, required_capabilities=("publish",))
    assert not plan.allowed
    assert "invalid_transition" in plan.reasons


def test_lifecycle_requires_registry_for_execution():
    c = IntegrationConnection("w1", "facebook", ConnectionState.SUGGEST_ENABLED, ("publish",), AutonomyMode.SUGGEST, "c1")
    o = IntegrationLifecycleOrchestrator("w1", c, contract())
    plan = o.plan(ConnectionState.EXECUTION_ENABLED, certification_ok=True, required_capabilities=("publish",))
    assert not plan.allowed
    assert "registry_required" in plan.reasons


def test_lifecycle_can_activate_after_certification_and_registry():
    now = datetime.now(timezone.utc)
    c = IntegrationConnection("w1", "facebook", ConnectionState.SUGGEST_ENABLED, ("publish",), AutonomyMode.SUGGEST, "c1")
    reg = register_certification(c, result(), now=now)
    reg = transition_registry(reg, RegistryState.ACTIVE, connection=c, now=now)
    o = IntegrationLifecycleOrchestrator("w1", c, contract(), reg)
    plan = o.plan(ConnectionState.EXECUTION_ENABLED, certification_ok=True, required_capabilities=("publish",))
    assert plan.allowed
    c2 = o.transition(ConnectionState.EXECUTION_ENABLED, certification_ok=True, required_capabilities=("publish",))
    assert c2.state == ConnectionState.EXECUTION_ENABLED
    assert c2.autonomy_mode == AutonomyMode.EXECUTE


def test_health_revocation_converges_connection_and_registry():
    now = datetime.now(timezone.utc)
    c = IntegrationConnection("w1", "facebook", ConnectionState.EXECUTION_ENABLED, ("publish",), AutonomyMode.EXECUTE, "c1")
    reg = register_certification(c, result(), now=now)
    reg = transition_registry(reg, RegistryState.ACTIVE, connection=c, now=now)
    o = IntegrationLifecycleOrchestrator("w1", c, contract(), reg)
    obs = health_observation_from(c, state=HealthState.HEALTHY, drift=(DriftKind.CREDENTIAL_REVOKED,), observed_at=now)
    o2 = o.apply_health(obs, now=now)
    assert o2.connection.state == ConnectionState.REVOKED
    assert o2.registry.state == RegistryState.REVOKED


def test_version_drift_forces_recertification_and_removes_execution_eligibility():
    now = datetime.now(timezone.utc)
    c = IntegrationConnection("w1", "facebook", ConnectionState.EXECUTION_ENABLED, ("publish",), AutonomyMode.EXECUTE, "c1")
    reg = register_certification(c, result(), now=now)
    reg = transition_registry(reg, RegistryState.ACTIVE, connection=c, now=now)
    o = IntegrationLifecycleOrchestrator("w1", c, contract(), reg)
    obs = health_observation_from(c, state=HealthState.HEALTHY, drift=(DriftKind.API_VERSION,), observed_at=now)
    o2 = o.apply_health(obs, now=now)
    assert o2.connection.state == ConnectionState.DEGRADED
    assert o2.registry.state == RegistryState.SUPERSEDED
    assert not o2.snapshot(now=now).execution_eligible


def test_cross_tenant_orchestrator_construction_is_rejected():
    c = IntegrationConnection("w2", "facebook", ConnectionState.OBSERVE_ONLY, ("publish",), AutonomyMode.OBSERVE, "c1")
    with pytest.raises(ValueError, match="tenant_mismatch"):
        IntegrationLifecycleOrchestrator("w1", c, contract())
