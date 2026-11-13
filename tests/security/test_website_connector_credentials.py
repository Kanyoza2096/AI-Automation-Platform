import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from api.website_integrations import require_website_connector
from core.api_tokens import APITokenManager
from middleware.request_context_middleware import _is_website_connector_ingress


def test_generic_token_issuer_cannot_mint_website_connector_or_service_tokens():
    manager = APITokenManager(allow_in_memory=True)
    with pytest.raises(ValueError, match="dedicated issuance flow"):
        manager.create_token(
            "ws-1",
            credential_purpose="website_connector",
            resource_constraints={"integration_id": "site-a"},
        )
    with pytest.raises(ValueError, match="not enabled"):
        manager.create_token("ws-1", credential_purpose="service_integration")


def test_dedicated_issuer_creates_single_integration_write_credential():
    manager = APITokenManager(allow_in_memory=True)
    result = manager.create_website_connector_token(
        "ws-1", "site-a", expires_at="2099-01-01T00:00:00+00:00"
    )

    record = manager.validate_token(result["token"])
    assert result["credential_purpose"] == "website_connector"
    assert record["workspace_id"] == "ws-1"
    assert record["permissions"] == ["write"]
    assert record["credential_purpose"] == "website_connector"
    assert record["resource_constraints"] == {"integration_id": "site-a"}


def test_website_connector_auth_accepts_only_matching_integration(monkeypatch):
    record = {
        "id": "tok-1",
        "workspace_id": "ws-1",
        "permissions": ["write"],
        "credential_purpose": "website_connector",
        "resource_constraints": {"integration_id": "site-a"},
    }
    manager = Mock()
    manager.validate_token.return_value = record
    monkeypatch.setattr("core.api_tokens.api_token_manager", manager)
    request = SimpleNamespace(state=SimpleNamespace())

    ctx = asyncio.run(
        require_website_connector(request, "site-a", "Bearer KDO_API_connector")
    )
    assert ctx.workspace_id == "ws-1"
    assert ctx.principal_type == "machine"
    assert ctx.auth_method == "website_connector_token"
    assert ctx.credential_purpose == "website_connector"
    assert request.state.request_context is ctx

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            require_website_connector(request, "site-b", "Bearer KDO_API_connector")
        )
    assert exc.value.status_code == 403


@pytest.mark.parametrize(
    "record",
    [
        {
            "id": "tok-workspace",
            "workspace_id": "ws-1",
            "permissions": ["write"],
            "credential_purpose": "workspace_api",
            "resource_constraints": {},
        },
        {
            "id": "tok-service",
            "workspace_id": "ws-1",
            "permissions": ["write"],
            "credential_purpose": "service_integration",
            "resource_constraints": {},
        },
        {
            "id": "tok-unscoped",
            "workspace_id": "ws-1",
            "permissions": ["write"],
            "credential_purpose": "website_connector",
            "resource_constraints": {},
        },
    ],
)
def test_website_connector_auth_rejects_other_or_unscoped_credentials(monkeypatch, record):
    manager = Mock()
    manager.validate_token.return_value = record
    monkeypatch.setattr("core.api_tokens.api_token_manager", manager)
    request = SimpleNamespace(state=SimpleNamespace())
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            require_website_connector(request, "site-a", "Bearer KDO_API_wrong")
        )
    assert exc.value.status_code == 403


def test_website_connector_auth_rejects_missing_or_revoked_token(monkeypatch):
    manager = Mock()
    manager.validate_token.return_value = None
    monkeypatch.setattr("core.api_tokens.api_token_manager", manager)
    request = SimpleNamespace(state=SimpleNamespace())
    with pytest.raises(HTTPException) as exc:
        asyncio.run(require_website_connector(request, "site-a", "Bearer KDO_API_revoked"))
    assert exc.value.status_code == 401


def test_only_website_event_ingress_bypasses_generic_workspace_auth():
    assert _is_website_connector_ingress(
        "/api/v1/website-integrations/site-a/events"
    )
    assert not _is_website_connector_ingress(
        "/api/v1/website-integrations/site-a"
    )
    assert not _is_website_connector_ingress(
        "/api/v1/website-integrations/site-a/credentials"
    )
