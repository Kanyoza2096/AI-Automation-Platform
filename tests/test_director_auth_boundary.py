from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]


def _build_director_app(monkeypatch):
    monkeypatch.setenv("MASTER_API_TOKEN", "master-test-token")
    monkeypatch.setenv("PYTHON_SERVICE_TOKEN", "service-test-token")
    from services.director import Director, create_app
    director = Director(supabase_client=None)
    return create_app(director)


def test_director_reuses_canonical_request_context(monkeypatch):
    app = _build_director_app(monkeypatch)

    @app.middleware("http")
    async def canonical_context(request, call_next):
        request.state.request_context = SimpleNamespace(
            workspace_id="ws-test",
            role="viewer",
            species_privileges=False,
        )
        request.state.auth_identity = SimpleNamespace(valid=True)
        return await call_next(request)

    client = TestClient(app)
    response = client.get("/status", headers={"Authorization": "Bearer valid-user-session"})
    assert response.status_code == 200


def test_director_rejects_unknown_direct_token(monkeypatch):
    app = _build_director_app(monkeypatch)
    client = TestClient(app)
    response = client.get("/status", headers={"Authorization": "Bearer wrong-token"})
    assert response.status_code == 401


def test_director_reports_missing_internal_auth_config_as_503(monkeypatch):
    monkeypatch.delenv("MASTER_API_TOKEN", raising=False)
    monkeypatch.delenv("PYTHON_SERVICE_TOKEN", raising=False)
    from services.director import Director, create_app
    app = create_app(Director(supabase_client=None))
    client = TestClient(app)
    response = client.get("/status", headers={"Authorization": "Bearer arbitrary-token"})
    assert response.status_code == 503


def test_director_viewer_cannot_mutate_with_canonical_context(monkeypatch):
    app = _build_director_app(monkeypatch)

    @app.middleware("http")
    async def canonical_context(request, call_next):
        request.state.request_context = SimpleNamespace(
            workspace_id="ws-test",
            role="viewer",
            species_privileges=False,
        )
        request.state.auth_identity = SimpleNamespace(valid=True)
        return await call_next(request)

    client = TestClient(app)
    response = client.post("/observe", headers={"Authorization": "Bearer valid-user-session"})
    assert response.status_code == 403
