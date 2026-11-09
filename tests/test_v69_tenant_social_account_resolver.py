from types import SimpleNamespace

import pytest

from models.social_account import Platform, SocialAccount
from services.v69_tenant_social_account_resolver import (
    TenantAccountResolutionError,
    TenantSocialAccountResolver,
)


BRAND_A = {
    "id": "brand-a",
    "workspace_id": "workspace-a",
    "name": "Brand A",
    "slug": "brand-a",
}
ACCOUNT_A = {
    "id": "account-a",
    "workspace_id": "workspace-a",
    "brand_id": "brand-a",
    "platform": "facebook",
    "platform_account_id": "page-a",
    "account_name": "Page A",
    "access_token": "",
    "refresh_token": "",
    "app_secret": "",
}


class FakeQuery:
    def __init__(self, client, table):
        self.client = client
        self.table_name = table
        self.filters = []
        self.max_rows = None

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def limit(self, value):
        self.max_rows = value
        return self

    def execute(self):
        if self.table_name in self.client.raise_tables:
            raise RuntimeError("database unavailable")
        rows = list(self.client.rows.get(self.table_name, []))
        if self.table_name not in self.client.ignore_filters:
            for key, value in self.filters:
                rows = [row for row in rows if row.get(key) == value]
        self.client.queries.append((self.table_name, tuple(self.filters), self.max_rows))
        if self.max_rows is not None:
            rows = rows[: self.max_rows]
        return SimpleNamespace(data=rows)


class FakeClient:
    def __init__(self, *, brands=None, accounts=None):
        self.rows = {
            "brands": list(brands if brands is not None else [BRAND_A]),
            "social_accounts": list(accounts if accounts is not None else [ACCOUNT_A]),
        }
        self.raise_tables = set()
        self.ignore_filters = set()
        self.queries = []

    def table(self, name):
        return FakeQuery(self, name)


def resolver(client=None):
    return TenantSocialAccountResolver(client or FakeClient())


def test_resolver_scopes_brand_and_account_queries_to_workspace():
    client = FakeClient()
    binding = resolver(client).resolve(
        workspace_id="workspace-a", brand_id="brand-a", platform=Platform.FACEBOOK
    )

    assert binding.workspace_id == "workspace-a"
    assert binding.brand_id == "brand-a"
    assert binding.account_id == "account-a"
    assert binding.platform_account_id == "page-a"
    brand_query, account_query = client.queries
    assert ("workspace_id", "workspace-a") in brand_query[1]
    assert ("workspace_id", "workspace-a") in account_query[1]
    assert ("brand_id", "brand-a") in account_query[1]
    assert ("platform", "facebook") in account_query[1]
    assert brand_query[2] == account_query[2] == 2


def test_resolver_rejects_brand_from_another_workspace():
    client = FakeClient(brands=[{**BRAND_A, "workspace_id": "workspace-b"}])

    with pytest.raises(TenantAccountResolutionError, match="brand_not_found_in_workspace"):
        resolver(client).resolve(
            workspace_id="workspace-a", brand_id="brand-a", platform="facebook"
        )


def test_resolver_rechecks_workspace_even_if_query_client_returns_wrong_brand():
    client = FakeClient(brands=[{**BRAND_A, "workspace_id": "workspace-b"}])
    client.ignore_filters.add("brands")

    with pytest.raises(TenantAccountResolutionError, match="brand_workspace_mismatch"):
        resolver(client).resolve(
            workspace_id="workspace-a", brand_id="brand-a", platform="facebook"
        )


def test_resolver_rejects_account_from_another_workspace():
    client = FakeClient(accounts=[{**ACCOUNT_A, "workspace_id": "workspace-b"}])

    with pytest.raises(TenantAccountResolutionError, match="social_account_not_found"):
        resolver(client).resolve(
            workspace_id="workspace-a", brand_id="brand-a", platform="facebook"
        )


def test_resolver_rechecks_account_tenant_binding_after_hydration():
    client = FakeClient(accounts=[{**ACCOUNT_A, "workspace_id": "workspace-b"}])
    client.ignore_filters.add("social_accounts")

    with pytest.raises(TenantAccountResolutionError, match="social_account_tenant_binding_mismatch"):
        resolver(client).resolve(
            workspace_id="workspace-a", brand_id="brand-a", platform="facebook"
        )


def test_resolver_rejects_ambiguous_brand_or_account():
    with pytest.raises(TenantAccountResolutionError, match="ambiguous_brand_identity"):
        resolver(FakeClient(brands=[BRAND_A, BRAND_A])).resolve(
            workspace_id="workspace-a", brand_id="brand-a", platform="facebook"
        )

    with pytest.raises(TenantAccountResolutionError, match="ambiguous_social_account"):
        resolver(FakeClient(accounts=[ACCOUNT_A, {**ACCOUNT_A, "id": "account-b"}])).resolve(
            workspace_id="workspace-a", brand_id="brand-a", platform="facebook"
        )


@pytest.mark.parametrize("table,expected", [
    ("brands", "brand_lookup_failed"),
    ("social_accounts", "social_account_lookup_failed"),
])
def test_resolver_fails_closed_on_database_errors(table, expected):
    client = FakeClient()
    client.raise_tables.add(table)

    with pytest.raises(TenantAccountResolutionError, match=expected):
        resolver(client).resolve(
            workspace_id="workspace-a", brand_id="brand-a", platform="facebook"
        )


def test_resolver_hides_decrypted_credentials_from_binding_repr(monkeypatch):
    client = FakeClient()
    account = SocialAccount(
        **{
            **ACCOUNT_A,
            "access_token": "sensitive-page-token",
            "platform": Platform.FACEBOOK,
        }
    )
    instance = resolver(client)
    monkeypatch.setattr(instance._account_hydrator, "_decrypt_entity", lambda _row: account)

    binding = instance.resolve(
        workspace_id="workspace-a", brand_id="brand-a", platform="facebook"
    )

    assert binding.account.access_token == "sensitive-page-token"
    assert "sensitive-page-token" not in repr(binding)
    assert "sensitive-page-token" not in repr(binding.public_identity())


def test_resolver_requires_explicit_database_client():
    with pytest.raises(TenantAccountResolutionError, match="explicit_supabase_client_required"):
        TenantSocialAccountResolver(None)


def test_resolver_requires_all_identity_dimensions():
    with pytest.raises(TenantAccountResolutionError, match="workspace_brand_platform_required"):
        resolver().resolve(workspace_id="", brand_id="brand-a", platform="facebook")


def test_resolver_uses_workspace_from_authenticated_identity():
    identity = SimpleNamespace(valid=True, workspace_id="workspace-a")

    binding = resolver().resolve_for_identity(
        identity, brand_id="brand-a", platform=Platform.FACEBOOK
    )

    assert binding.workspace_id == identity.workspace_id


@pytest.mark.parametrize("identity,expected", [
    (None, "authenticated_identity_required"),
    (SimpleNamespace(valid=False, workspace_id="workspace-a"), "authenticated_identity_required"),
    (SimpleNamespace(valid=True, workspace_id=None), "authenticated_workspace_required"),
])
def test_resolver_rejects_invalid_or_unscoped_auth_identity(identity, expected):
    with pytest.raises(TenantAccountResolutionError, match=expected):
        resolver().resolve_for_identity(
            identity, brand_id="brand-a", platform=Platform.FACEBOOK
        )
