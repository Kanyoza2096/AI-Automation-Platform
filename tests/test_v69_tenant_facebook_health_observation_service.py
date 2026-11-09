from types import SimpleNamespace

import pytest

from services.v69_tenant_facebook_health_observation_service import (
    TenantFacebookHealthObservationService,
)
from services.v69_tenant_facebook_health_producer import TenantHealthPublisherError


class FakeContextResolver:
    def __init__(self, context=None, error=None):
        self.context = context or SimpleNamespace(
            workspace_id="workspace-a",
            binding=SimpleNamespace(
                workspace_id="workspace-a",
                brand_id="brand-a",
                account_id="page-a",
            ),
            connection=SimpleNamespace(
                workspace_id="workspace-a",
                connection_id="connection-a",
                brand_id="brand-a",
                social_account_id="page-a",
            ),
            contract=SimpleNamespace(connector_key="facebook", version="1.0.0"),
            registry=SimpleNamespace(
                workspace_id="workspace-a",
                connection_id="connection-a",
                brand_id="brand-a",
                social_account_id="page-a",
            ),
        )
        self.error = error
        self.calls = []

    def resolve_for_identity(self, identity, *, brand_id, connection_id):
        self.calls.append((identity, brand_id, connection_id))
        if self.error:
            raise self.error
        return self.context


class FakeProducer:
    def __init__(self):
        self.calls = []

    async def observe(self, identity, **kwargs):
        self.calls.append((identity, kwargs))
        return SimpleNamespace(
            workspace_id="workspace-a",
            brand_id="brand-a",
            connection_id="connection-a",
            event_id="event-a",
            receipt=SimpleNamespace(
                social_account_id="page-a",
                health_state="healthy",
                evidence_id="evidence-a",
                observation_id="observation-a",
            ),
        )


class FakeRuntime:
    def __init__(self, client, *, ready=True):
        self.event_bus = SimpleNamespace(supabase=client)
        self.health_evidence_ready = ready
        self.factory_calls = []

    def create_health_producer(self, *, resolver, connector_factory):
        self.factory_calls.append((resolver, connector_factory))
        return FakeProducer()


class FakeClient:
    pass


def make_service(*, ready=True, context_resolver=None, producer=None, client=None):
    client = client or FakeClient()
    runtime = FakeRuntime(client, ready=ready)
    context_resolver = context_resolver or FakeContextResolver()
    producer = producer or FakeProducer()
    service = TenantFacebookHealthObservationService(
        provenance_runtime=runtime,
        supabase_client=client,
        facebook_service=object(),
        context_resolver=context_resolver,
        connector_factory=object(),
        evidence_store=object(),
        producer=producer,
    )
    return service, runtime, context_resolver, producer, client


@pytest.mark.asyncio
async def test_service_accepts_only_identifiers_and_passes_resolved_context_to_producer():
    service, runtime, context_resolver, producer, _ = make_service()
    identity = SimpleNamespace(valid=True, workspace_id="workspace-a")

    result = await service.observe(
        identity,
        brand_id="brand-a",
        connection_id="connection-a",
    )

    assert context_resolver.calls == [(identity, "brand-a", "connection-a")]
    assert len(producer.calls) == 1
    passed_identity, kwargs = producer.calls[0]
    assert passed_identity is identity
    assert kwargs["brand_id"] == "brand-a"
    assert kwargs["connection"].connection_id == "connection-a"
    assert kwargs["contract"].connector_key == "facebook"
    assert kwargs["registry"].connection_id == "connection-a"
    assert kwargs["evidence_store"] is not None
    assert result.workspace_id == "workspace-a"
    assert result.social_account_id == "page-a"
    assert result.health_state == "healthy"
    assert result.evidence_id == "evidence-a"
    assert result.event_id == "event-a"
    assert runtime.factory_calls == []


def test_service_fails_closed_before_composition_when_health_evidence_is_not_ready():
    client = FakeClient()
    runtime = FakeRuntime(client, ready=False)

    with pytest.raises(TenantHealthPublisherError, match="health_evidence_schema_not_enabled"):
        TenantFacebookHealthObservationService(
            provenance_runtime=runtime,
            supabase_client=client,
            facebook_service=object(),
        )


def test_service_rejects_a_supabase_client_different_from_the_canonical_event_bus():
    runtime_client = FakeClient()
    supplied_client = FakeClient()
    runtime = FakeRuntime(runtime_client)

    with pytest.raises(TenantHealthPublisherError, match="canonical_event_database_mismatch"):
        TenantFacebookHealthObservationService(
            provenance_runtime=runtime,
            supabase_client=supplied_client,
            facebook_service=object(),
        )


@pytest.mark.asyncio
async def test_context_resolution_failure_prevents_producer_invocation():
    error = ValueError("not eligible")
    context_resolver = FakeContextResolver(error=error)
    producer = FakeProducer()
    service, _, _, _, _ = make_service(
        context_resolver=context_resolver,
        producer=producer,
    )

    with pytest.raises(ValueError, match="not eligible"):
        await service.observe(
            SimpleNamespace(valid=True, workspace_id="workspace-a"),
            brand_id="brand-a",
            connection_id="connection-a",
        )

    assert producer.calls == []
