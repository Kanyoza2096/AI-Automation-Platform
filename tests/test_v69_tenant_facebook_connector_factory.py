from dataclasses import replace

import pytest

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
from services.v69_tenant_facebook_connector_factory import (
    TenantFacebookCompositionError,
    TenantFacebookConnectorFactory,
)
from services.v69_tenant_social_account_resolver import TenantSocialAccountBinding


class FakeFacebookService:
    def __init__(self):
        self.account_checks = []
        self.global_checks = 0

    def validate_account_token(self, page_id, page_token):
        self.account_checks.append((page_id, page_token))
        return {"ok": True, "page_id": page_id, "page_name": "Bound Page"}

    def validate_token(self):
        self.global_checks += 1
        raise AssertionError("process-wide token validation must never be used")


class FakeHealthStore:
    def __init__(self):
        self.calls = []

    def record_health_observation(self, **kwargs):
        self.calls.append(kwargs)
        return {"persisted": True, **kwargs}


def make_contract():
    return ConnectorContract(
        "facebook", "facebook", "1.0.0",
        capabilities=("publish", "lookup", "edit", "delete", "comments"),
        inbound_events=("comment", "message"),
        outbound_actions=("publish", "edit", "delete"),
        authentication_method="oauth2",
        risk_by_action={"publish": "medium", "edit": "medium", "delete": "high"},
        requires_reconciliation=True,
        tenant_scoped=True,
        certified=True,
    )


def make_binding():
    brand = Brand(
        id="brand-a", workspace_id="workspace-a",
        name="Brand A", slug="brand-a",
    )
    account = SocialAccount(
        id="account-a", workspace_id="workspace-a", brand_id="brand-a",
        platform=Platform.FACEBOOK, platform_account_id="page-a",
        account_name="Page A", access_token="tenant-page-token",
    )
    return TenantSocialAccountBinding(
        workspace_id="workspace-a",
        brand_id="brand-a",
        account_id="account-a",
        platform="facebook",
        platform_account_id="page-a",
        brand=brand,
        account=account,
    )


def make_connection_and_registry():
    connection = IntegrationConnection(
        "workspace-a", "facebook", ConnectionState.AUTHENTICATED,
        granted_capabilities=("lookup",), autonomy_mode=AutonomyMode.OBSERVE,
        connection_id="connection-a",
    )
    registry = ConnectorRegistryRecord(
        "workspace-a", "connection-a", "facebook", "1.0.0",
        RegistryState.ACTIVE,
    )
    return connection, registry


def make_factory(service=None):
    return TenantFacebookConnectorFactory(
        facebook_service=service or FakeFacebookService(),
        ledger=ExecutionLedger(allow_in_memory=True),
    )


def test_factory_composes_account_bound_connector_and_health_uses_only_page_token():
    service = FakeFacebookService()
    factory = make_factory(service)
    binding = make_binding()
    connection, registry = make_connection_and_registry()

    gateway = factory.build_gateway(
        binding=binding,
        connection=connection,
        contract=make_contract(),
        registry=registry,
    )

    # Composition itself performs no provider call.
    assert service.account_checks == []
    store = FakeHealthStore()
    result = gateway.observe_health(store)

    assert service.account_checks == [("page-a", "tenant-page-token")]
    assert service.global_checks == 0
    assert result["health_state"] == "healthy"
    assert store.calls[0]["workspace_id"] == "workspace-a"
    assert store.calls[0]["connection_id"] == "connection-a"
    assert store.calls[0]["connector_key"] == "facebook"


def test_factory_rejects_account_from_another_workspace():
    binding = make_binding()
    bad_account = binding.account.model_copy(update={"workspace_id": "workspace-b"})
    bad_binding = replace(binding, account=bad_account)
    connection, registry = make_connection_and_registry()

    with pytest.raises(TenantFacebookCompositionError, match="tenant_account_binding_mismatch"):
        make_factory().build_gateway(
            binding=bad_binding, connection=connection,
            contract=make_contract(), registry=registry,
        )


def test_factory_rejects_brand_workspace_mismatch():
    binding = make_binding()
    bad_brand = binding.brand.model_copy(update={"workspace_id": "workspace-b"})
    bad_binding = replace(binding, brand=bad_brand)
    connection, registry = make_connection_and_registry()

    with pytest.raises(TenantFacebookCompositionError, match="tenant_account_binding_mismatch"):
        make_factory().build_gateway(
            binding=bad_binding, connection=connection,
            contract=make_contract(), registry=registry,
        )


def test_factory_rejects_connection_registry_workspace_mismatch():
    binding = make_binding()
    connection, registry = make_connection_and_registry()
    bad_registry = replace(registry, workspace_id="workspace-b")

    with pytest.raises(TenantFacebookCompositionError, match="tenant_connection_registry_mismatch"):
        make_factory().build_gateway(
            binding=binding, connection=connection,
            contract=make_contract(), registry=bad_registry,
        )


def test_factory_rejects_connection_registry_id_mismatch():
    binding = make_binding()
    connection, registry = make_connection_and_registry()
    bad_registry = replace(registry, connection_id="other-connection")

    with pytest.raises(TenantFacebookCompositionError, match="tenant_connection_registry_mismatch"):
        make_factory().build_gateway(
            binding=binding, connection=connection,
            contract=make_contract(), registry=bad_registry,
        )


def test_factory_rejects_non_facebook_contract():
    binding = make_binding()
    connection, registry = make_connection_and_registry()
    bad_contract = replace(make_contract(), connector_key="linkedin")

    with pytest.raises(TenantFacebookCompositionError, match="tenant_connection_registry_mismatch"):
        make_factory().build_gateway(
            binding=binding, connection=connection,
            contract=bad_contract, registry=registry,
        )


def test_factory_requires_service_and_ledger():
    with pytest.raises(TenantFacebookCompositionError, match="facebook_service_required"):
        TenantFacebookConnectorFactory(facebook_service=None, ledger=ExecutionLedger(allow_in_memory=True))
    with pytest.raises(TenantFacebookCompositionError, match="execution_ledger_required"):
        TenantFacebookConnectorFactory(facebook_service=FakeFacebookService(), ledger=None)


def test_factory_rejects_mismatched_provider_contract():
    binding = make_binding()
    connection, registry = make_connection_and_registry()
    bad_contract = replace(make_contract(), provider="linkedin")

    with pytest.raises(TenantFacebookCompositionError, match="tenant_connection_registry_mismatch"):
        make_factory().build_gateway(
            binding=binding, connection=connection,
            contract=bad_contract, registry=registry,
        )
