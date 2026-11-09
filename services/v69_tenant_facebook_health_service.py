"""Trusted composition for read-only tenant Facebook health observation.

The service accepts only authenticated identity plus tenant resource IDs.
It resolves all connection/certification/account state server-side, persists
an append-only health receipt, and publishes through the already configured
canonical event bus. It does not expose provider credentials or mutation APIs.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from services.execution_ledger import ExecutionLedger
from services.v69_integration_evidence import SupabaseIntegrationEvidenceStore
from services.v69_tenant_facebook_connector_factory import TenantFacebookConnectorFactory
from services.v69_tenant_facebook_health_context import (
    TenantFacebookHealthContextResolver,
    TenantFacebookHealthContextError,
)
from services.v69_tenant_facebook_health_producer import (
    TenantFacebookHealthProducer,
    TenantHealthPublication,
    TenantHealthPublisherError,
)
from services.v69_tenant_social_account_resolver import TenantSocialAccountResolver


class TenantFacebookHealthServiceError(RuntimeError):
    """The trusted tenant health-observation service is not safely composed."""


class TenantFacebookHealthObservationService:
    """Observe exactly one authenticated workspace/brand/connection."""

    def __init__(
        self,
        *,
        context_resolver: TenantFacebookHealthContextResolver,
        producer: TenantFacebookHealthProducer,
        evidence_store: SupabaseIntegrationEvidenceStore,
    ) -> None:
        if not isinstance(context_resolver, TenantFacebookHealthContextResolver):
            raise TenantFacebookHealthServiceError("trusted_context_resolver_required")
        if not isinstance(producer, TenantFacebookHealthProducer):
            raise TenantFacebookHealthServiceError("trusted_health_producer_required")
        if not isinstance(evidence_store, SupabaseIntegrationEvidenceStore):
            raise TenantFacebookHealthServiceError("durable_supabase_evidence_store_required")

        client = getattr(context_resolver, "_client", None)
        if client is None or getattr(evidence_store, "_client", None) is not client:
            raise TenantFacebookHealthServiceError("context_and_evidence_database_mismatch")
        account_resolver = getattr(context_resolver, "_accounts", None)
        if getattr(account_resolver, "_client", None) is not client:
            raise TenantFacebookHealthServiceError("account_resolver_database_mismatch")
        if getattr(getattr(producer, "_resolver", None), "_client", None) is not client:
            raise TenantFacebookHealthServiceError("producer_account_resolver_database_mismatch")

        factory = getattr(producer, "_connector_factory", None)
        ledger = getattr(factory, "_ledger", None)
        if not isinstance(ledger, ExecutionLedger) or getattr(ledger, "_db", None) is not client:
            raise TenantFacebookHealthServiceError("durable_execution_ledger_database_mismatch")
        if getattr(ledger, "_allow_in_memory", True):
            raise TenantFacebookHealthServiceError("execution_ledger_memory_fallback_forbidden")

        event_bus = getattr(producer, "_event_bus", None)
        if getattr(event_bus, "supabase", None) is not client:
            raise TenantFacebookHealthServiceError("canonical_event_bus_database_mismatch")
        if not getattr(event_bus, "_tenant_health_evidence_ready", False):
            raise TenantFacebookHealthServiceError("health_evidence_schema_not_enabled")

        self._context_resolver = context_resolver
        self._producer = producer
        self._evidence_store = evidence_store
        self._client = client
        self._ledger = ledger
        self._cooldown_seconds = 300
        self._connection_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._locally_blocked_windows: set[tuple[str, str, int]] = set()

    async def observe(
        self,
        identity: Any,
        *,
        brand_id: str,
        connection_id: str,
    ) -> TenantHealthPublication:
        """Resolve trusted context, then perform one read-only provider probe."""
        try:
            context = self._context_resolver.resolve_for_identity(
                identity,
                brand_id=brand_id,
                connection_id=connection_id,
            )
        except TenantFacebookHealthContextError:
            raise
        except Exception as exc:
            raise TenantFacebookHealthServiceError(
                "tenant_health_context_resolution_failed"
            ) from exc

        if (
            context.workspace_id != getattr(identity, "workspace_id", None)
            or context.binding.workspace_id != context.workspace_id
            or context.binding.brand_id != brand_id
            or context.connection.connection_id != connection_id
            or context.connection.workspace_id != context.workspace_id
            or context.connection.brand_id != context.binding.brand_id
            or context.connection.social_account_id != context.binding.account_id
            or context.registry.workspace_id != context.workspace_id
            or context.registry.connection_id != connection_id
            or context.registry.brand_id != context.binding.brand_id
            or context.registry.social_account_id != context.binding.account_id
        ):
            raise TenantFacebookHealthServiceError("resolved_context_binding_mismatch")

        # The durable ledger gives each workspace/connection one observation
        # lease per five-minute window. A process-local lock closes the same-
        # worker race; the database lease coordinates separate processes.
        key = (context.workspace_id, connection_id)
        lock = self._connection_locks.setdefault(key, asyncio.Lock())
        async with lock:
            now = datetime.now(timezone.utc)
            window = int(now.timestamp() // self._cooldown_seconds)
            window_key = (context.workspace_id, connection_id, window)
            if window_key in self._locally_blocked_windows:
                raise TenantFacebookHealthServiceError(
                    "health_observation_cooldown_or_in_progress"
                )
            idempotency_key = ExecutionLedger.make_idempotency_key(
                "tenant-facebook-health-observe",
                context.workspace_id,
                connection_id,
                str(window),
            )
            try:
                execution = self._ledger.begin(
                    workspace_id=context.workspace_id,
                    action="connector_health_observe",
                    idempotency_key=idempotency_key,
                    provider="facebook",
                    metadata={
                        "brand_id": brand_id,
                        "observation_window": window,
                    },
                )
                claimed = self._ledger.claim(execution, lease_seconds=300)
            except Exception as exc:
                raise TenantFacebookHealthServiceError(
                    "health_observation_ledger_unavailable"
                ) from exc

            if (
                claimed.status != "started"
                or claimed.locked_by != self._ledger.worker_id
            ):
                raise TenantFacebookHealthServiceError(
                    "health_observation_cooldown_or_in_progress"
                )

            # No connection, contract, registry, token, or Page object is
            # accepted from the caller. All are supplied by the trusted
            # resolver above. Any uncertain failure consumes this time window:
            # never repeat a provider probe just because event publication or
            # ledger finalization had an ambiguous outcome.
            try:
                publication = await self._producer.observe(
                    identity,
                    brand_id=brand_id,
                    connection=context.connection,
                    contract=context.contract,
                    registry=context.registry,
                    evidence_store=self._evidence_store,
                )
            except Exception as exc:
                receipt = getattr(exc, "receipt", None)
                evidence = {}
                if receipt is not None:
                    evidence = {
                        "evidence_id": getattr(receipt, "evidence_id", None),
                        "observation_id": getattr(receipt, "observation_id", None),
                    }
                try:
                    self._ledger.complete(
                        claimed,
                        status="uncertain",
                        error="health_observation_unconfirmed",
                        evidence=evidence,
                    )
                except Exception:
                    self._locally_blocked_windows.add(window_key)
                raise

            try:
                self._ledger.complete(
                    claimed,
                    status="succeeded",
                    evidence={
                        "evidence_id": publication.receipt.evidence_id,
                        "observation_id": publication.receipt.observation_id,
                        "event_id": publication.event_id,
                    },
                )
            except Exception as exc:
                self._locally_blocked_windows.add(window_key)
                raise TenantFacebookHealthServiceError(
                    "health_observation_ledger_finalization_failed"
                ) from exc
            return publication


def build_tenant_facebook_health_observation_service(
    *,
    runtime: Any,
    service_role_client: Any,
    facebook_service: Any = None,
) -> TenantFacebookHealthObservationService:
    """Compose the service from one explicit service-role client and boot runtime."""
    if runtime is None or not getattr(runtime, "health_evidence_ready", False):
        raise TenantFacebookHealthServiceError("tenant_health_runtime_not_enabled")
    event_bus = getattr(runtime, "event_bus", None)
    if event_bus is None or getattr(event_bus, "supabase", None) is not service_role_client:
        raise TenantFacebookHealthServiceError("canonical_service_role_bus_required")
    if service_role_client is None:
        raise TenantFacebookHealthServiceError("service_role_client_required")

    if facebook_service is None:
        from services.facebook import facebook as facebook_service

    account_resolver = TenantSocialAccountResolver(service_role_client)
    context_resolver = TenantFacebookHealthContextResolver(
        service_role_client,
        social_account_resolver=account_resolver,
    )
    ledger = ExecutionLedger(db=service_role_client, allow_in_memory=False)
    connector_factory = TenantFacebookConnectorFactory(
        facebook_service=facebook_service,
        ledger=ledger,
    )
    producer = runtime.create_health_producer(
        resolver=account_resolver,
        connector_factory=connector_factory,
    )
    evidence_store = SupabaseIntegrationEvidenceStore(service_role_client)
    return TenantFacebookHealthObservationService(
        context_resolver=context_resolver,
        producer=producer,
        evidence_store=evidence_store,
    )


__all__ = [
    "TenantFacebookHealthServiceError",
    "TenantFacebookHealthObservationService",
    "build_tenant_facebook_health_observation_service",
]
