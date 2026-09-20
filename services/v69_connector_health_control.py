"""V69.9 — connector health, drift and re-certification control plane.

Health observations are evidence, not authority. The control plane fails closed:
provider/credential/capability/mapping/version drift can downgrade a connector
before stale certification is reused. Re-certification creates a new evidence
fingerprint; it never silently restores execution authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Iterable

from services.business_integration_platform import ConnectionState, IntegrationConnection
from services.v69_connector_activation_registry import (
    ConnectorRegistryRecord, RegistryFailure, RegistryState, fingerprint_certification,
    register_certification,
)
from services.v69_connector_certification import ConnectorCertificationResult, CertificationStatus


class HealthState(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class DriftKind(str, Enum):
    NONE = "none"
    CREDENTIAL_REVOKED = "credential_revoked"
    API_VERSION = "api_version_drift"
    CAPABILITY = "capability_drift"
    EVENT_MAPPING = "event_mapping_drift"
    CONNECTOR_VERSION = "connector_version_drift"
    IDENTITY = "identity_drift"
    PROVIDER_BEHAVIOR = "provider_behavior_drift"
    TENANT_SCOPE = "tenant_scope_drift"


class ControlAction(str, Enum):
    NO_CHANGE = "no_change"
    DEGRADE = "degrade"
    SUSPEND = "suspend"
    REVOKE = "revoke"
    REQUIRE_RECERTIFICATION = "require_recertification"


@dataclass(frozen=True)
class HealthObservation:
    workspace_id: str
    connection_id: str
    connector_key: str
    observed_at: datetime
    state: HealthState
    drift: tuple[DriftKind, ...] = ()
    provider_status_code: int | None = None
    latency_ms: int | None = None
    evidence_ref: str = ""

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        if not self.workspace_id.strip() or not self.connection_id.strip() or not self.connector_key.strip():
            raise ValueError("workspace_id, connection_id and connector_key are required")
        if self.latency_ms is not None and self.latency_ms < 0:
            raise ValueError("latency_ms cannot be negative")


@dataclass(frozen=True)
class ControlDecision:
    action: ControlAction
    reasons: tuple[str, ...]
    safe_for_execution: bool
    requires_recertification: bool
    authority_granted: bool = False


def assess_health(
    observation: HealthObservation,
    connection: IntegrationConnection,
    record: ConnectorRegistryRecord,
    *,
    now: datetime | None = None,
) -> ControlDecision:
    """Translate observed health/drift into a fail-closed registry action."""
    now = now or datetime.now(timezone.utc)
    reasons: list[str] = []
    if observation.workspace_id != connection.workspace_id or observation.workspace_id != record.workspace_id:
        reasons.append(RegistryFailure.TENANT_MISMATCH.value)
    if observation.connection_id != connection.connection_id or observation.connection_id != record.connection_id:
        reasons.append(RegistryFailure.TENANT_MISMATCH.value)
    if observation.connector_key != connection.connector_key or observation.connector_key != record.connector_key:
        reasons.append(RegistryFailure.TENANT_MISMATCH.value)

    tenant_mismatch = bool(reasons)
    if tenant_mismatch:
        return ControlDecision(ControlAction.REVOKE, (RegistryFailure.TENANT_MISMATCH.value,), False, True)

    drifts = set(observation.drift)
    hard_revocation = DriftKind.CREDENTIAL_REVOKED in drifts
    identity_failure = bool(drifts & {DriftKind.IDENTITY, DriftKind.TENANT_SCOPE})
    recert_drift = bool(drifts & {
        DriftKind.API_VERSION, DriftKind.CAPABILITY, DriftKind.EVENT_MAPPING,
        DriftKind.CONNECTOR_VERSION, DriftKind.PROVIDER_BEHAVIOR,
    })

    if hard_revocation:
        reasons.append(DriftKind.CREDENTIAL_REVOKED.value)
    if identity_failure:
        reasons.extend(sorted(d.value for d in drifts & {DriftKind.IDENTITY, DriftKind.TENANT_SCOPE}))
    if recert_drift:
        reasons.extend(sorted(d.value for d in drifts & {
            DriftKind.API_VERSION, DriftKind.CAPABILITY, DriftKind.EVENT_MAPPING,
            DriftKind.CONNECTOR_VERSION, DriftKind.PROVIDER_BEHAVIOR,
        }))
    if observation.state in {HealthState.DEGRADED, HealthState.UNAVAILABLE}:
        reasons.append(observation.state.value)
    if record.evidence is not None and record.evidence.is_expired(now):
        reasons.append(RegistryFailure.EXPIRED.value)

    reasons = list(dict.fromkeys(reasons))
    if identity_failure or hard_revocation:
        return ControlDecision(ControlAction.REVOKE, tuple(reasons), False, True)
    if recert_drift or (record.state == RegistryState.SUPERSEDED):
        return ControlDecision(ControlAction.REQUIRE_RECERTIFICATION, tuple(reasons), False, True)
    if observation.state == HealthState.UNAVAILABLE:
        return ControlDecision(ControlAction.SUSPEND, tuple(reasons), False, False)
    if observation.state == HealthState.DEGRADED or record.state == RegistryState.DEGRADED:
        return ControlDecision(ControlAction.DEGRADE, tuple(reasons), False, False)
    if record.state != RegistryState.ACTIVE or record.evidence is None or record.evidence.is_expired(now):
        return ControlDecision(ControlAction.REQUIRE_RECERTIFICATION, tuple(reasons), False, True)
    return ControlDecision(ControlAction.NO_CHANGE, tuple(reasons), True, False)


def apply_control_decision(
    record: ConnectorRegistryRecord,
    connection: IntegrationConnection,
    decision: ControlDecision,
    *,
    now: datetime | None = None,
) -> ConnectorRegistryRecord:
    """Apply only state changes; never grants authority."""
    from services.v69_connector_activation_registry import transition_registry
    now = now or datetime.now(timezone.utc)
    if decision.action == ControlAction.REVOKE:
        return transition_registry(record, RegistryState.REVOKED, connection=connection, now=now, revocation_confirmed=True)
    if decision.action == ControlAction.SUSPEND:
        return transition_registry(record, RegistryState.DEGRADED, connection=connection, now=now, health_ok=False)
    if decision.action == ControlAction.DEGRADE:
        return transition_registry(record, RegistryState.DEGRADED, connection=connection, now=now, health_ok=False)
    if decision.action == ControlAction.REQUIRE_RECERTIFICATION:
        return transition_registry(record, RegistryState.SUPERSEDED, connection=connection, now=now)
    return record


def re_certify(
    connection: IntegrationConnection,
    result: ConnectorCertificationResult,
    *,
    now: datetime | None = None,
    ttl_days: int | None = 30,
    evidence_ref: str = "",
) -> ConnectorRegistryRecord:
    """Create fresh evidence after a drift event; activation remains separate."""
    if result.status != CertificationStatus.PASS:
        return register_certification(connection, result, now=now, ttl_days=ttl_days, evidence_ref=evidence_ref)
    return register_certification(connection, result, now=now, ttl_days=ttl_days, evidence_ref=evidence_ref)


def health_observation_from(
    connection: IntegrationConnection,
    *,
    state: HealthState,
    drift: Iterable[DriftKind] = (),
    observed_at: datetime | None = None,
    provider_status_code: int | None = None,
    latency_ms: int | None = None,
    evidence_ref: str = "",
) -> HealthObservation:
    return HealthObservation(
        connection.workspace_id, connection.connection_id, connection.connector_key,
        observed_at or datetime.now(timezone.utc), state, tuple(dict.fromkeys(drift)),
        provider_status_code, latency_ms, evidence_ref,
    )


__all__ = [
    "HealthState", "DriftKind", "ControlAction", "HealthObservation", "ControlDecision",
    "assess_health", "apply_control_decision", "re_certify", "health_observation_from",
]
