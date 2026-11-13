import asyncio

from fastapi import HTTPException

from core.request_context import RequestContext, clear_request_context, set_request_context
from core.security.deps import require_platform_or_species_privileges


def _request(ctx):
    return type("Req", (), {"state": type("State", (), {"request_context": ctx})()})()


def _assert_allowed(ctx):
    set_request_context(ctx)
    try:
        assert asyncio.run(require_platform_or_species_privileges(_request(ctx))) is ctx
    finally:
        clear_request_context()


def _assert_forbidden(ctx):
    set_request_context(ctx)
    try:
        try:
            asyncio.run(require_platform_or_species_privileges(_request(ctx)))
        except HTTPException as exc:
            assert exc.status_code == 403
        else:
            raise AssertionError("principal gained platform control-plane access")
    finally:
        clear_request_context()


def test_verified_human_platform_super_admin_is_allowed():
    _assert_allowed(RequestContext(
        principal_id="verified-human", workspace_id="ws-owner", role="owner",
        permissions=("read", "write", "admin", "owner"),
        platform_role="super_admin", platform_permissions=("platform:*",),
        principal_type="human", auth_method="supabase_jwt",
    ))


def test_workspace_owner_without_platform_grant_is_rejected():
    _assert_forbidden(RequestContext(
        principal_id="workspace-owner", workspace_id="ws-owner", role="owner",
        permissions=("read", "write", "admin", "owner"),
        principal_type="human", auth_method="supabase_jwt",
    ))


def test_platform_operator_does_not_gain_super_admin_control_plane_access():
    _assert_forbidden(RequestContext(
        principal_id="platform-operator", workspace_id="ws-a", role="viewer",
        platform_role="operator", platform_permissions=("platform:operations:read",),
        principal_type="human", auth_method="supabase_jwt",
    ))


def test_workspace_token_cannot_forge_platform_super_admin_context():
    _assert_forbidden(RequestContext(
        principal_id="machine-token", workspace_id="ws-a",
        platform_role="super_admin", platform_permissions=("platform:*",),
        principal_type="machine", auth_method="workspace_token",
    ))


def test_machine_species_principal_retains_access():
    _assert_allowed(RequestContext(
        principal_id="species-service", workspace_id="platform", role="species",
        species_privileges=True, principal_type="machine", auth_method="python_service",
    ))
