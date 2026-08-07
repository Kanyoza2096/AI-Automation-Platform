"""
Integration tests — Feature Flags, Rate Limits, Monitoring.

Tests configuration persistence and monitoring endpoints.
"""

import pytest
from unittest.mock import patch


class TestFeatureFlags:
    """Test feature flag read/write with persistence."""

    def test_get_features_returns_defaults(self, client, auth_headers):
        """GET /features should return default flags."""
        resp = client.get("/api/v1/features", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "features" in data
        assert "auto_reply" in data["features"]
        assert "auto_post" in data["features"]

    def test_toggle_feature_requires_params(self, client, auth_headers):
        """Toggle without feature name should return 400."""
        resp = client.post(
            "/api/v1/features/toggle",
            json={},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_toggle_feature_requires_enabled(self, client, auth_headers):
        """Toggle without enabled should return 400."""
        resp = client.post(
            "/api/v1/features/toggle",
            json={"feature": "auto_post"},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_toggle_feature_succeeds(self, client, auth_headers):
        """Should toggle a feature flag."""
        resp = client.post(
            "/api/v1/features/toggle",
            json={"feature": "auto_post", "enabled": False},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["feature"] == "auto_post"
        assert data["enabled"] is False


class TestRateLimits:
    """Test rate limit configuration."""

    def test_get_rate_limits_returns_config(self, client):
        """GET /rate-limits should return configuration."""
        resp = client.get("/api/v1/rate-limits")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "rate_limits" in data

    def test_put_rate_limits_updates_config(self, client, auth_headers):
        """PUT /rate-limits/config should update configuration."""
        resp = client.put(
            "/api/v1/rate-limits/config",
            json={
                "limit": 50,
                "window": 120,
                "scope": "workspace",
                "workspace_id": "default",
                "provider": "default",
                "enabled": True,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["updated"] is True

    def test_unblock_clears_rate_limit(self, client, auth_headers):
        """POST /rate-limits/unblock should unblock an identifier."""
        resp = client.post(
            "/api/v1/rate-limits/unblock",
            json={"identifier": "test-user-123"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True


class TestMonitoring:
    """Test monitoring and log streaming endpoints."""

    def test_monitoring_stats_returns_canonical_format(self, client, auth_headers):
        """GET /monitoring/stats should return canonical contract."""
        resp = client.get("/api/v1/monitoring/stats", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "total" in data
        assert "errors" in data
        assert "warnings" in data
        assert "success" in data
        assert "rate" in data
        assert "as_of" in data

    def test_stream_token_requires_auth(self, client):
        """POST /monitoring/stream-token should require auth."""
        resp = client.post("/api/v1/monitoring/stream-token")
        assert resp.status_code == 401

    def test_stream_token_generated_with_auth(self, client, auth_headers):
        """POST /monitoring/stream-token should generate a token."""
        resp = client.post("/api/v1/monitoring/stream-token", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "token" in data
        assert "expires_in_seconds" in data

    def test_stream_requires_token(self, client):
        """GET /monitoring/stream without token should return 401."""
        resp = client.get("/api/v1/monitoring/stream")
        assert resp.status_code == 401

    def test_stream_invalid_token_rejected(self, client):
        """GET /monitoring/stream with invalid token should return 401."""
        resp = client.get("/api/v1/monitoring/stream?token=invalid-token-123")
        assert resp.status_code == 401

    def test_logs_recent_returns_entries(self, client, auth_headers):
        """GET /logs/recent should return log entries."""
        resp = client.get("/api/v1/logs/recent", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "logs" in data


class TestSystemHealth:
    """Test health check endpoints."""

    def test_health_endpoint(self, client):
        """GET /health should return healthy status."""
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("healthy", "degraded", "starting")

    def test_readiness_endpoint(self, client):
        """GET /health/ready should return readiness status."""
        resp = client.get("/health/ready")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("ready", "not_ready")

    def test_deep_health_returns_services(self, client):
        """GET /health/deep should return service statuses."""
        resp = client.get("/api/v1/health/deep")
        assert resp.status_code == 200
        data = resp.json()
        assert "services" in data
        assert "gemini" in data["services"]
        assert "facebook" in data["services"]
        assert "supabase" in data["services"]

    def test_bot_status_is_public(self, client):
        """GET /bot/status should be accessible without auth."""
        resp = client.get("/api/v1/bot/status")
        assert resp.status_code == 200
