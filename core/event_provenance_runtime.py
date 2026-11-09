"""Trusted startup composition for durable tenant event provenance.

This module is the only place that installs producer capabilities into the
canonical in-process event bus. It never creates a second bus, provisions
keys, changes database state, or enables signing until the event schema is
confirmed by a read-only column probe.
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from dataclasses import dataclass, field
from typing import Any, Mapping

from core.event_provenance import (
    ProducerCapability,
    ProducerRegistry,
    VerifiedEventProvenance,
)
from core.event_provenance_key_provider import (
    DurableProvenanceKeyProvider,
    ProvenanceKeyConfigError,
)
from core.unified_bus import UnifiedEventBus
# Stable identity constants are repeated here to keep boot-time composition
# independent from the provider/connector import graph.
PRODUCER_ID = "kdo-tenant-health-facebook"
PRODUCER_CLASS = "tenant_connector_health"


@dataclass(frozen=True)
class TenantFacebookProvenanceRuntime:
    """Producer-specific runtime capability minted once during trusted boot."""

    registry: ProducerRegistry = field(repr=False)
    capability: ProducerCapability = field(repr=False)
    active_key_id: str
    health_evidence_ready: bool
    event_bus: UnifiedEventBus = field(repr=False)
    status: str = "enabled"

    def create_health_producer(self, *, resolver: Any, connector_factory: Any):
        """Bind request-time dependencies only after health evidence readiness."""
        if not self.health_evidence_ready:
            from services.v69_tenant_facebook_health_producer import TenantHealthPublisherError
            raise TenantHealthPublisherError("health_evidence_schema_not_enabled")
        from services.v69_tenant_facebook_health_producer import TenantFacebookHealthProducer

        return TenantFacebookHealthProducer(
            resolver=resolver,
            connector_factory=connector_factory,
            event_bus=self.event_bus,
            producer_registry=self.registry,
            producer_capability=self.capability,
        )

    def __repr__(self) -> str:
        return (
            "TenantFacebookProvenanceRuntime("
            f"status={self.status!r}, "
            f"producer_id={PRODUCER_ID!r}, "
            f"key_id={self.active_key_id!r}, "
            f"health_evidence_ready={self.health_evidence_ready})"
        )


@dataclass(frozen=True)
class ProvenanceInstallResult:
    enabled: bool
    reason: str
    runtime: TenantFacebookProvenanceRuntime | None = field(
        default=None, repr=False
    )


_SIGNING_ENV = (
    "KDO_EVENT_PROVENANCE_ACTIVE_SIGNING_KEY_ID",
    "KDO_EVENT_PROVENANCE_SIGNING_PRODUCER_ID",
    "KDO_EVENT_PROVENANCE_SIGNING_PRODUCER_CLASS",
    "KDO_EVENT_PROVENANCE_SIGNING_PRIVATE_KEY_B64",
)
_PUBLIC_ENV = (
    "KDO_EVENT_PROVENANCE_PUBLIC_KEYS_JSON",
    "KDO_EVENT_PROVENANCE_KEY_OWNERS_JSON",
)


def _probe_columns(client: Any, table: str, columns: str) -> bool:
    """Read-only PostgREST schema probe; limit(0) retrieves no table rows."""
    try:
        response = client.table(table).select(columns).limit(0).execute()
        return getattr(response, "data", None) is not None
    except Exception:
        return False


def _probe_provenance_column(client: Any) -> bool:
    return _probe_columns(client, "events", "provenance_envelope")


def _probe_health_evidence_schema(client: Any) -> bool:
    """Check the tables/columns required by migrations 159–162, without writes."""
    required = (
        ("integration_connections", "id,workspace_id,brand_id,social_account_id"),
        ("connector_certification_registry", "workspace_id,connection_id,connector_key,connector_version,registry_state,brand_id,social_account_id"),
        ("integration_evidence", "evidence_id,workspace_id,connection_id,sequence_no,metadata,evidence_hash"),
        ("connector_health_observations", "id,workspace_id,connection_id,connector_key,observed_at,health_state,evidence_ref"),
    )
    return all(_probe_columns(client, table, columns) for table, columns in required)


_HEALTH_EVIDENCE_READY_ENV = "KDO_TENANT_FACEBOOK_HEALTH_EVIDENCE_READY"

_FACEBOOK_HEALTH_FIELDS = frozenset({
    "service",
    "status",
    "health_state",
    "connection_id",
    "social_account_id",
    "connector_key",
    "provider",
    "connector_version",
    "observation_id",
    "evidence_id",
    "evidence_hash",
    "observed_at",
})
_NERVE_ADDED_FIELDS = frozenset({"_nerve", "workspace_id", "correlation_id", "brand_id"})


def _validate_facebook_health_signing_request(
    provenance: Any,
    *,
    event_id: Any,
    event_name: Any,
    payload: Any,
    brand_id: Any,
) -> None:
    """Enforce the complete non-secret evidence contract before signing."""
    if event_name != "connector.health.observed":
        raise ValueError("facebook_health_event_name_not_allowed")
    if (
        not isinstance(provenance, VerifiedEventProvenance)
        or provenance.producer.producer_id != PRODUCER_ID
        or provenance.producer.producer_class != PRODUCER_CLASS
        or not provenance.workspace_id
        or provenance.actor is None
        or provenance.actor.workspace_id != provenance.workspace_id
        or not isinstance(brand_id, str)
        or not brand_id.strip()
        or provenance.brand_id != brand_id
    ):
        raise ValueError("facebook_health_provenance_scope_invalid")
    if not isinstance(event_id, str) or not event_id.strip() or len(event_id) > 160:
        raise ValueError("facebook_health_event_id_invalid")
    if not isinstance(payload, Mapping):
        raise ValueError("facebook_health_payload_invalid")

    keys = set(payload)
    if not _FACEBOOK_HEALTH_FIELDS.issubset(keys):
        raise ValueError("facebook_health_payload_incomplete")
    if not keys.issubset(_FACEBOOK_HEALTH_FIELDS | _NERVE_ADDED_FIELDS):
        raise ValueError("facebook_health_payload_contains_unapproved_fields")

    nerve = payload.get("_nerve")
    if (
        not isinstance(nerve, Mapping)
        or nerve.get("event_id") != event_id
        or nerve.get("event_name") != event_name
        or nerve.get("workspace_id") != provenance.workspace_id
        or nerve.get("brand_id") != brand_id
        or payload.get("workspace_id") != provenance.workspace_id
        or payload.get("brand_id") != brand_id
    ):
        raise ValueError("facebook_health_nerve_scope_mismatch")

    status = payload.get("status")
    if status not in {"healthy", "degraded", "unavailable", "unknown"}:
        raise ValueError("facebook_health_state_invalid")
    if payload.get("health_state") != status:
        raise ValueError("facebook_health_state_mismatch")
    connection_id = payload.get("connection_id")
    if not isinstance(connection_id, str) or not connection_id.strip() or len(connection_id) > 160:
        raise ValueError("facebook_health_connection_id_invalid")
    if payload.get("service") != f"facebook:{connection_id}":
        raise ValueError("facebook_health_service_binding_mismatch")
    for field_name in ("social_account_id", "observation_id", "evidence_id"):
        value = payload.get(field_name)
        if not isinstance(value, str) or not value.strip() or len(value) > 160:
            raise ValueError(f"facebook_health_{field_name}_invalid")
    if payload.get("connector_key") != "facebook":
        raise ValueError("facebook_health_connector_invalid")
    if payload.get("provider") not in {"facebook", "meta"}:
        raise ValueError("facebook_health_provider_invalid")
    version = payload.get("connector_version")
    if not isinstance(version, str) or not version.strip() or len(version) > 80:
        raise ValueError("facebook_health_connector_version_invalid")
    evidence_hash = payload.get("evidence_hash")
    if (
        not isinstance(evidence_hash, str)
        or len(evidence_hash) != 64
        or re.fullmatch(r"[0-9a-f]{64}", evidence_hash) is None
    ):
        raise ValueError("facebook_health_evidence_hash_invalid")
    observed_at = payload.get("observed_at")
    if not isinstance(observed_at, str):
        raise ValueError("facebook_health_observed_at_invalid")
    try:
        parsed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("facebook_health_observed_at_invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError("facebook_health_observed_at_timezone_required")


def install_tenant_facebook_provenance(
    event_bus: Any,
    *,
    supabase_client: Any = None,
    environ: Mapping[str, str] | None = None,
    schema_probe: Any = None,
) -> ProvenanceInstallResult:
    """Install one producer capability on the already-created canonical bus.

    Missing or invalid configuration, absent database schema, or a bus that
    has already started all leave the bus unchanged. This is intentionally
    opt-in: a missing migration or secret cannot degrade legacy event traffic.
    """
    source = os.environ if environ is None else environ

    existing = getattr(event_bus, "_tenant_facebook_provenance_runtime", None)
    if isinstance(existing, TenantFacebookProvenanceRuntime):
        return ProvenanceInstallResult(True, "already_installed", existing)

    if not isinstance(event_bus, UnifiedEventBus):
        return ProvenanceInstallResult(False, "canonical_unified_event_bus_required")
    if getattr(event_bus, "_running", False):
        return ProvenanceInstallResult(False, "event_bus_already_started")
    if (
        getattr(event_bus, "_producer_registry", None) is not None
        or getattr(event_bus, "_provenance_signer", None) is not None
        or getattr(event_bus, "_provenance_storage_enabled", False)
    ):
        return ProvenanceInstallResult(False, "event_bus_provenance_already_owned")

    # An entirely unconfigured environment is a normal disabled state.
    if not any(source.get(name) for name in (*_SIGNING_ENV, *_PUBLIC_ENV)):
        return ProvenanceInstallResult(False, "key_configuration_absent")

    if any(not source.get(name) for name in (*_SIGNING_ENV, *_PUBLIC_ENV)):
        return ProvenanceInstallResult(False, "key_configuration_incomplete")

    try:
        key_provider = DurableProvenanceKeyProvider.from_environment(source)
    except ProvenanceKeyConfigError:
        return ProvenanceInstallResult(False, "key_configuration_invalid")

    if not key_provider.can_sign or not key_provider.active_key_id:
        return ProvenanceInstallResult(False, "producer_signing_key_unavailable")

    _, owners = key_provider.public_verification_config()
    if owners.get(key_provider.active_key_id) != PRODUCER_ID:
        return ProvenanceInstallResult(False, "active_key_not_owned_by_facebook_producer")

    bus_client = getattr(event_bus, "supabase", None)
    if bus_client is None:
        return ProvenanceInstallResult(False, "event_database_unavailable")
    # The producer's account/evidence reads and durable event writes require
    # the explicitly configured service-role client. An anon/RLS client is
    # never an acceptable fallback for this privileged backend pipeline.
    if supabase_client is None:
        return ProvenanceInstallResult(False, "service_role_client_required")
    if supabase_client is not bus_client:
        return ProvenanceInstallResult(False, "canonical_event_database_mismatch")
    client = supabase_client

    probe = schema_probe or _probe_provenance_column
    if not probe(client):
        return ProvenanceInstallResult(False, "provenance_schema_not_ready")

    # The event envelope column alone is not enough to run the tenant health
    # producer. Migrations 159–162 establish its account-bound evidence and
    # atomic health-write path. Require both an explicit operator attestation
    # and read-only table/column probes before exposing that producer factory.
    health_evidence_ready = (
        str(source.get(_HEALTH_EVIDENCE_READY_ENV, "")).strip().lower() == "true"
        and _probe_health_evidence_schema(client)
    )

    # Build all state locally first. Mutate the canonical bus only after every
    # gate has passed, so partial configuration cannot leave it half-enabled.
    try:
        registry = ProducerRegistry({PRODUCER_ID: PRODUCER_CLASS})
        capability = registry.issue(PRODUCER_ID)
        runtime = TenantFacebookProvenanceRuntime(
            registry=registry,
            capability=capability,
            active_key_id=key_provider.active_key_id,
            health_evidence_ready=health_evidence_ready,
            event_bus=event_bus,
        )

        def sign_facebook_health_event(provenance: Any, **event: Any) -> dict[str, Any]:
            # The signing key is scoped to one producer and one complete,
            # evidence-backed event schema. Arbitrary payloads cannot borrow it.
            _validate_facebook_health_signing_request(provenance, **event)
            return key_provider.sign(provenance, **event)
    except Exception:
        return ProvenanceInstallResult(False, "producer_registry_initialization_failed")

    event_bus._producer_registry = registry
    event_bus._provenance_signer = sign_facebook_health_event
    event_bus._provenance_storage_enabled = True
    event_bus._tenant_health_evidence_ready = health_evidence_ready
    event_bus._tenant_facebook_provenance_runtime = runtime

    return ProvenanceInstallResult(True, "installed", runtime)


__all__ = [
    "TenantFacebookProvenanceRuntime",
    "ProvenanceInstallResult",
    "install_tenant_facebook_provenance",
]
