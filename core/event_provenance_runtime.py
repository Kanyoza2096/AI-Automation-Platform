"""Trusted startup composition for durable tenant event provenance.

This module is the only place that installs producer capabilities into the
canonical in-process event bus. It never creates a second bus, provisions
keys, changes database state, or enables signing until the event schema is
confirmed by a read-only column probe.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Mapping

from core.event_provenance import ProducerCapability, ProducerRegistry
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
    if supabase_client is not None and supabase_client is not bus_client:
        return ProvenanceInstallResult(False, "canonical_event_database_mismatch")
    client = bus_client

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
            # A component capability is not a blanket licence to sign every
            # event type. This key is scoped to the one tenant-health event.
            if event.get("event_name") != "connector.health.observed":
                raise ValueError("facebook_health_event_name_not_allowed")
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
