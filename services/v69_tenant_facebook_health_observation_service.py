"""Authenticated, observe-only Facebook health observation service."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from services.execution_ledger import ExecutionLedger
from services.v69_integration_evidence import SupabaseIntegrationEvidenceStore
from services.v69_tenant_facebook_connector_factory import TenantFacebookConnectorFactory
from services.v69_tenant_facebook_health_context import (
    TenantFacebookHealthContext,
    TenantFacebookHealthContextResolver,
)
from services.v69_tenant_facebook_health_producer import (
    TenantFacebookHealthProducer,
    TenantHealthPublication,
    TenantHealthPublisherError,
)
from services.v69_tenant_social_account_resolver import TenantSocialAccountResolver


@dataclass(frozen=True)
class TenantFacebookHealthObservationResult:
    workspace_id: str
    brand_id: str
    connection_id: str
    social_account_id: str
    health_state: str
    evidence_id: str
    observation_id: str
    event_id: str


class TenantFacebookHealthObservationService:
    """Compose only trusted tenant context and durable observe-only dependencies.

    The public operation accepts identifiers, never caller-created connection,
    contract, registry, account, or evidence-store objects.
    """

    def __init__(
        self,
        *,
        provenance_runtime: Any,
        supabase_client: Any,
        facebook_service: Any,
        context_resolver: TenantFacebookHealthContextResolver | None = None,
        account_resolver: TenantSocialAccountResolver | None = None,
        connector_factory: TenantFacebookConnectorFactory | None = None,
        evidence_store: SupabaseIntegrationEvidenceStore | None = None,
        producer: TenantFacebookHealthProducer | None = None,
    ) -> None:
        if provenance_runtime is None or not getattr(
            provenance_runtime, "health_evidence_ready", False
        ):
            raise TenantHealthPublisherError("health_evidence_schema_not_enabled")
        if supabase_client is None:
            raise TenantHealthPublisherError("durable_event_database_required")
        if getattr(getattr(provenance_runtime, "event_bus", None), "supabase", None) is not supabase_client:
            raise TenantHealthPublisherError("canonical_event_database_mismatch")
        if facebook_service is None:
            raise TenantHealthPublisherError("facebook_service_required")

        self._account_resolver = account_resolver or TenantSocialAccountResolver(
            supabase_client
        )
        self._context_resolver = context_resolver or TenantFacebookHealthContextResolver(
            supabase_client,
            social_account_resolver=self._account_resolver,
        )
        ledger = ExecutionLedger(db=supabase_client, allow_in_memory=False)
        self._connector_factory = connector_factory or TenantFacebookConnectorFactory(
            facebook_service=facebook_service,
            ledger=ledger,
        )
        self._evidence_store = evidence_store or SupabaseIntegrationEvidenceStore(
            supabase_client
        )
        self._producer = producer or provenance_runtime.create_health_producer(
            resolver=self._account_resolver,
            connector_factory=self._connector_factory,
        )

    async def observe(
        self,
        identity: Any,
        *,
        brand_id: str,
        connection_id: str,
    ) -> TenantFacebookHealthObservationResult:
        """Resolve, observe, persist and publish one signed health observation."""
        context: TenantFacebookHealthContext = await asyncio.to_thread(
            self._context_resolver.resolve_for_identity,
            identity,
            brand_id=brand_id,
            connection_id=connection_id,
        )
        publication: TenantHealthPublication = await self._producer.observe(
            identity,
            brand_id=brand_id,
            connection=context.connection,
            contract=context.contract,
            registry=context.registry,
            evidence_store=self._evidence_store,
        )
        receipt = publication.receipt
        return TenantFacebookHealthObservationResult(
            workspace_id=publication.workspace_id,
            brand_id=publication.brand_id,
            connection_id=publication.connection_id,
            social_account_id=receipt.social_account_id,
            health_state=receipt.health_state,
            evidence_id=receipt.evidence_id,
            observation_id=receipt.observation_id,
            event_id=publication.event_id,
        )


__all__ = [
    "TenantFacebookHealthObservationResult",
    "TenantFacebookHealthObservationService",
]
