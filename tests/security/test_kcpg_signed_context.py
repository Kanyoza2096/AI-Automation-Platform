from __future__ import annotations

import hashlib
import hmac
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from core.request_context import RequestContext
from core.security.deps import _bind_verified_gateway_human_context


class FakeRequest:
    def __init__(self, headers):
        self.headers = headers
        self.state = SimpleNamespace()


def signed_headers(secret, workspace="ws-test", actor="owner-1", role="owner", age_ms=0, platform_role=""):
    timestamp = str(int(time.time() * 1000) - age_ms)
    request_id = "req-test-1"
    canonical = "|".join((timestamp, request_id, workspace, actor, role, platform_role))
    signature = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    return {
        "x-kcpg-context-timestamp": timestamp,
        "x-kcpg-context-request-id": request_id,
        "x-kcpg-context-workspace-id": workspace,
        "x-kcpg-context-actor-id": actor,
        "x-kcpg-context-actor-role": role,
        "x-kcpg-context-platform-role": platform_role,
        "x-kcpg-context-signature": signature,
        "X-Workspace-ID": workspace,
    }


def machine_context():
    return RequestContext(
        principal_id="gateway-service",
        workspace_id="ws-test",
        role="species",
        species_privileges=True,
        principal_type="machine",
        auth_method="species_api_key",
    )


def test_valid_signed_context_binds_human_owner(monkeypatch):
    secret = "test-kcpg-context-secret-that-is-long-enough"
    monkeypatch.setenv("KCPG_CONTEXT_SIGNING_SECRET", secret)
    request = FakeRequest(signed_headers(secret))
    result = _bind_verified_gateway_human_context(request, machine_context(), "req-test-1")
    assert result is not None
    assert result.principal_id == "owner-1"
    assert result.workspace_id == "ws-test"
    assert result.role == "owner"
    assert result.principal_type == "human"
    assert result.auth_method == "supabase_jwt"
    assert result.species_privileges is False


def test_invalid_signature_is_rejected(monkeypatch):
    monkeypatch.setenv("KCPG_CONTEXT_SIGNING_SECRET", "test-kcpg-context-secret-that-is-long-enough")
    headers = signed_headers("test-kcpg-context-secret-that-is-long-enough")
    headers["x-kcpg-context-signature"] = "0" * 64
    with pytest.raises(HTTPException) as exc:
        _bind_verified_gateway_human_context(FakeRequest(headers), machine_context(), "req-test-1")
    assert exc.value.status_code == 403


def test_expired_context_is_rejected(monkeypatch):
    secret = "test-kcpg-context-secret-that-is-long-enough"
    monkeypatch.setenv("KCPG_CONTEXT_SIGNING_SECRET", secret)
    headers = signed_headers(secret, age_ms=300001)
    with pytest.raises(HTTPException) as exc:
        _bind_verified_gateway_human_context(FakeRequest(headers), machine_context(), "req-test-1")
    assert exc.value.status_code == 403


def test_unsigned_machine_request_does_not_become_human():
    result = _bind_verified_gateway_human_context(FakeRequest({"X-Workspace-ID": "ws-test"}), machine_context(), "")
    assert result is None


def test_non_species_identity_cannot_be_overridden(monkeypatch):
    secret = "test-kcpg-context-secret-that-is-long-enough"
    monkeypatch.setenv("KCPG_CONTEXT_SIGNING_SECRET", secret)
    human = RequestContext(principal_id="human-1", workspace_id="ws-test", role="owner", principal_type="human", auth_method="supabase_jwt")
    result = _bind_verified_gateway_human_context(FakeRequest(signed_headers(secret)), human, "")
    assert result is None


def test_valid_signed_platform_super_admin_context_preserves_platform_grant(monkeypatch):
    secret = "test-kcpg-context-secret-that-is-long-enough"
    monkeypatch.setenv("KCPG_CONTEXT_SIGNING_SECRET", secret)
    request = FakeRequest(signed_headers(secret, platform_role="super_admin"))
    result = _bind_verified_gateway_human_context(request, machine_context(), "req-test-1")
    assert result is not None
    assert result.platform_role == "super_admin"
    assert result.has_permission("platform:control_plane:access") is True
    assert result.workspace_id == "ws-test"


def test_unrecognized_signed_platform_role_is_rejected(monkeypatch):
    secret = "test-kcpg-context-secret-that-is-long-enough"
    monkeypatch.setenv("KCPG_CONTEXT_SIGNING_SECRET", secret)
    headers = signed_headers(secret, platform_role="platform_admin")
    with pytest.raises(HTTPException) as exc:
        _bind_verified_gateway_human_context(FakeRequest(headers), machine_context(), "req-test-1")
    assert exc.value.status_code == 403
