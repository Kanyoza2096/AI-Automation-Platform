from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from services.v69_tenant_facebook_health_context import (
    TenantFacebookHealthContextError,
    TenantFacebookHealthContextResolver,
)


NOW = datetime(2026, 11, 9, 9, 0, tzinfo=timezone.utc)


class FakeAccountResolver:
    def __init__(self, *, workspace_id="workspace-a", brand_id="brand-a", account_id="page-a"):
        self.workspace_id = workspace_id
        self.brand_id = brand_id
        self.account_id = account_id
        self.calls = []

    def resolve_for_identity(self, identity, *, brand_id, platform):
        self.calls.append((identity, brand_id, platform))
        if not getattr(identity, "valid", False):
            raise ValueError("identity invalid")
        if identity.workspace_id != self.workspace_id or brand_id != self.brand_id or platform != "facebook":
            raise ValueError("tenant account mismatch")
        return SimpleNamespace(
            workspace_id=self.workspace_id,
            brand_id=self.brand_id,
            account_id=self.account_id,
            platform="facebook",
            platform_account_id="page-external-a",
            brand=SimpleNamespace(id=self.brand_id, workspace_id=self.workspace_id),
            account=SimpleNamespace(
                id=self.account_id,
                workspace_id=self.workspace_id,
                brand_id=self.brand_id,
                platform="facebook",
                platform_account_id="page-external-a",
            ),
        )


class Query:
    def __init__(self, client, table):
        self.client = client
        self.table = table
        self.filters = {}
        self.limit_value = None

    def select(self, _columns):
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def execute(self):
        self.client.reads += 1
        rows = self.client.rows.get(self.table, [])
        matched = [
            row for row in rows
            if all(row.get(key) == value for key, value in self.filters.items())
        ]
        return SimpleNamespace(data=matched[: self.limit_value])


class FakeClient:
    def __init__(self, connection=None, registry=None):
        self.rows = {
            "integration_connections": [] if connection is None else [connection],
            "connector_certification_registry": [] if registry is None else [registry],
        }
        self.reads = 0

    def table(self, name):
        return Query(self, name)


def identity():
    return SimpleNamespace(valid=True, workspace_id="workspace-a")


def connection_row(**overrides):
    row = {
        "id": "connection-a",
        "workspace_id": "workspace-a",
        "connector_key": "facebook",
        "state": "observe_only",
        "granted_capabilities": ["lookup"],
        "autonomy_mode": "observe",
        "brand_id": "brand-a",
        "social_account_id": "page-a",
    }
    row.update(overrides)
    return row


def registry_row(**overrides):
    row = {
        "workspace_id": "workspace-a",
        "connection_id": "connection-a",
        "connector_key": "facebook",
        "connector_version": "1.0.0",
        "registry_state": "active",
        "certification_version": "v69.6",
        "certification_status": "pass",
        "certification_fingerprint": "a" * 64,
        "live_provider_verified": True,
        "certified_at": "2026-11-08T09:00:00+00:00",
        "expires_at": "2026-12-08T09:00:00+00:00",
        "evidence_ref": "cert-evidence-a",
        "generation": 1,
        "reason": "certified",
        "updated_at": "2026-11-08T09:00:00+00:00",
    }
    row.update(overrides)
    return row


def resolver(client, account_resolver=None):
    return TenantFacebookHealthContextResolver(
        client,
        social_account_resolver=account_resolver or FakeAccountResolver(),
    )


def test_resolves_connection_registry_and_page_from_same_authenticated_workspace():
    client = FakeClient(connection_row(), registry_row())
    account_resolver = FakeAccountResolver()
    context = resolver(client, account_resolver).resolve_for_identity(
        identity(),
        brand_id="brand-a",
        connection_id="connection-a",
        now=NOW,
    )

    assert context.workspace_id == "workspace-a"
    assert context.binding.account_id == "page-a"
    assert context.connection.connection_id == "connection-a"
    assert context.connection.brand_id == "brand-a"
    assert context.connection.social_account_id == "page-a"
    assert context.contract.connector_key == "facebook"
    assert context.contract.version == "1.0.0"
    assert context.registry.brand_id == "brand-a"
    assert context.registry.social_account_id == "page-a"
    assert context.registry.activation_eligible is True
    assert account_resolver.calls == [(identity(), "brand-a", "facebook")]
    assert client.reads == 2


@pytest.mark.parametrize("connection", [
    connection_row(brand_id="brand-b"),
    connection_row(social_account_id="page-b"),
    connection_row(state="revoked"),
    connection_row(state="suspended"),
    connection_row(granted_capabilities=[]),
    connection_row(granted_capabilities="lookup"),
])
def test_rejects_unbound_or_non_observable_connection_rows(connection):
    client = FakeClient(connection, registry_row())
    with pytest.raises(TenantFacebookHealthContextError):
        resolver(client).resolve_for_identity(
            identity(), brand_id="brand-a", connection_id="connection-a", now=NOW
        )


@pytest.mark.parametrize("registry", [
    registry_row(brand_id="brand-b"),
    registry_row(social_account_id="page-b"),
    registry_row(registry_state="revoked"),
    registry_row(certification_status="fail"),
    registry_row(certification_status="not_proven"),
    registry_row(live_provider_verified=False),
    registry_row(expires_at="2026-11-09T08:00:00+00:00"),
    registry_row(certification_fingerprint="bad"),
    registry_row(certification_version=""),
])
def test_rejects_mismatched_or_untrusted_certification_registry_rows(registry):
    client = FakeClient(connection_row(), registry)
    with pytest.raises(TenantFacebookHealthContextError):
        resolver(client).resolve_for_identity(
            identity(), brand_id="brand-a", connection_id="connection-a", now=NOW
        )


def test_rejects_missing_connection_or_registry_rows():
    with pytest.raises(TenantFacebookHealthContextError, match="integration_connections_not_found"):
        resolver(FakeClient(registry=registry_row())).resolve_for_identity(
            identity(), brand_id="brand-a", connection_id="connection-a", now=NOW
        )

    with pytest.raises(TenantFacebookHealthContextError, match="connector_certification_registry_not_found"):
        resolver(FakeClient(connection=connection_row())).resolve_for_identity(
            identity(), brand_id="brand-a", connection_id="connection-a", now=NOW
        )


def test_rejects_wrong_workspace_and_invalid_identity_before_database_reads():
    client = FakeClient(connection_row(), registry_row())
    with pytest.raises(TenantFacebookHealthContextError, match="authenticated_identity_required"):
        resolver(client).resolve_for_identity(
            None, brand_id="brand-a", connection_id="connection-a", now=NOW
        )
    assert client.reads == 0

    wrong_workspace = SimpleNamespace(valid=True, workspace_id="workspace-b")
    with pytest.raises(TenantFacebookHealthContextError, match="tenant_facebook_account_resolution_failed"):
        resolver(client).resolve_for_identity(
            wrong_workspace, brand_id="brand-a", connection_id="connection-a", now=NOW
        )
    assert client.reads == 0


def test_connection_and_registry_queries_are_scoped_to_workspace_and_connection():
    client = FakeClient(connection_row(), registry_row())
    resolver(client).resolve_for_identity(
        identity(), brand_id="brand-a", connection_id="connection-a", now=NOW
    )

    # Only the two expected, read-only row lookups are performed.
    assert client.reads == 2
