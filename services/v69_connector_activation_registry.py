"""V69.8 — runtime connector activation and certification registry.

Certification is evidence; registry state determines whether that evidence is
still usable for a tenant connection. Registry state never grants authority.
Execution eligibility is only a governance input and must still be enforced by
connection state, DecisionGate/Action Kernel, durable execution and provider
reconciliation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Mapping

from services.business_integration_platform import ConnectionState, IntegrationConnection
from services.v69_connector_certification import ConnectorCertificationResult, CertificationStatus


class RegistryState(str, Enum):
    REGISTERED = "registered"
    CERTIFIED = "certified"
    ACTIVE = "active"
    DEGRADED = "degraded"
    EXPIRED = "expired"
    REVOKED = "revoked"
    SUPERSEDED = "superseded"


class RegistryFailure(str, Enum):
    CERTIFICATION_FAILED = "certification_failed"
    CERTIFICATION_NOT_PROVEN = "certification_not_proven"
    EXPIRED = "expired"
    REVOKED = "revoked"
    DEGRADED = "degraded"
    VERSION_DRIFT = "version_drift"
    TENANT_MISMATCH = "tenant_mismatch"
    CONNECTION_STATE = "connection_state"


@dataclass(frozen=True)
class CertificationEvidence:
    certification_version: str
    connector_key: str
    connector_version: str
    fingerprint: str
    status: CertificationStatus
    live_provider_verified: bool
    certified_at: datetime
    expires_at: datetime | None = None
    evidence_ref: str = ""

    def __post_init__(self) -> None:
        if self.certified_at.tzinfo is None:
            raise ValueError("certified_at must be timezone-aware")
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            raise ValueError("expires_at must be timezone-aware")

    def is_expired(self, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        now = now or datetime.now(timezone.utc)
        return now >= self.expires_at


@dataclass(frozen=True)
class ConnectorRegistryRecord:
    workspace_id: str
    connection_id: str
    connector_key: str
    connector_version: str
    state: RegistryState
    evidence: CertificationEvidence | None = None
    generation: int = 1
    reason: str = ""
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # Optional resource binding; required by account-specific connector composition.
    brand_id: str | None = None
    social_account_id: str | None = None
    authority: str = field(default="none", init=False)
    execution_effect: str = field(default="none", init=False)

    def __post_init__(self) -> None:
        if not self.workspace_id.strip() or not self.connection_id.strip() or not self.connector_key.strip():
            raise ValueError("workspace_id, connection_id and connector_key are required")
        if bool(self.brand_id) != bool(self.social_account_id):
            raise ValueError("brand_id and social_account_id must be set together")
        if self.authority != "none" or self.execution_effect != "none":
            raise ValueError("registry records cannot grant authority or execution")

    @property
    def activation_eligible(self) -> bool:
        return self.state == RegistryState.ACTIVE and self.evidence is not None and not self.evidence.is_expired()


def fingerprint_certification(result: ConnectorCertificationResult) -> str:
    """Stable evidence fingerprint over the certification facts, not secrets."""
    import hashlib
    material = "|".join([
        result.certification_version, result.connector_key, result.provider,
        result.version, result.status.value,
        ",".join(result.failure_classes), str(result.live_provider_verified),
    ])
    return hashlib.sha256(material.encode()).hexdigest()


def register_certification(
    connection: IntegrationConnection,
    result: ConnectorCertificationResult,
    *,
    now: datetime | None = None,
    ttl_days: int | None = 30,
    evidence_ref: str = "",
) -> ConnectorRegistryRecord:
    """Convert certification evidence into registry state, fail-closed."""
    now = now or datetime.now(timezone.utc)
    if connection.connector_key != result.connector_key:
        raise ValueError(RegistryFailure.TENANT_MISMATCH.value + ": connector mismatch")
    if not result.certified:
        return ConnectorRegistryRecord(
            connection.workspace_id, connection.connection_id, result.connector_key,
            result.version, RegistryState.REGISTERED, reason=RegistryFailure.CERTIFICATION_FAILED.value,
            updated_at=now,
        )
    expiry = now + timedelta(days=ttl_days) if ttl_days is not None else None
    evidence = CertificationEvidence(
        result.certification_version, result.connector_key, result.version,
        fingerprint_certification(result), result.status, result.live_provider_verified,
        now, expiry, evidence_ref,
    )
    return ConnectorRegistryRecord(
        connection.workspace_id, connection.connection_id, result.connector_key,
        result.version, RegistryState.CERTIFIED, evidence=evidence, updated_at=now,
    )


def transition_registry(
    record: ConnectorRegistryRecord,
    target: RegistryState,
    *,
    connection: IntegrationConnection,
    now: datetime | None = None,
    observed_connector_version: str | None = None,
    health_ok: bool = True,
    revocation_confirmed: bool = False,
) -> ConnectorRegistryRecord:
    """Pure registry transition guard; never authenticates or executes."""
    now = now or datetime.now(timezone.utc)
    if record.workspace_id != connection.workspace_id or record.connection_id != connection.connection_id:
        raise ValueError(RegistryFailure.TENANT_MISMATCH.value)
    if record.connector_key != connection.connector_key:
        raise ValueError(RegistryFailure.TENANT_MISMATCH.value)
    if observed_connector_version is not None and observed_connector_version != record.connector_version:
        return _replace(record, RegistryState.SUPERSEDED, RegistryFailure.VERSION_DRIFT.value, now)
    if record.evidence and record.evidence.is_expired(now):
        if target not in {RegistryState.EXPIRED, RegistryState.REVOKED}:
            raise ValueError(RegistryFailure.EXPIRED.value)
        return _replace(record, RegistryState.EXPIRED, RegistryFailure.EXPIRED.value, now)
    if target == RegistryState.ACTIVE:
        if record.evidence is None or record.state not in {RegistryState.CERTIFIED, RegistryState.ACTIVE}:
            raise ValueError(RegistryFailure.CERTIFICATION_NOT_PROVEN.value)
        if not health_ok:
            return _replace(record, RegistryState.DEGRADED, RegistryFailure.DEGRADED.value, now)
        if connection.state not in {ConnectionState.OBSERVE_ONLY, ConnectionState.SUGGEST_ENABLED, ConnectionState.EXECUTION_ENABLED}:
            raise ValueError(RegistryFailure.CONNECTION_STATE.value)
    if target == RegistryState.REVOKED and not revocation_confirmed:
        raise ValueError(RegistryFailure.REVOKED.value + ": explicit confirmation required")
    return _replace(record, target, "", now)


def evaluate_activation(
    record: ConnectorRegistryRecord,
    connection: IntegrationConnection,
    *,
    now: datetime | None = None,
    observed_connector_version: str | None = None,
) -> dict[str, object]:
    """Return eligibility without changing state or granting authority."""
    now = now or datetime.now(timezone.utc)
    reasons: list[str] = []
    if record.workspace_id != connection.workspace_id or record.connection_id != connection.connection_id:
        reasons.append(RegistryFailure.TENANT_MISMATCH.value)
    if record.connector_key != connection.connector_key:
        reasons.append(RegistryFailure.TENANT_MISMATCH.value)
    if record.state in {RegistryState.REVOKED, RegistryState.EXPIRED, RegistryState.SUPERSEDED, RegistryState.DEGRADED}:
        reasons.append(record.state.value)
    if record.evidence is None:
        reasons.append(RegistryFailure.CERTIFICATION_NOT_PROVEN.value)
    elif record.evidence.status != CertificationStatus.PASS:
        reasons.append(RegistryFailure.CERTIFICATION_FAILED.value)
    elif record.evidence.is_expired(now):
        reasons.append(RegistryFailure.EXPIRED.value)
    if observed_connector_version is not None and observed_connector_version != record.connector_version:
        reasons.append(RegistryFailure.VERSION_DRIFT.value)
    if connection.state == ConnectionState.REVOKED:
        reasons.append(RegistryFailure.REVOKED.value)
    eligible = not reasons and record.state == RegistryState.ACTIVE
    return {"eligible": eligible, "workspace_id": connection.workspace_id,
            "connection_id": connection.connection_id, "connector_key": connection.connector_key,
            "reasons": tuple(dict.fromkeys(reasons)), "authority_granted": False}


def _replace(record: ConnectorRegistryRecord, state: RegistryState, reason: str, now: datetime) -> ConnectorRegistryRecord:
    return ConnectorRegistryRecord(record.workspace_id, record.connection_id, record.connector_key,
                                   record.connector_version, state, record.evidence,
                                   record.generation + 1, reason, now)


__all__ = ["RegistryState", "RegistryFailure", "CertificationEvidence", "ConnectorRegistryRecord",
           "fingerprint_certification", "register_certification", "transition_registry", "evaluate_activation"]
