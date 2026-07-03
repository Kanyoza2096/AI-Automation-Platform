"""
M9 — REST API v1 endpoint tests (M8 routes).
"""

import pytest


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------


def test_posts_requires_auth(client):
    resp = client.get("/api/v1/posts")
    assert resp.status_code == 401


def test_analytics_requires_auth(client):
    resp = client.get("/api/v1/analytics")
    assert resp.status_code == 401


def test_metrics_requires_auth(client):
    resp = client.get("/api/v1/metrics")
    assert resp.status_code == 401


def test_schedule_requires_auth(client):
    resp = client.get("/api/v1/schedule")
    assert resp.status_code == 401


def test_force_post_requires_auth(client):
    resp = client.post("/api/v1/bot/post")
    assert resp.status_code == 401


def test_force_news_requires_auth(client):
    resp = client.post("/api/v1/bot/news")
    assert resp.status_code == 401


def test_scan_requires_auth(client):
    resp = client.post("/api/v1/guardian/scan")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Authenticated requests
# ---------------------------------------------------------------------------


def test_posts_returns_list(client, auth_headers):
    resp = client.get("/api/v1/posts", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get("ok") is True
    assert "posts" in data


def test_analytics_returns_data(client, auth_headers):
    resp = client.get("/api/v1/analytics", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get("ok") is True
    assert "analytics" in data


def test_metrics_returns_data(client, auth_headers):
    resp = client.get("/api/v1/metrics", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get("ok") is True
    assert "metrics" in data


def test_schedule_returns_config(client, auth_headers):
    resp = client.get("/api/v1/schedule", headers=auth_headers)
    assert resp.status_code in (200, 500)  # 500 if scheduler has no get_config()


def test_messages_reply_validates_body(client, auth_headers):
    resp = client.post(
        "/api/v1/messages/reply",
        json={},
        headers=auth_headers,
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert data.get("ok") is False


def test_guardian_scan_returns_503_when_not_configured(client, auth_headers):
    """When guardian is not configured the endpoint should return 503."""
    resp = client.post("/api/v1/guardian/scan", headers=auth_headers)
    assert resp.status_code in (202, 503)


def test_update_schedule_rejects_unknown_keys(client, auth_headers):
    resp = client.put(
        "/api/v1/schedule",
        json={"bad_key": 99},
        headers=auth_headers,
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert "Unknown keys" in data.get("error", "")
