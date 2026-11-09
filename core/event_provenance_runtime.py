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
from services.v69_tenant_facebook_health_producer import (
    PRODUCER_CLASS,
    PRODUCER_ID,
)


@dataclass(frozen=True)
class TenantFacebookProvenanceRuntime:
    """Producer-specific runtime capability minted once during trusted boot."""

    registry: ProducerRegistry = field(repr=False)
    capability: ProducerCapability = field(repr=False)
    key_provider: DurableProvenanceKeyProvider = field(repr=False)
    status: str = "enabled"

    def __repr__(self) -> str:
        return (
            "TenantFacebookProvenanceRuntime("
            f"status={self.status!r}, "
            f"producer_id={PRODUCER_ID!r}, "
            f"key_id={self.key_provider.active_key_id!r})"
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


def _probe_provenance_column(client: Any) -> bool:
    """Read-only PostgREST schema probe; limit(0) retrieves no event rows."""
    try:
        response = (
            client.table("events")
            .select("provenance_envelope")
            .limit(0)
            .execute()
        )
        return getattr(response, "data", None) is not None
    except Exception:
        return False


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

    if event_bus is None:
        return ProvenanceInstallResult(False, "canonical_event_bus_unavailable")
    if getattr(event_bus, "_running", False):
        return ProvenanceInstallResult(False, "event_bus_already_started")
    if (
        getattr(event_bus, "_producer_registry", None) is not None
        or getattr(event_bus, "_provenance_signer", None) is not None
        or getattr(event_bus, "_provenance_storage_enabled", False)
    ):
        return ProvenanceInstallResult(False, "event_bus_provenance_already_owned")

    if any(source.get(name) for name in (*_SIGNING_ENV, *_PUBLIC_ENV)) is False:
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

    client = supabase_client or getattr(event_bus, "supabase", None)
    if client is None:
        return ProvenanceInstallResult(False, "event_database_unavailable")

    probe = schema_probe or _probe_provenance_column
    if not probe(client):
        return ProvenanceInstallResult(False, "provenance_schema_not_ready")

    # Build all state locally first. Mutate the canonical bus only after every
    # gate has passed, so partial configuration cannot leave it half-enabled.
    try:
        registry = ProducerRegistry({PRODUCER_ID: PRODUCER_CLASS})
        capability = registry.issue(PRODUCER_ID)
        runtime = TenantFacebookProvenanceRuntime(
            registry=registry,
            capability=capability,
            key_provider=key_provider,
        )
    except Exception:
        return ProvenanceInstallResult(False, "producer_registry_initialization_failed")

    event_bus._producer_registry = registry
    event_bus._provenance_signer = key_provider.sign
    event_bus._provenance_storage_enabled = True
    event_bus._tenant_facebook_provenance_runtime = runtime

    return ProvenanceInstallResult(True, "installed", runtime)


__all__ = [
    "TenantFacebookProvenanceRuntime",
    "ProvenanceInstallResult",
    "install_tenant_facebook_provenance",
]
