from types import SimpleNamespace

from core.request_context import bind_from_auth_identity
from core.species_auth import AuthIdentity


def test_supabase_identity_preserves_membership_role():
    identity = AuthIdentity(
        valid=True,
        method="supabase_jwt",
        workspace_id="ws-1",
        user_id="user-1",
        user_email="user@example.com",
        role="viewer",
        permissions=["read"],
    )
    ctx = bind_from_auth_identity(identity)
    assert ctx.workspace_id == "ws-1"
    assert ctx.role == "viewer"
    assert ctx.has_permission("read")
    assert not ctx.has_permission("write")


def test_operator_identity_gets_write_permission():
    identity = AuthIdentity(
        valid=True,
        method="supabase_jwt",
        workspace_id="ws-1",
        user_id="user-2",
        role="operator",
        permissions=["read", "write"],
    )
    ctx = bind_from_auth_identity(identity)
    assert ctx.role == "operator"
    assert ctx.has_permission("write")
