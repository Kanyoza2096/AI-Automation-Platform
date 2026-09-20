"""Regression tests for route initialization and placeholder hardening."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_main_import_is_warning_clean():
    """Importing the ASGI application must not create un-awaited coroutines."""
    backend = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONWARNINGS"] = "error"
    result = subprocess.run(
        [sys.executable, "-c", "import routes.event_api; print('import-ok')"],
        cwd=backend,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "import-ok" in result.stdout
    assert "RuntimeWarning" not in result.stderr


def test_event_api_route_registration_does_not_start_async_initialization():
    from routes.event_api import register_event_api_routes

    app = FastAPI()
    register_event_api_routes(app)
    assert app.routes


def _notification_app():
    from api.v1 import require_auth, router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_auth] = lambda: True
    return app


def test_notifications_endpoint_returns_contract_shape():
    with TestClient(_notification_app()) as client:
        response = client.get("/api/v1/notifications")
    assert response.status_code == 200
    assert response.json()["notifications"] == []


def test_notification_read_endpoint_returns_read_contract():
    with TestClient(_notification_app()) as client:
        response = client.post("/api/v1/notifications/notif-1/read")
    assert response.status_code == 200
    assert response.json()["read"] is True


def test_notifications_endpoint_keeps_auth_boundary():
    from api.v1 import router

    app = FastAPI()
    app.state.container = None
    app.include_router(router)
    with TestClient(app) as client:
        response = client.get("/api/v1/notifications")
    assert response.status_code in {401, 403}


def test_director_hands_is_disabled_in_production():
    from api.e2e_routes import router
    app = FastAPI()
    app.include_router(router)

    # E2E auth is still required, so override it for this contract test.
    from api.v1 import require_auth
    app.dependency_overrides[require_auth] = lambda: True
    with TestClient(app) as client:
        response = client.get("/e2e/director-hands")
    assert response.status_code == 404
    assert response.json()["detail"] == "E2E diagnostic routes are disabled in production"
