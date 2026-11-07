"""Platform authority is a separate, server-issued human grant."""

from core.species_auth import resolve_platform_authority
from core.request_context import RequestContext, bind_from_auth_identity


def test_platform_super_admin_requires_exact_server_configured_user_uuid():
    role, permissions = resolve_platform_authority(
        "  8c2f0000-0000-4000-8000-000000000001  ",
        super_admin_ids="8c2f0000-0000-4000-8000-000000000001",
        operator_ids="",
    )
    assert role == "super_admin"
    assert permissions == ["platform:*"]


def test_workspace_role_or_email_cannot_create_platform_grant():
    role, permissions = resolve_platform_authority(
        "8c2f0000-0000-4000-8000-000000000003",
        super_admin_ids="owner@example.com",
        operator_ids="",
    )
    assert role == ""
    assert permissions == []


def test_platform_operator_is_limited_and_separate_from_super_admin():
    role, permissions = resolve_platform_authority(
        "8c2f0000-0000-4000-8000-000000000002",
        super_admin_ids="",
        operator_ids="8c2f0000-0000-4000-8000-000000000002",
    )
    assert role == "operator"
    assert "platform:workspaces:read" in permissions
    assert "platform:*" not in permissions


def test_machine_identity_never_becomes_human_platform_super_admin():
    identity = type("Identity", (), {
        "is_species": True,
        "workspace_id": None,
        "permissions": ["species", "owner"],
        "role": "species",
        "user_id": None,
        "method": "python_service",
        "platform_role": "super_admin",
        "platform_permissions": ["platform:*"],
    })()
    ctx = bind_from_auth_identity(identity)
    assert ctx.principal_type == "machine"
    assert ctx.platform_role == ""
    assert not ctx.has_permission("platform:users:manage")
    from core.request_context import clear_request_context
    clear_request_context()


def test_platform_super_admin_can_cross_workspace_but_workspace_owner_cannot():
    super_admin = RequestContext(
        principal_id="human-owner",
        workspace_id="ws-a",
        role="viewer",
        platform_role="super_admin",
        platform_permissions=("platform:*",),
        principal_type="human",
        auth_method="supabase_jwt",
    )
    super_admin.assert_workspace("ws-b")
    assert super_admin.effective_workspace("ws-b") == "ws-b"
    assert super_admin.has_permission("write")
    assert super_admin.has_permission("platform:users:manage")

    workspace_owner = RequestContext(
        principal_id="workspace-owner",
        workspace_id="ws-a",
        role="owner",
        permissions=("owner",),
        principal_type="human",
    )
    assert not workspace_owner.has_permission("platform:workspaces:read")
    forged_context = RequestContext(
        principal_id="api-key",
        workspace_id="ws-a",
        platform_role="operator",
        platform_permissions=("platform:workspaces:read",),
        principal_type="human",
        auth_method="workspace_token",
    )
    assert not forged_context.has_permission("platform:workspaces:read")
    try:
        workspace_owner.assert_workspace("ws-b")
    except PermissionError:
        pass
    else:
        raise AssertionError("Workspace owner must not gain platform-wide access")

def test_auth_identity_applies_platform_grants_without_mutating_workspace_role():
    from core.species_auth import AuthIdentity

    owner = AuthIdentity(
        valid=True,
        method="supabase_jwt",
        workspace_id="ws-a",
        role="viewer",
        permissions=["read"],
        platform_role="super_admin",
        platform_permissions=["platform:*"],
    )
    assert owner.has_permission("write")
    assert owner.has_permission("platform:users:manage")
    owner.assert_workspace_access("ws-b")

    machine = AuthIdentity(
        valid=True,
        method="python_service",
        workspace_id=None,
        role="species",
        permissions=["species", "owner"],
        is_species=True,
        platform_role="super_admin",
        platform_permissions=["platform:*"],
    )
    assert not machine.has_permission("platform:users:manage")

    forged_api_key = AuthIdentity(
        valid=True,
        method="workspace_token",
        workspace_id="ws-a",
        platform_role="operator",
        platform_permissions=["platform:workspaces:read"],
    )
    assert not forged_api_key.has_permission("platform:workspaces:read")

    operator = AuthIdentity(
        valid=True,
        method="supabase_jwt",
        workspace_id="ws-a",
        role="viewer",
        permissions=["read"],
        platform_role="operator",
        platform_permissions=["platform:workspaces:read"],
    )
    assert operator.has_permission("platform:workspaces:read")
    assert not operator.has_permission("platform:users:manage")
    try:
        operator.assert_workspace_access("ws-b")
    except PermissionError:
        pass
    else:
        raise AssertionError("Platform Operator must remain tenant-scoped for mutations")
