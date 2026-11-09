from types import SimpleNamespace

from services.facebook import FacebookService
from services.connectors.facebook_connector import FacebookConnector


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self.payload = payload

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, *, params, timeout):
        self.calls.append((url, params, timeout))
        return self.response


class FakeAccountHealthService:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def validate_account_token(self, page_id, page_token):
        self.calls.append((page_id, page_token))
        return self.result

    def validate_token(self):
        raise AssertionError("global process credentials must never be used")


def account(page_id="page-tenant-a", token="tenant-page-secret"):
    return SimpleNamespace(
        id="social-account-a",
        platform_account_id=page_id,
        access_token=token,
    )


def test_service_validates_explicit_page_id_and_token_without_global_fallback():
    service = FacebookService.__new__(FacebookService)
    session = FakeSession(FakeResponse(200, {"id": "page-tenant-a", "name": "Tenant Page"}))
    service._session = session

    result = service.validate_account_token("page-tenant-a", "tenant-page-secret")

    assert result == {
        "ok": True,
        "page_id": "page-tenant-a",
        "page_name": "Tenant Page",
    }
    url, params, timeout = session.calls[0]
    assert url.endswith("/page-tenant-a")
    assert params == {
        "fields": "id,name",
        "access_token": "tenant-page-secret",
    }
    assert timeout > 0


def test_service_rejects_provider_page_identity_mismatch():
    service = FacebookService.__new__(FacebookService)
    session = FakeSession(FakeResponse(200, {"id": "another-page", "name": "Wrong Page"}))
    service._session = session

    result = service.validate_account_token("page-tenant-a", "tenant-page-secret")

    assert result == {"ok": False, "error": "provider_page_identity_mismatch"}


def test_service_does_not_make_request_without_explicit_account_credentials():
    service = FacebookService.__new__(FacebookService)
    session = FakeSession(FakeResponse(200, {"id": "page-tenant-a"}))
    service._session = session

    result = service.validate_account_token("", "")

    assert result["ok"] is False
    assert session.calls == []


def test_connector_health_uses_only_its_bound_account_credentials():
    service = FakeAccountHealthService({"ok": True, "page_id": "page-tenant-a"})
    connector = FacebookConnector(service, social_account=account())

    result = connector.health()

    assert result.ok is True
    assert service.calls == [("page-tenant-a", "tenant-page-secret")]


def test_connector_health_rejects_a_different_provider_page():
    service = FakeAccountHealthService({"ok": True, "page_id": "another-page"})
    connector = FacebookConnector(service, social_account=account())

    result = connector.health()

    assert result.ok is False
    assert result.error == "provider_page_identity_mismatch"
    assert service.calls == [("page-tenant-a", "tenant-page-secret")]


def test_connector_health_fails_closed_when_account_credentials_are_missing():
    service = FakeAccountHealthService({"ok": True, "page_id": "page-tenant-a"})
    connector = FacebookConnector(
        service,
        social_account=account(page_id="", token=""),
    )

    result = connector.health()

    assert result.ok is False
    assert result.error == "account_credentials_unavailable"
    assert service.calls == []


def test_connector_health_never_falls_back_to_global_token_validation():
    service = SimpleNamespace(validate_token=lambda: True)
    connector = FacebookConnector(service, social_account=account())

    result = connector.health()

    assert result.ok is False
    assert result.error == "tenant_bound_health_validation_unavailable"


def test_connector_validate_uses_bound_account_health():
    service = FakeAccountHealthService({"ok": True, "page_id": "page-tenant-a"})
    connector = FacebookConnector(service, social_account=account())

    assert connector.validate() is True
    assert service.calls == [("page-tenant-a", "tenant-page-secret")]


def test_service_provider_rejection_does_not_echo_account_token():
    service = FacebookService.__new__(FacebookService)
    session = FakeSession(FakeResponse(401, {"error": {"code": 190}}))
    service._session = session

    result = service.validate_account_token("page-tenant-a", "tenant-page-secret")

    assert result == {"ok": False, "error": {"status_code": 401}}
    assert "tenant-page-secret" not in repr(result)


def test_service_transport_failure_does_not_echo_account_token():
    class FailingSession:
        def get(self, *_args, **_kwargs):
            raise RuntimeError("transport failed")

    service = FacebookService.__new__(FacebookService)
    service._session = FailingSession()

    result = service.validate_account_token("page-tenant-a", "tenant-page-secret")

    assert result == {"ok": False, "error": "provider_check_failed"}
    assert "tenant-page-secret" not in repr(result)
