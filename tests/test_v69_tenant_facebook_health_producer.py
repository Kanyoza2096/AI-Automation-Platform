from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from api.support import auth_identity_var
from core.event_provenance import ProducerRegistry
from models.brand import Brand
from models.social_account import Platform, SocialAccount
from services.business_integration_platform import (
    AutonomyMode,
    ConnectionState,
    ConnectorContract,
    IntegrationConnection,
)
from services.execution_ledger import ExecutionLedger
from services.v69_connector_activation_registry import ConnectorRegistryRecord, RegistryState
from services.v69_integration_evidence import DurableHealthObservation
from services.v69_tenant_facebook_connector_factory import TenantFacebookConnectorFactory
from services.v69_tenant_facebook_health_producer import (
    EVENT_NAME,
    PRODUCER_CLASS,
    PRODUCER_ID,
    TenantFacebookHealthProducer,
    TenantHealthPublisherError,
)
from services.v69_tenant_social_account_resolver import TenantSocialAccountBinding


@pytest.fixture(autouse=True)
def authenticated_actor_context():
    identity = SimpleNamespace(
        valid=True,
        method="api_key",
        user_id="test-user",
        is_species=False,
        workspace_id="workspace-a",
    )
    token = auth_identity_var.set(identity)
    try:
        yield
    finally:
        auth_identity_var.reset(token)


class FakeResolver:
    def __init__(self, binding):
        self.binding = binding
        self.calls = []

    def resolve_for_identity(self, identity, *, brand_id, platform):
        self.calls.append((identity, brand_id, platform))
        if not getattr(identity, "valid", False):
            raise ValueError("unauthenticated")
        if identity.workspace_id != self.binding.workspace_id:
            raise ValueError("workspace mismatch")
        if brand_id != self.binding.brand_id or platform != "facebook":
            raise ValueError("binding mismatch")
        return self.binding


class FakeFacebookService:
    def __init__(self):
        self.calls = []

    def validate_account_token(self, page_id, page_token):
        self.calls.append((page_id, page_token))
        return {"ok": True, "page_id": page_id, "page_name": "Bound Page"}


class FakeEvidenceStore:
    def __init__(self, *, health_state="healthy", wrong_workspace=None, fail=False):
        self.health_state = health_state
        self.wrong_workspace = wrong_workspace
        self.fail = fail
        self.calls = []

    def record_health_observation(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("database unavailable")
        return DurableHealthObservation(
            evidence_id="evidence-001",
            observation_id="observation-001",
            workspace_id=self.wrong_workspace or kwargs["workspace_id"],
            connection_id=kwargs["connection_id"],
            connector_key=kwargs["connector_key"],
            observed_at=datetime.now(timezone.utc),
            health_state=self.health_state,
            evidence_hash="c" * 64,
            brand_id=kwargs.get("metadata", {}).get("brand_id"),
            social_account_id=kwargs.get("metadata", {}).get("social_account_id"),
        )


class FakeEventBus:
    def __init__(self, registry, *, publish_result="event-001", enabled=True):
        self._producer_registry = registry
        self._provenance_storage_enabled = enabled
        self._tenant_health_evidence_ready = True
        self._provenance_signer = lambda *_args, **_kwargs: {"signature": "test-only"}
        self.supabase = object()
        self.publish_result = publish_result
        self.calls = []

    async def publish(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.publish_result


def make_binding():
    brand = Brand(
        id="brand-a", workspace_id="workspace-a",
        name="Brand A", slug="brand-a",
    )
    account = SocialAccount(
        id="account-a", workspace_id="workspace-a", brand_id="brand-a",
        platform=Platform.FACEBOOK, platform_account_id="page-a",
        account_name="Page A", access_token="private-page-token",
    )
    return TenantSocialAccountBinding(
        workspace_id="workspace-a", brand_id="brand-a",
        account_id="account-a", platform="facebook",
        platform_account_id="page-a", brand=brand, account=account,
    )


def make_contract():
    return ConnectorContract(
        "facebook", "facebook", "1.0.0",
        capabilities=("lookup",), inbound_events=(),
        outbound_actions=(), authentication_method="oauth2",
        risk_by_action={}, tenant_scoped=True, certified=True,
    )


def make_connection_and_registry():
    connection = IntegrationConnection(
        "workspace-a", "facebook", ConnectionState.AUTHENTICATED,
        granted_capabilities=("lookup",), autonomy_mode=AutonomyMode.OBSERVE,
        connection_id="connection-a",
        brand_id="brand-a", social_account_id="account-a",
    )
    registry = ConnectorRegistryRecord(
        "workspace-a", "connection-a", "facebook", "1.0.0",
        RegistryState.ACTIVE,
        brand_id="brand-a", social_account_id="account-a",
    )
    return connection, registry


def make_producer(*, event_bus=None, producer_id=PRODUCER_ID, foreign_capability=False):
    binding = make_binding()
    resolver = FakeResolver(binding)
    service = FakeFacebookService()
    factory = TenantFacebookConnectorFactory(
        facebook_service=service,
        ledger=ExecutionLedger(allow_in_memory=True),
    )
    producer_registry = ProducerRegistry({PRODUCER_ID: PRODUCER_CLASS})
    if foreign_capability:
        capability = ProducerRegistry({PRODUCER_ID: PRODUCER_CLASS}).issue(PRODUCER_ID)
    elif producer_id == PRODUCER_ID:
        capability = producer_registry.issue(producer_id)
    else:
        capability = ProducerRegistry({producer_id: "other"}).issue(producer_id)
    bus = event_bus or FakeEventBus(producer_registry)
    producer = TenantFacebookHealthProducer(
        resolver=resolver,
        connector_factory=factory,
        event_bus=bus,
        producer_registry=producer_registry,
        producer_capability=capability,
    )
    identity = SimpleNamespace(
        valid=True, workspace_id="workspace-a",
        has_permission=lambda permission: permission == "write",
    )
    connection, registry = make_connection_and_registry()
    return producer, resolver, service, bus, identity, connection, registry


@pytest.mark.asyncio
async def test_producer_persists_evidence_before_publishing_signed_tenant_event():
    producer, resolver, service, bus, identity, connection, registry = make_producer()
    store = FakeEvidenceStore()

    result = await producer.observe(
        identity,
        brand_id="brand-a",
        connection=connection,
        contract=make_contract(),
        registry=registry,
        evidence_store=store,
    )

    assert result.event_id == "event-001"
    assert result.receipt.evidence_id == "evidence-001"
    assert resolver.calls == [(identity, "brand-a", "facebook")]
    assert service.calls == [("page-a", "private-page-token")]
    assert len(store.calls) == 1
    assert len(bus.calls) == 1
    args, kwargs = bus.calls[0]
    assert args[0] == EVENT_NAME
    assert kwargs["workspace_id"] == "workspace-a"
    assert kwargs["brand_id"] == "brand-a"
    assert kwargs["correlation_id"] == "evidence-001"
    assert kwargs["producer_capability"] is not None
    payload = args[1]
    assert payload["evidence_id"] == "evidence-001"
    assert payload["evidence_hash"] == "c" * 64
    assert payload["social_account_id"] == "account-a"
    assert "private-page-token" not in repr(payload)


@pytest.mark.asyncio
async def test_persistence_failure_prevents_event_publication():
    producer, _, _, bus, identity, connection, registry = make_producer()
    store = FakeEvidenceStore(fail=True)

    with pytest.raises(TenantHealthPublisherError, match="health_observation_persistence_failed"):
        await producer.observe(
            identity, brand_id="brand-a", connection=connection,
            contract=make_contract(), registry=registry, evidence_store=store,
        )

    assert bus.calls == []


@pytest.mark.asyncio
async def test_wrong_workspace_receipt_fails_closed_and_preserves_receipt():
    producer, _, _, bus, identity, connection, registry = make_producer()
    store = FakeEvidenceStore(wrong_workspace="workspace-b")

    with pytest.raises(TenantHealthPublisherError, match="health_receipt_tenant_binding_mismatch") as caught:
        await producer.observe(
            identity, brand_id="brand-a", connection=connection,
            contract=make_contract(), registry=registry, evidence_store=store,
        )

    assert caught.value.receipt is not None
    assert caught.value.receipt.workspace_id == "workspace-b"
    assert bus.calls == []


@pytest.mark.asyncio
async def test_event_publish_failure_preserves_durable_receipt_for_reconciliation():
    bus = None
    producer, _, _, bus, identity, connection, registry = make_producer()
    bus.publish_result = None
    store = FakeEvidenceStore()

    with pytest.raises(TenantHealthPublisherError, match="signed_health_event_publish_unconfirmed") as caught:
        await producer.observe(
            identity, brand_id="brand-a", connection=connection,
            contract=make_contract(), registry=registry, evidence_store=store,
        )

    assert caught.value.receipt.evidence_id == "evidence-001"
    assert len(store.calls) == 1


def test_producer_requires_durable_signed_event_bus():
    binding = make_binding()
    registry = ProducerRegistry({PRODUCER_ID: PRODUCER_CLASS})
    capability = registry.issue(PRODUCER_ID)
    bus = FakeEventBus(registry, enabled=False)

    with pytest.raises(TenantHealthPublisherError, match="durable_provenance_storage_not_enabled"):
        TenantFacebookHealthProducer(
            resolver=FakeResolver(binding),
            connector_factory=TenantFacebookConnectorFactory(
                facebook_service=FakeFacebookService(),
                ledger=ExecutionLedger(allow_in_memory=True),
            ),
            event_bus=bus,
            producer_registry=registry,
            producer_capability=capability,
        )


def test_producer_rejects_capability_for_another_component():
    binding = make_binding()
    registry = ProducerRegistry({
        PRODUCER_ID: PRODUCER_CLASS,
        "kdo-other-producer": "other",
    })
    capability = registry.issue("kdo-other-producer")
    bus = FakeEventBus(registry)

    with pytest.raises(TenantHealthPublisherError, match="facebook_health_producer_capability_mismatch"):
        TenantFacebookHealthProducer(
            resolver=FakeResolver(binding),
            connector_factory=TenantFacebookConnectorFactory(
                facebook_service=FakeFacebookService(),
                ledger=ExecutionLedger(allow_in_memory=True),
            ),
            event_bus=bus,
            producer_registry=registry,
            producer_capability=capability,
        )


@pytest.mark.asyncio
async def test_foreign_registry_capability_is_rejected_before_provider_probe():
    producer, _, service, bus, identity, connection, registry = make_producer(
        foreign_capability=True
    )
    store = FakeEvidenceStore()

    with pytest.raises(TenantHealthPublisherError, match="producer_capability_scope_invalid"):
        await producer.observe(
            identity, brand_id="brand-a", connection=connection,
            contract=make_contract(), registry=registry, evidence_store=store,
        )

    assert service.calls == []
    assert store.calls == []
    assert bus.calls == []



def test_tenant_facebook_health_producer_rejects_unattested_health_evidence_schema():
    binding = make_binding()
    producer_registry = ProducerRegistry({PRODUCER_ID: PRODUCER_CLASS})
    capability = producer_registry.issue(PRODUCER_ID)
    bus = FakeEventBus(producer_registry)
    bus._tenant_health_evidence_ready = False

    with pytest.raises(TenantHealthPublisherError, match="health_evidence_schema_not_enabled"):
        TenantFacebookHealthProducer(
            resolver=FakeResolver(binding),
            connector_factory=TenantFacebookConnectorFactory(
                facebook_service=FakeFacebookService(),
                ledger=ExecutionLedger(allow_in_memory=True),
            ),
            event_bus=bus,
            producer_registry=producer_registry,
            producer_capability=capability,
        )



@pytest.mark.asyncio
async def test_missing_authenticated_actor_context_fails_before_provider_probe():
    producer, _, service, bus, identity, connection, registry = make_producer()
    store = FakeEvidenceStore()
    token = auth_identity_var.set(None)
    try:
        with pytest.raises(
            TenantHealthPublisherError,
            match="authenticated_actor_provenance_required",
        ):
            await producer.observe(
                identity,
                brand_id="brand-a",
                connection=connection,
                contract=make_contract(),
                registry=registry,
                evidence_store=store,
            )
    finally:
        auth_identity_var.reset(token)

    assert service.calls == []
    assert store.calls == []
    assert bus.calls == []


@pytest.mark.asyncio
async def test_actor_workspace_mismatch_fails_before_provider_probe():
    producer, _, service, bus, identity, connection, registry = make_producer()
    store = FakeEvidenceStore()
    token = auth_identity_var.set(SimpleNamespace(
        valid=True,
        method="api_key",
        user_id="test-user",
        is_species=False,
        workspace_id="workspace-b",
    ))
    try:
        with pytest.raises(
            TenantHealthPublisherError,
            match="authenticated_actor_provenance_required",
        ):
            await producer.observe(
                identity,
                brand_id="brand-a",
                connection=connection,
                contract=make_contract(),
                registry=registry,
                evidence_store=store,
            )
    finally:
        auth_identity_var.reset(token)

    assert service.calls == []
    assert store.calls == []
    assert bus.calls == []
