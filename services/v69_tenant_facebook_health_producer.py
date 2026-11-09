"""Signed, evidence-first publication of tenant-scoped Facebook health observations.

The caller supplies a resolver, connector factory, durable evidence store and
already-configured UnifiedEventBus. This class never creates credentials,
producer capabilities, signing keys, database clients or event-bus instances.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from core.event_provenance import (
    ProducerCapability,
    ProducerRegistry,
    current_actor_principal,
)
from services.v69_integration_evidence import DurableHealthObservation
from services.v69_tenant_social_account_resolver import (
    TenantSocialAccountBinding,
    TenantSocialAccountResolver,
)
from services.v69_tenant_facebook_connector_factory import (
    TenantFacebookConnectorFactory,
)


PRODUCER_ID = "kdo-tenant-health-facebook"
PRODUCER_CLASS = "tenant_connector_health"
EVENT_NAME = "connector.health.observed"
_EVIDENCE_HASH = re.compile(r"^[0-9a-f]{64}$")


class TenantHealthPublisherError(RuntimeError):
    """A tenant health observation could not be safely published."""

    def __init__(
        self,
        code: str,
        *,
        receipt: DurableHealthObservation | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        # A persisted receipt is retained when event publication fails, so a
        # caller can reconcile it without probing the provider a second time.
        self.receipt = receipt


@dataclass(frozen=True)
class TenantHealthPublication:
    workspace_id: str
    brand_id: str
    connection_id: str
    event_id: str
    receipt: DurableHealthObservation


class TenantFacebookHealthProducer:
    """Observe, durably record, then publish a signed tenant-scoped health event.

    Producer capabilities are supplied by trusted startup composition and are
    never minted here. The event bus must have the same registry, an injected
    signer, a Supabase persistence client and durable provenance storage enabled.
    """

    def __init__(
        self,
        *,
        resolver: TenantSocialAccountResolver,
        connector_factory: TenantFacebookConnectorFactory,
        event_bus: Any,
        producer_registry: ProducerRegistry,
        producer_capability: ProducerCapability,
    ) -> None:
        if resolver is None or connector_factory is None:
            raise TenantHealthPublisherError("tenant_resolver_and_factory_required")
        if not isinstance(producer_registry, ProducerRegistry):
            raise TenantHealthPublisherError("trusted_producer_registry_required")
        if not isinstance(producer_capability, ProducerCapability):
            raise TenantHealthPublisherError("producer_capability_required")
        principal = producer_capability.principal
        if (
            principal.producer_id != PRODUCER_ID
            or principal.producer_class != PRODUCER_CLASS
        ):
            raise TenantHealthPublisherError("facebook_health_producer_capability_mismatch")
        if event_bus is None:
            raise TenantHealthPublisherError("unified_event_bus_required")
        if getattr(event_bus, "_producer_registry", None) is not producer_registry:
            raise TenantHealthPublisherError("event_bus_producer_registry_mismatch")
        if not getattr(event_bus, "_provenance_storage_enabled", False):
            raise TenantHealthPublisherError("durable_provenance_storage_not_enabled")
        if not callable(getattr(event_bus, "_provenance_signer", None)):
            raise TenantHealthPublisherError("durable_provenance_signer_required")
        if getattr(event_bus, "supabase", None) is None:
            raise TenantHealthPublisherError("durable_event_database_required")

        self._resolver = resolver
        self._connector_factory = connector_factory
        self._event_bus = event_bus
        self._producer_registry = producer_registry
        self._producer_capability = producer_capability

    async def observe(
        self,
        identity: Any,
        *,
        brand_id: str,
        connection: Any,
        contract: Any,
        registry: Any,
        evidence_store: Any,
    ) -> TenantHealthPublication:
        """Resolve one authenticated tenant account and publish its signed observation."""
        try:
            binding = self._resolver.resolve_for_identity(
                identity,
                brand_id=brand_id,
                platform="facebook",
            )
        except Exception as exc:
            raise TenantHealthPublisherError("tenant_account_resolution_failed") from exc

        # Verify this exact in-process capability against the trusted registry
        # and authenticated actor before probing the provider.
        try:
            self._producer_registry.verify(
                self._producer_capability,
                workspace_id=binding.workspace_id,
                actor=current_actor_principal(),
                brand_id=binding.brand_id,
            )
        except Exception as exc:
            raise TenantHealthPublisherError("producer_capability_scope_invalid") from exc

        try:
            gateway = self._connector_factory.build_gateway(
                binding=binding,
                connection=connection,
                contract=contract,
                registry=registry,
            )
        except Exception as exc:
            raise TenantHealthPublisherError("tenant_connector_composition_failed") from exc

        try:
            receipt = gateway.observe_health(evidence_store)
        except Exception as exc:
            raise TenantHealthPublisherError("health_observation_persistence_failed") from exc

        try:
            self._validate_receipt(receipt, binding=binding, connection=connection)
        except TenantHealthPublisherError as exc:
            raise TenantHealthPublisherError(
                exc.code,
                receipt=receipt if isinstance(receipt, DurableHealthObservation) else None,
            ) from exc

        payload = {
            "service": f"facebook:{receipt.connection_id}",
            "status": receipt.health_state,
            "health_state": receipt.health_state,
            "connection_id": receipt.connection_id,
            "social_account_id": receipt.social_account_id,
            "connector_key": receipt.connector_key,
            "provider": contract.provider,
            "connector_version": contract.version,
            "observation_id": receipt.observation_id,
            "evidence_id": receipt.evidence_id,
            "evidence_hash": receipt.evidence_hash,
            "observed_at": receipt.observed_at.isoformat(),
        }
        try:
            event_id = await self._event_bus.publish(
                EVENT_NAME,
                payload,
                source="kdo.tenant_facebook_health",
                correlation_id=receipt.evidence_id,
                workspace_id=binding.workspace_id,
                brand_id=binding.brand_id,
                producer_capability=self._producer_capability,
            )
        except Exception as exc:
            raise TenantHealthPublisherError(
                "signed_health_event_publish_failed",
                receipt=receipt,
            ) from exc
        if not isinstance(event_id, str) or not event_id.strip():
            raise TenantHealthPublisherError(
                "signed_health_event_publish_unconfirmed",
                receipt=receipt,
            )

        return TenantHealthPublication(
            workspace_id=binding.workspace_id,
            brand_id=binding.brand_id,
            connection_id=connection.connection_id,
            event_id=event_id,
            receipt=receipt,
        )

    @staticmethod
    def _validate_receipt(
        receipt: Any,
        *,
        binding: TenantSocialAccountBinding,
        connection: Any,
    ) -> None:
        if not isinstance(receipt, DurableHealthObservation):
            raise TenantHealthPublisherError("durable_health_receipt_required")
        if (
            receipt.workspace_id != binding.workspace_id
            or receipt.brand_id != binding.brand_id
            or receipt.social_account_id != binding.account_id
            or receipt.connection_id != connection.connection_id
            or receipt.connector_key != "facebook"
        ):
            raise TenantHealthPublisherError("health_receipt_tenant_binding_mismatch")
        if receipt.health_state not in {"healthy", "degraded", "unavailable", "unknown"}:
            raise TenantHealthPublisherError("health_receipt_state_invalid")
        if not receipt.evidence_id or not receipt.observation_id:
            raise TenantHealthPublisherError("health_receipt_identity_missing")
        if not isinstance(receipt.evidence_hash, str) or not _EVIDENCE_HASH.fullmatch(
            receipt.evidence_hash
        ):
            raise TenantHealthPublisherError("health_receipt_hash_invalid")
        if not isinstance(receipt.observed_at, datetime) or receipt.observed_at.tzinfo is None:
            raise TenantHealthPublisherError("health_receipt_timestamp_invalid")


__all__ = [
    "PRODUCER_ID",
    "PRODUCER_CLASS",
    "EVENT_NAME",
    "TenantHealthPublisherError",
    "TenantHealthPublication",
    "TenantFacebookHealthProducer",
]
