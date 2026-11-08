from types import SimpleNamespace

import pytest

from api.dependencies import require_auth
from api.support import auth_identity_var, auth_method_var, auth_user_email_var, auth_workspace_id_var


class FakeIdentity:
    valid = True
    method = "supabase_jwt"
    workspace_id = "workspace-a"
    user_email = "owner@example.test"
    user_id = "user-123"
    is_species = False


@pytest.mark.asyncio
async def test_require_auth_binds_validated_identity_for_dependency_lifetime(monkeypatch):
    import core.species_auth as species_auth_module

    identity = FakeIdentity()
    monkeypatch.setattr(
        species_auth_module.species_auth,
        "validate",
        lambda **kwargs: identity,
    )
    request = SimpleNamespace(
        headers={},
        app=SimpleNamespace(state=SimpleNamespace(key_store=object())),
        client=SimpleNamespace(host="127.0.0.1"),
    )

    dependency = require_auth(request, container=object(), credentials=None)
    assert await anext(dependency) is True
    assert auth_identity_var.get() is identity
    assert auth_workspace_id_var.get() == "workspace-a"
    assert auth_method_var.get() == "supabase_jwt"
    assert auth_user_email_var.get() == "owner@example.test"

    await dependency.aclose()
    assert auth_identity_var.get() is None
    assert auth_workspace_id_var.get() is None
    assert auth_method_var.get() is None
    assert auth_user_email_var.get() is None


@pytest.mark.asyncio
async def test_require_auth_resets_identity_when_endpoint_raises(monkeypatch):
    import core.species_auth as species_auth_module

    identity = FakeIdentity()
    monkeypatch.setattr(
        species_auth_module.species_auth,
        "validate",
        lambda **kwargs: identity,
    )
    request = SimpleNamespace(
        headers={},
        app=SimpleNamespace(state=SimpleNamespace(key_store=object())),
        client=SimpleNamespace(host="127.0.0.1"),
    )

    dependency = require_auth(request, container=object(), credentials=None)
    assert await anext(dependency) is True
    with pytest.raises(RuntimeError, match="endpoint failure"):
        await dependency.athrow(RuntimeError("endpoint failure"))

    assert auth_identity_var.get() is None
    assert auth_workspace_id_var.get() is None
    assert auth_method_var.get() is None
    assert auth_user_email_var.get() is None
