from types import SimpleNamespace
from unittest.mock import Mock

from services.connectors.website_connector import WebsiteConnector


def _connector():
    account = SimpleNamespace(
        platform_config={
            "base_url": "https://growthbridgevirtual.onrender.com/",
            "publish_path": "/api/kdo/content",
            "lookup_path_template": "/api/kdo/content/{post_id}",
            "context_path": "/api/kdo/context",
        },
        access_token="KDO_API_test-token",
        workspace_id="ws-test",
    )
    return WebsiteConnector(account)


def test_fetch_context_uses_configured_https_path_and_bearer(monkeypatch):
    connector = _connector()
    response = Mock(status_code=200, content=b'{"ok":true,"context":{"schema_version":"1.0"}}')
    response.json.return_value = {
        "ok": True,
        "context": {"schema_version": "1.0", "services": [{"title": "Engineering"}]},
    }
    get = Mock(return_value=response)
    monkeypatch.setattr("services.connectors.website_connector.requests.get", get)

    result = connector.fetch_context()

    assert result["schema_version"] == "1.0"
    assert result["services"][0]["title"] == "Engineering"
    args, kwargs = get.call_args
    assert args[0] == "https://growthbridgevirtual.onrender.com/api/kdo/context"
    assert kwargs["headers"]["Authorization"] == "Bearer KDO_API_test-token"
    assert kwargs["timeout"] == 20


def test_fetch_context_fails_closed_on_http_error(monkeypatch):
    connector = _connector()
    monkeypatch.setattr(
        "services.connectors.website_connector.requests.get",
        Mock(return_value=Mock(status_code=403, content=b"")),
    )
    assert connector.fetch_context() is None


def test_fetch_context_is_disabled_without_context_path_or_token(monkeypatch):
    account = SimpleNamespace(
        platform_config={
            "base_url": "https://growthbridgevirtual.onrender.com/",
            "publish_path": "/api/kdo/content",
            "lookup_path_template": "/api/kdo/content/{post_id}",
            "context_path": "",
        },
        access_token="",
        workspace_id="ws-test",
    )
    connector = WebsiteConnector(account)
    get = Mock()
    monkeypatch.setattr("services.connectors.website_connector.requests.get", get)

    assert connector.fetch_context() is None
    get.assert_not_called()
