from __future__ import annotations

import pytest

from core.api_tokens import APITokenManager
from core.request_context import clear_request_context, bind_from_auth_identity


class Identity:
    valid = True
    is_species = False
    workspace_id = None
    method = "workspace_token"
    user_id = "u-1"
    user_email = "user@example.com"
    permissions = ("read",)


def test_non_species_identity_without_workspace_never_gets_default_tenant():
    clear_request_context()
    with pytest.raises(PermissionError):
        bind_from_auth_identity(Identity())
    clear_request_context()


def test_malformed_token_expiry_fails_closed():
    manager = APITokenManager(allow_in_memory=True)
    created = manager.create_token("ws-a", expires_at="not-a-timestamp")
    assert created["token"].startswith("KDO_API_")
    assert manager.validate_token(created["token"]) is None


def test_expired_token_is_rejected():
    manager = APITokenManager(allow_in_memory=True)
    created = manager.create_token("ws-a", expires_at="2000-01-01T00:00:00+00:00")
    assert created["token"].startswith("KDO_API_")
    assert manager.validate_token(created["token"]) is None

def test_non_expiring_token_remains_valid_until_revoked():
    manager = APITokenManager(allow_in_memory=True)
    created = manager.create_token(
        "ws-a",
        name="non-expiring",
        permissions=["read", "write"],
        expires_at=None,
    )

    validated = manager.validate_token(created["token"])
    assert validated is not None
    assert validated["expires_at"] is None
    assert validated["permissions"] == ["read", "write"]

    assert manager.revoke_token(created["id"], "ws-a") is True
    assert manager.validate_token(created["token"]) is None


def test_api_key_request_accepts_explicit_null_expiry():
    from api.v1_models import APIKeyGenerateRequest

    request = APIKeyGenerateRequest(label="non-expiring", expires_in_days=None)
    assert request.expires_in_days is None


def test_api_key_request_keeps_finite_expiry_default():
    from api.v1_models import APIKeyGenerateRequest

    request = APIKeyGenerateRequest(label="default-expiry")
    assert request.expires_in_days == 30
