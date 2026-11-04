"""Tenant isolation and RequestContext laws."""

import pytest

from core.species_auth import _workspace_token_role
from core.request_context import (
    RequestContext,
    bind_from_auth_identity,
    clear_request_context,
    get_request_context,
    set_request_context,
)
from core.plugin_execution import PluginWorkspaceError, resolve_plugin_workspace
from core.fail_closed import resolve_runtime_mode, assert_persistable, InfrastructureUnavailable
from species.laws.registry import SPECIES_LAWS, assert_law


class FakeIdentity:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_species_laws_registered():
    assert "WORKSPACE_FROM_AUTH" in SPECIES_LAWS
    assert assert_law("PRODUCTION_FAIL_CLOSED")


def test_ordinary_user_cannot_cross_workspace():
    ctx = RequestContext(
        principal_id="user-a",
        workspace_id="ws-a",
        role="operator",
        permissions=("read", "write"),
        species_privileges=False,
    )
    with pytest.raises(PermissionError):
        ctx.assert_workspace("ws-b")
    assert ctx.effective_workspace("ws-b") == "ws-a"


def test_species_operator_may_select_workspace():
    ctx = RequestContext(
        principal_id="species",
        workspace_id="species",
        role="species",
        permissions=("species",),
        species_privileges=True,
    )
    assert ctx.effective_workspace("ws-b") == "ws-b"
    ctx.assert_workspace("ws-b")  # no raise


def test_plugin_workspace_requires_context():
    clear_request_context()
    with pytest.raises(PluginWorkspaceError):
        resolve_plugin_workspace("attacker-ws")


def test_plugin_workspace_ignores_client_for_tenant():
    set_request_context(
        RequestContext(
            principal_id="u1",
            workspace_id="ws-tenant",
            species_privileges=False,
        )
    )
    assert resolve_plugin_workspace("attacker-ws") == "ws-tenant"
    clear_request_context()


def test_production_fail_closed():
    mode = resolve_runtime_mode("production")
    assert mode.allow_memory_fallback is False
    with pytest.raises(InfrastructureUnavailable):
        assert_persistable(mode, "database")


def test_development_allows_fallback():
    mode = resolve_runtime_mode("development")
    assert mode.allow_memory_fallback is True
    assert_persistable(mode)  # no raise


def test_bind_from_auth_identity():
    clear_request_context()
    identity = FakeIdentity(
        is_species=False,
        workspace_id="ws-9",
        permissions=["read"],
        user_id="uid-9",
        method="workspace_token",
        user_email="a@b.c",
    )
    ctx = bind_from_auth_identity(identity, correlation_id="corr-1")
    assert get_request_context() is ctx
    assert ctx.workspace_id == "ws-9"
    assert ctx.correlation_id == "corr-1"
    clear_request_context()


@pytest.mark.parametrize(
    ("permissions", "expected_role"),
    [
        (["read"], "viewer"),
        (["read", "write"], "operator"),
        (["read", "admin"], "admin"),
        (["read", "owner"], "owner"),
    ],
)
def test_workspace_token_permissions_map_to_authority_role(permissions, expected_role):
    clear_request_context()
    identity = FakeIdentity(
        is_species=False,
        workspace_id="ws-token",
        role=_workspace_token_role(permissions),
        permissions=permissions,
        method="workspace_token",
        name="Scoped Token",
    )
    ctx = bind_from_auth_identity(identity)
    assert ctx.role == expected_role
    clear_request_context()
