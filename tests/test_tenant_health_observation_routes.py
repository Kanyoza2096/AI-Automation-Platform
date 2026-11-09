from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.support import auth_identity_var
from api.v1 import require_auth
from routes.tenant_health_observation_routes import router
from services.v69_tenant_facebook_health_context import TenantFacebookHealthContextError
from services.v69_tenant_facebook_health_producer import TenantHealthPublisherError
from services.v69_tenant_facebook_health_service import TenantFacebookHealthServiceError


class FakeHealthService:
    def __init__(self, result=None, error=None):
        self.result = result or SimpleNamespace(
            workspace_id="workspace-a",
            brand_id="brand-a",
            connection_id="connection-a",
            event_id="event-a",
            receipt=SimpleNamespace(
                health_state="healthy",
                evidence_id="evidence-a",
                observation_id="observation-a",
            ),
        )
        self.error = error
        self.calls = []

    async def observe(self, identity, *, brand_id, connection_id):
        self.calls.append((identity, brand_id, connection_id))
        if self.error:
            raise self.error
        return self.result


def identity(*, workspace_id="workspace-a", allowed=True):
    return SimpleNamespace(
        valid=True,
        workspace_id=workspace_id,
        has_permission=lambda permission: allowed and permission == "write",
    )


def client_for(*, auth_identity=None, service=None):
    app = FastAPI()
    app.include_router(router)
    app.state.tenant_facebook_health_service = service

    async def auth_override():
        token = auth_identity_var.set(auth_identity)
        try:
            yield True
        finally:
            auth_identity_var.reset(token)

    app.dependency_overrides[require_auth] = auth_override
    return TestClient(app)


def endpoint(client):
    return client.post(
        "/api/v1/tenant-health/facebook/connections/connection-a/observe",
        params={"brand_id": "brand-a"},
    )


def test_observe_route_uses_authenticated_workspace_and_returns_only_safe_receipt_fields():
    service = FakeHealthService()
    authenticated = identity()
    client = client_for(auth_identity=authenticated, service=service)

    response = endpoint(client)

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "workspace_id": "workspace-a",
        "brand_id": "brand-a",
        "connection_id": "connection-a",
        "health_state": "healthy",
        "evidence_id": "evidence-a",
        "observation_id": "observation-a",
        "event_id": "event-a",
    }
    assert service.calls == [(authenticated, "brand-a", "connection-a")]


def test_observe_route_rejects_species_identity_without_explicit_tenant_workspace():
    client = client_for(
        auth_identity=identity(workspace_id=None),
        service=FakeHealthService(),
    )

    response = endpoint(client)

    assert response.status_code == 403


def test_observe_route_requires_write_permission():
    client = client_for(
        auth_identity=identity(allowed=False),
        service=FakeHealthService(),
    )

    response = endpoint(client)

    assert response.status_code == 403


def test_observe_route_is_disabled_when_startup_gates_do_not_compose_service():
    client = client_for(auth_identity=identity(), service=None)

    response = endpoint(client)

    assert response.status_code == 503


def test_observe_route_returns_rate_limit_when_probe_window_is_already_used():
    service = FakeHealthService(
        error=TenantFacebookHealthServiceError(
            "health_observation_cooldown_or_in_progress"
        )
    )
    client = client_for(auth_identity=identity(), service=service)

    response = endpoint(client)

    assert response.status_code == 429


def test_observe_route_preserves_durable_receipt_when_event_publication_is_pending():
    receipt = SimpleNamespace(
        health_state="degraded",
        evidence_id="evidence-persisted",
        observation_id="observation-persisted",
    )
    service = FakeHealthService(
        error=TenantHealthPublisherError(
            "signed_health_event_publish_failed",
            receipt=receipt,
        )
    )
    client = client_for(auth_identity=identity(), service=service)

    response = endpoint(client)

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "error": "health_evidence_persisted_event_pending",
        "evidence_id": "evidence-persisted",
        "observation_id": "observation-persisted",
        "health_state": "degraded",
    }


def test_observe_route_does_not_disclose_context_lookup_details():
    service = FakeHealthService(
        error=TenantFacebookHealthContextError(
            "integration_connections_lookup_failed"
        )
    )
    client = client_for(auth_identity=identity(), service=service)

    response = endpoint(client)

    assert response.status_code == 503
    assert response.json()["detail"] == "Tenant connector context is unavailable"
