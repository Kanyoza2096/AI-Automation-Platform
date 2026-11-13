import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from api.website_integrations import issue_connector_credential, require_website_connector
from core.request_context import RequestContext
from core.api_tokens import APITokenManager
from middleware.request_context_middleware import _is_website_connector_ingress


def test_human_admin_issue_route_binds_credential_to_existing_workspace_integration(monkeypatch):
    manager = APITokenManager(allow_in_memory=True)
    service = Mock()
    service.get.return_value = SimpleNamespace(status="configured")
    monkeypatch.setattr(
        "api.website_integrations._application_service",
        lambda request: service,
    )
    monkeypatch.setattr("core.api_tokens.api_token_manager", manager)
    request = SimpleNamespace(state=SimpleNamespace())
    ctx = RequestContext(
        principal_id="human-admin",
        workspace_id="ws-1",
        role="admin",
        permissions=("admin",),
        principal_type="human",
        auth_method="supabase_jwt",
    )

    result = asyncio.run(issue_connector_credential("site-a", request, ctx))
    credential = result["credential"]
    record = manager.validate_token(credential["token"])
    assert record["workspace_id"] == "ws-1"
    assert record["resource_constraints"] == {"integration_id": "site-a"}
    assert credential["credential_purpose"] == "website_connector"
    service.get.assert_called_once()


def test_human_admin_issue_route_rejects_inactive_integration(monkeypatch):
    manager = Mock()
    service = Mock()
    service.get.return_value = SimpleNamespace(status="revoked")
    monkeypatch.setattr(
        "api.website_integrations._application_service",
        lambda request: service,
    )
    monkeypatch.setattr("core.api_tokens.api_token_manager", manager)
    request = SimpleNamespace(state=SimpleNamespace())
    ctx = RequestContext(
        principal_id="human-admin",
        workspace_id="ws-1",
        role="admin",
        permissions=("admin",),
        principal_type="human",
        auth_method="supabase_jwt",
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(issue_connector_credential("site-a", request, ctx))
    assert exc.value.status_code == 409
    manager.create_website_connector_token.assert_not_called()


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


def test_dedicated_connector_token_revocation_invalidates_authentication():
    manager = APITokenManager(allow_in_memory=True)
    result = manager.create_website_connector_token("ws-1", "site-a")
    assert manager.revoke_token(result["id"], "ws-1") is True
    assert manager.validate_token(result["token"]) is None


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
