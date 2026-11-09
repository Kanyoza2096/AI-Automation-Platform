from types import SimpleNamespace

import pytest

from core.event_provenance import ProducerRegistry
from core.unified_bus import UnifiedEventBus
from services.execution_ledger import ExecutionLedger
from services.v69_integration_evidence import SupabaseIntegrationEvidenceStore
from services.v69_tenant_facebook_connector_factory import TenantFacebookConnectorFactory
from services.v69_tenant_facebook_health_context import TenantFacebookHealthContextResolver
from services.v69_tenant_facebook_health_producer import (
    PRODUCER_CLASS,
    PRODUCER_ID,
    TenantFacebookHealthProducer,
    TenantHealthPublisherError,
)
from services.v69_tenant_facebook_health_service import (
    TenantFacebookHealthObservationService,
    TenantFacebookHealthServiceError,
    build_tenant_facebook_health_observation_service,
)
from services.v69_tenant_social_account_resolver import TenantSocialAccountResolver


class FakeClient:
    def table(self, _name):
        raise AssertionError("No database operation is expected in composition tests")


class ContextResolver(TenantFacebookHealthContextResolver):
    def __init__(self, client, context):
        super().__init__(client, social_account_resolver=TenantSocialAccountResolver(client))
        self.context = context
        self.calls = []

    def resolve_for_identity(self, identity, *, brand_id, connection_id, now=None):
        self.calls.append((identity, brand_id, connection_id))
        return self.context


def build_context():
    binding = SimpleNamespace(
        workspace_id="workspace-a",
        brand_id="brand-a",
        account_id="page-a",
    )
    connection = SimpleNamespace(
        workspace_id="workspace-a",
        connection_id="connection-a",
        brand_id="brand-a",
        social_account_id="page-a",
    )
    contract = SimpleNamespace(connector_key="facebook", version="1.0.0", provider="facebook")
    registry = SimpleNamespace(
        workspace_id="workspace-a",
        connection_id="connection-a",
        brand_id="brand-a",
        social_account_id="page-a",
    )
    return SimpleNamespace(
        workspace_id="workspace-a",
        binding=binding,
        connection=connection,
        contract=contract,
        registry=registry,
    )


def configure_ledger(ledger, *, status="started", locked_by=None):
    state = {"completed": [], "begun": []}
    record = SimpleNamespace(
        execution_id="execution-a",
        workspace_id="workspace-a",
        action="connector_health_observe",
        idempotency_key="idempotency-a",
        status=status,
        locked_by=ledger.worker_id if locked_by is None and status == "started" else locked_by,
    )
    ledger.begin = lambda **kwargs: state["begun"].append(kwargs) or record
    ledger.claim = lambda _record, **kwargs: record
    ledger.complete = lambda _record, **kwargs: state["completed"].append(kwargs) or record
    return state, record


def build_dependencies(client):
    bus = UnifiedEventBus(supabase_client=client)
    bus._tenant_health_evidence_ready = True
    bus._provenance_storage_enabled = True
    bus._provenance_signer = lambda *_args, **_kwargs: {}
    registry = ProducerRegistry({PRODUCER_ID: PRODUCER_CLASS})
    capability = registry.issue(PRODUCER_ID)
    bus._producer_registry = registry
    account_resolver = TenantSocialAccountResolver(client)
    ledger = ExecutionLedger(db=client, allow_in_memory=False)
    factory = TenantFacebookConnectorFactory(
        facebook_service=object(),
        ledger=ledger,
    )
    producer = TenantFacebookHealthProducer(
        resolver=account_resolver,
        connector_factory=factory,
        event_bus=bus,
        producer_registry=registry,
        producer_capability=capability,
    )
    return bus, registry, capability, producer


@pytest.mark.asyncio
async def test_service_resolves_trusted_context_and_never_accepts_caller_connection_objects():
    client = FakeClient()
    context = build_context()
    context_resolver = ContextResolver(client, context)
    _bus, _registry, _capability, producer = build_dependencies(client)
    evidence_store = SupabaseIntegrationEvidenceStore(client)
    captured = {}

    async def observe(identity, **kwargs):
        captured["identity"] = identity
        captured.update(kwargs)
        return SimpleNamespace(
            event_id="event-a",
            receipt=SimpleNamespace(
                evidence_id="evidence-a",
                observation_id="observation-a",
            ),
        )

    producer.observe = observe
    ledger_state, _record = configure_ledger(producer._connector_factory._ledger)
    service = TenantFacebookHealthObservationService(
        context_resolver=context_resolver,
        producer=producer,
        evidence_store=evidence_store,
    )
    identity = SimpleNamespace(valid=True, workspace_id="workspace-a")

    result = await service.observe(
        identity,
        brand_id="brand-a",
        connection_id="connection-a",
    )

    assert result.event_id == "event-a"
    assert ledger_state["completed"][0]["status"] == "succeeded"
    assert ledger_state["completed"][0]["evidence"]["evidence_id"] == "evidence-a"
    assert context_resolver.calls == [(identity, "brand-a", "connection-a")]
    assert captured["identity"] is identity
    assert captured["connection"] is context.connection
    assert captured["contract"] is context.contract
    assert captured["registry"] is context.registry
    assert captured["evidence_store"] is evidence_store


@pytest.mark.asyncio
async def test_service_rejects_context_whose_resource_binding_does_not_match_request():
    client = FakeClient()
    context = build_context()
    context.registry.social_account_id = "another-page"
    context_resolver = ContextResolver(client, context)
    _bus, _registry, _capability, producer = build_dependencies(client)
    evidence_store = SupabaseIntegrationEvidenceStore(client)
    service = TenantFacebookHealthObservationService(
        context_resolver=context_resolver,
        producer=producer,
        evidence_store=evidence_store,
    )

    with pytest.raises(TenantFacebookHealthServiceError, match="resolved_context_binding_mismatch"):
        await service.observe(
            SimpleNamespace(valid=True, workspace_id="workspace-a"),
            brand_id="brand-a",
            connection_id="connection-a",
        )


def test_service_rejects_evidence_store_from_another_database_client():
    client_a = FakeClient()
    client_b = FakeClient()
    context_resolver = ContextResolver(client_a, build_context())
    _bus, _registry, _capability, producer = build_dependencies(client_a)

    with pytest.raises(TenantFacebookHealthServiceError, match="context_and_evidence_database_mismatch"):
        TenantFacebookHealthObservationService(
            context_resolver=context_resolver,
            producer=producer,
            evidence_store=SupabaseIntegrationEvidenceStore(client_b),
        )


def test_builder_requires_the_same_explicit_service_role_client_as_the_canonical_bus():
    client_a = FakeClient()
    client_b = FakeClient()
    bus, registry, capability, _producer = build_dependencies(client_a)

    runtime = SimpleNamespace(
        health_evidence_ready=True,
        event_bus=bus,
        create_health_producer=lambda *, resolver, connector_factory: TenantFacebookHealthProducer(
            resolver=resolver,
            connector_factory=connector_factory,
            event_bus=bus,
            producer_registry=registry,
            producer_capability=capability,
        ),
    )

    with pytest.raises(TenantFacebookHealthServiceError, match="canonical_service_role_bus_required"):
        build_tenant_facebook_health_observation_service(
            runtime=runtime,
            service_role_client=client_b,
            facebook_service=object(),
        )


def test_builder_composes_all_durable_dependencies_from_one_client():
    client = FakeClient()
    bus, registry, capability, _producer = build_dependencies(client)

    runtime = SimpleNamespace(
        health_evidence_ready=True,
        event_bus=bus,
        create_health_producer=lambda *, resolver, connector_factory: TenantFacebookHealthProducer(
            resolver=resolver,
            connector_factory=connector_factory,
            event_bus=bus,
            producer_registry=registry,
            producer_capability=capability,
        ),
    )

    service = build_tenant_facebook_health_observation_service(
        runtime=runtime,
        service_role_client=client,
        facebook_service=object(),
    )

    assert service._client is client
    assert service._producer._event_bus is bus
    assert service._producer._resolver._client is client
    assert service._producer._connector_factory._ledger._db is client
    assert service._evidence_store._client is client
    assert service._context_resolver._client is client


@pytest.mark.asyncio
async def test_service_rejects_a_recent_or_concurrent_probe_without_calling_provider():
    client = FakeClient()
    context_resolver = ContextResolver(client, build_context())
    _bus, _registry, _capability, producer = build_dependencies(client)
    ledger_state, _record = configure_ledger(
        producer._connector_factory._ledger,
        status="succeeded",
        locked_by=None,
    )
    called = False

    async def observe(*_args, **_kwargs):
        nonlocal called
        called = True

    producer.observe = observe
    service = TenantFacebookHealthObservationService(
        context_resolver=context_resolver,
        producer=producer,
        evidence_store=SupabaseIntegrationEvidenceStore(client),
    )

    with pytest.raises(TenantFacebookHealthServiceError, match="health_observation_cooldown_or_in_progress"):
        await service.observe(
            SimpleNamespace(valid=True, workspace_id="workspace-a"),
            brand_id="brand-a",
            connection_id="connection-a",
        )

    assert called is False
    assert ledger_state["completed"] == []


@pytest.mark.asyncio
async def test_uncertain_event_publication_consumes_window_and_records_durable_receipt():
    client = FakeClient()
    context_resolver = ContextResolver(client, build_context())
    _bus, _registry, _capability, producer = build_dependencies(client)
    ledger_state, _record = configure_ledger(producer._connector_factory._ledger)
    receipt = SimpleNamespace(
        evidence_id="evidence-persisted",
        observation_id="observation-persisted",
    )

    async def observe(*_args, **_kwargs):
        raise TenantHealthPublisherError(
            "signed_health_event_publish_failed",
            receipt=receipt,
        )

    producer.observe = observe
    service = TenantFacebookHealthObservationService(
        context_resolver=context_resolver,
        producer=producer,
        evidence_store=SupabaseIntegrationEvidenceStore(client),
    )

    with pytest.raises(TenantHealthPublisherError, match="signed_health_event_publish_failed"):
        await service.observe(
            SimpleNamespace(valid=True, workspace_id="workspace-a"),
            brand_id="brand-a",
            connection_id="connection-a",
        )

    assert ledger_state["completed"][0]["status"] == "uncertain"
    assert ledger_state["completed"][0]["evidence"] == {
        "evidence_id": "evidence-persisted",
        "observation_id": "observation-persisted",
    }


@pytest.mark.asyncio
async def test_ledger_failure_fails_closed_before_provider_probe():
    client = FakeClient()
    context_resolver = ContextResolver(client, build_context())
    _bus, _registry, _capability, producer = build_dependencies(client)
    called = False

    async def observe(*_args, **_kwargs):
        nonlocal called
        called = True

    producer.observe = observe
    producer._connector_factory._ledger.begin = lambda **_kwargs: (_ for _ in ()).throw(
        RuntimeError("ledger unavailable")
    )
    service = TenantFacebookHealthObservationService(
        context_resolver=context_resolver,
        producer=producer,
        evidence_store=SupabaseIntegrationEvidenceStore(client),
    )

    with pytest.raises(TenantFacebookHealthServiceError, match="health_observation_ledger_unavailable"):
        await service.observe(
            SimpleNamespace(valid=True, workspace_id="workspace-a"),
            brand_id="brand-a",
            connection_id="connection-a",
        )

    assert called is False
