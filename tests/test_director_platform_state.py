from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from core.platform_state import (
    CapabilityState,
    PlatformStateSnapshot,
    Readiness,
    ServiceState,
)
from services.director import Director, create_app


NOW = datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc)


class FakeStore:
    def __init__(self, _client):
        pass

    async def load(self, *, scope_type="platform", scope_id="platform"):
        assert scope_type == "platform"
        assert scope_id == "platform"
        return PlatformStateSnapshot(
            revision=4,
            updated_at=NOW,
            services=(
                ServiceState(
                    service_id="director",
                    owner="director",
                    readiness=Readiness.HEALTHY,
                    observed_at=NOW,
                    source_version="health-v1",
                    dependencies=(),
                    capabilities=("strategic_planning",),
                    critical=True,
                    evidence=(),
                ),
            ),
            capabilities=(
                CapabilityState(
                    capability_id="strategic_planning",
                    owner_service="director",
                    readiness=Readiness.HEALTHY,
                    observed_at=NOW,
                ),
            ),
        )


def test_platform_state_route_returns_authoritative_snapshot_for_platform_token(monkeypatch):
    monkeypatch.setenv("MASTER_API_TOKEN", "master-test-token")
    monkeypatch.setattr(
        "services.supabase.get_service_client_from_settings",
        lambda: object(),
    )
    monkeypatch.setattr("core.platform_state_store.PlatformStateStore", FakeStore)
    client = TestClient(create_app(Director(supabase_client=None)))

    response = client.get(
        "/platform-state",
        headers={"Authorization": "Bearer master-test-token"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "available"
    assert payload["revision"] == 4
    assert payload["services"][0]["service_id"] == "director"
    assert payload["services"][0]["readiness"] == "healthy"
    assert payload["capabilities"][0]["capability_id"] == "strategic_planning"


def test_platform_state_route_denies_tenant_even_when_tenant_owner(monkeypatch):
    monkeypatch.setenv("MASTER_API_TOKEN", "master-test-token")
    app = create_app(Director(supabase_client=None))

    @app.middleware("http")
    async def tenant_context(request, call_next):
        request.state.request_context = SimpleNamespace(
            workspace_id="workspace-a",
            role="owner",
            species_privileges=False,
        )
        request.state.auth_identity = SimpleNamespace(valid=True)
        return await call_next(request)

    response = TestClient(app).get(
        "/platform-state",
        headers={"Authorization": "Bearer valid-user-session"},
    )
    assert response.status_code == 403


def test_platform_state_route_requires_authentication(monkeypatch):
    monkeypatch.setenv("MASTER_API_TOKEN", "master-test-token")
    client = TestClient(create_app(Director(supabase_client=None)))

    response = client.get("/platform-state")

    assert response.status_code == 401
