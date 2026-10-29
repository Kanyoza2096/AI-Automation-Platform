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
    from api.dependencies import require_auth
    from api.routers.monitoring import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_auth] = lambda: True
    return app


def test_notifications_endpoint_reads_durable_workspace_projection(monkeypatch):
    from api.dependencies import auth_workspace_id_var
    from services.notification_inbox_repository import NotificationInboxRepository
    auth_workspace_id_var.set("ws-test")
    monkeypatch.setattr(NotificationInboxRepository, "list_for_workspace", lambda self, workspace_id, **kwargs: [{"id": "n1", "workspace_id": workspace_id, "read_at": None}])
    with TestClient(_notification_app()) as client:
        response = client.get("/api/v1/notifications?unread_only=true")
    assert response.status_code == 200
    assert response.json()["notifications"][0]["id"] == "n1"
    assert response.json()["truth"] == "OBSERVED"


def test_notification_read_endpoint_persists_workspace_read_state(monkeypatch):
    from api.dependencies import auth_workspace_id_var
    from services.notification_inbox_repository import NotificationInboxRepository
    auth_workspace_id_var.set("ws-test")
    monkeypatch.setattr(NotificationInboxRepository, "mark_read", lambda self, workspace_id, notification_id: {"id": notification_id, "workspace_id": workspace_id, "read_at": "now"})
    with TestClient(_notification_app()) as client:
        response = client.post("/api/v1/notifications/notif-1/read")
    assert response.status_code == 200
    assert response.json()["notification"]["id"] == "notif-1"

def test_notifications_endpoint_keeps_auth_boundary():
    from api.dependencies import require_auth
    from api.routers.monitoring import router
    app = FastAPI()
    app.state.container = None
    app.include_router(router, prefix="/api/v1")
    with TestClient(app) as client:
        response = client.get("/api/v1/notifications")
    assert response.status_code in {401, 403}


def test_director_hands_is_disabled_in_production():
    from api.e2e_routes import director_hands
    from config.settings import settings
    from unittest.mock import Mock
    import asyncio
    original = settings.environment
    settings.environment = "production"
    try:
        request = Mock()
        with pytest.raises(Exception) as exc: # FastAPI HTTPException
            asyncio.run(director_hands(request))
        assert getattr(exc.value, "status_code", None) == 404
    finally:
        settings.environment = original
