"""
M9 — Health and status endpoint tests.
"""

import pytest


def test_status_endpoint_public(client):
    """GET /health is publicly accessible and returns service status."""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, dict)
    assert len(data) > 0


def test_api_v1_status_public(client):
    """GET /api/v1/status is publicly accessible and returns version info."""
    resp = client.get("/api/v1/status")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, dict)
    assert len(data) > 0


def test_health_deep_public(client):
    """GET /api/v1/health/deep returns a structured health report."""
    resp = client.get("/api/v1/health/deep")
    assert resp.status_code == 200
    data = resp.json()
    assert "services" in data or "status" in data


def test_bot_status_public(client):
    """GET /api/v1/bot/status is available without auth."""
    resp = client.get("/api/v1/bot/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is True


def test_guardian_status_public(client):
    """GET /api/v1/guardian/status is available without auth."""
    resp = client.get("/api/v1/guardian/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is True
