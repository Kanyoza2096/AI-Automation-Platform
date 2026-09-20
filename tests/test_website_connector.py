from unittest.mock import MagicMock, patch

import pytest

from services.connectors.connector import ConnectorHealth, ConnectorResult, Platform
from services.connectors.website_connector import WebsiteConnector


def _account(**config):
    account = MagicMock()
    account.id = "web-1"
    account.workspace_id = "ws-1"
    account.platform = Platform.WEBSITE
    account.enabled = True
    account.access_token = "secret-token"
    account.platform_config = {
        "base_url": "https://cms.example.com/api",
        "publish_path": "/content",
        "update_path": "/content/{post_id}",
        "lookup_path": "/content/{post_id}",
        "health_path": "/health",
        **config,
    }
    return account


def test_website_platform_is_factory_supported():
    from services.connectors.connector_factory import create_connector, supported_platforms

    account = _account()
    connector = create_connector(account)
    assert isinstance(connector, WebsiteConnector)
    assert "website" in supported_platforms()


def test_website_publish_requires_provider_object_id_for_success():
    connector = WebsiteConnector(_account())
    response = MagicMock(status_code=201, content=b'{"ok": true}')
    response.json.return_value = {"ok": True}
    with patch("services.connectors.website_connector.requests.post", return_value=response):
        result = connector.publish("A news article", title="News")
    assert result.ok is False
    assert result.outcome == "uncertain"
    assert result.post_id is None


def test_website_publish_returns_receipt():
    connector = WebsiteConnector(_account())
    response = MagicMock(status_code=201, content=b'{"id":"article-123","slug":"hello"}')
    response.json.return_value = {"id": "article-123", "slug": "hello"}
    with patch("services.connectors.website_connector.requests.post", return_value=response) as post:
        result = connector.publish("Hello website", title="Hello", content_type="news", slug="hello")
    assert result.ok is True
    assert result.outcome == "succeeded"
    assert result.post_id == "article-123"
    assert post.call_args.kwargs["json"]["content_type"] == "news"
    assert post.call_args.kwargs["json"]["title"] == "Hello"


def test_website_update_preserves_existing_provider_identity():
    connector = WebsiteConnector(_account())
    response = MagicMock(status_code=200, content=b'{"updated":true}')
    response.json.return_value = {"updated": True}
    with patch("services.connectors.website_connector.requests.request", return_value=response) as request:
        result = connector.edit("article-123", "Updated body", title="Updated")
    assert result.ok is True
    assert result.outcome == "succeeded"
    assert result.post_id == "article-123"
    assert request.call_args.args[0] == "PATCH"
    assert request.call_args.args[1].endswith("/content/article-123")


def test_website_transport_timeout_is_uncertain():
    connector = WebsiteConnector(_account())
    with patch("services.connectors.website_connector.requests.post", side_effect=__import__("requests").Timeout("timed out")):
        result = connector.publish("Hello")
    assert result.ok is False
    assert result.outcome == "uncertain"


def test_website_http_requires_explicit_opt_in():
    account = _account(base_url="http://cms.example.com/api")
    with pytest.raises(ValueError, match="HTTPS"):
        WebsiteConnector(account)


def test_website_lookup_is_conservative():
    connector = WebsiteConnector(_account())
    response = MagicMock(status_code=500, content=b"bad")
    with patch("services.connectors.website_connector.requests.get", return_value=response):
        assert connector.lookup_post("article-123") is None


def test_website_health_uses_configured_endpoint():
    connector = WebsiteConnector(_account())
    response = MagicMock(status_code=200, content=b'{"ok":true}')
    with patch("services.connectors.website_connector.requests.get", return_value=response) as get:
        result = connector.health()
    assert isinstance(result, ConnectorHealth)
    assert result.ok is True
    assert get.call_args.args[0] == "https://cms.example.com/api/health"


def test_website_actions_are_registered_as_high_risk_approval_gated():
    from services.action_kernel import build_default_action_kernel

    kernel = build_default_action_kernel(db=None, allow_in_memory=True)
    publish = kernel._contracts["website_publish"]
    update = kernel._contracts["website_update"]
    assert publish.risk_level == "high"
    assert publish.requires_approval is True
    assert update.risk_level == "high"
    assert update.requires_approval is True
