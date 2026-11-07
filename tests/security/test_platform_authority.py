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
    clear = __import__("core.request_context", fromlist=["clear_request_context"]).clear_request_context
    clear()


def test_platform_super_admin_can_cross_workspace_but_workspace_owner_cannot():
    super_admin = RequestContext(
        principal_id="human-owner",
        workspace_id="ws-a",
        role="viewer",
        platform_role="super_admin",
        platform_permissions=("platform:*",),
        principal_type="human",
    )
    super_admin.assert_workspace("ws-b")
    assert super_admin.effective_workspace("ws-b") == "ws-b"

    workspace_owner = RequestContext(
        principal_id="workspace-owner",
        workspace_id="ws-a",
        role="owner",
        permissions=("owner",),
        principal_type="human",
    )
    try:
        workspace_owner.assert_workspace("ws-b")
    except PermissionError:
        pass
    else:
        raise AssertionError("Workspace owner must not gain platform-wide access")
