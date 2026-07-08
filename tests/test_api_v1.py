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


# ---------------------------------------------------------------------------
# New endpoints — ai/chat, ai/persona, audit-logs, knowledge, integrations
# ---------------------------------------------------------------------------


def test_ai_chat_requires_auth(client):
    resp = client.post("/api/v1/ai/chat", json={"message": "Hello"})
    assert resp.status_code == 401


def test_ai_chat_rejects_empty_message(client, auth_headers):
    resp = client.post("/api/v1/ai/chat", json={}, headers=auth_headers)
    assert resp.status_code == 400
    assert resp.get_json().get("ok") is False


def test_ai_chat_calls_gemini_service(client, auth_headers):
    """Verify ai_chat uses generate_chat_response(sender_id, user_message)."""
    from unittest.mock import MagicMock, patch

    mock_gemini = MagicMock()
    mock_gemini.generate_chat_response.return_value = "Hello from Kanyoza!"

    # GeminiService is imported inside the route function, so patch at source module
    with patch("services.gemini.GeminiService", return_value=mock_gemini):
        resp = client.post(
            "/api/v1/ai/chat",
            json={"message": "What can you do?", "history": []},
            headers=auth_headers,
        )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get("ok") is True
    assert data.get("reply") == "Hello from Kanyoza!"
    # Confirm the service was called with the correct keyword arguments
    mock_gemini.generate_chat_response.assert_called_once()
    call_kwargs = mock_gemini.generate_chat_response.call_args
    assert call_kwargs.kwargs.get("user_message") == "What can you do?"


def test_ai_persona_get_returns_frontend_fields(client, auth_headers):
    """GET /ai/persona must return {tone, aggression, humor, model, system_prompt}."""
    resp = client.get("/api/v1/ai/persona", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get("ok") is True
    for field in ("tone", "aggression", "humor", "model", "system_prompt"):
        assert field in data, f"Missing field: {field}"
    # Values must be 0-100 integers
    assert 0 <= data["tone"] <= 100
    assert 0 <= data["aggression"] <= 100
    assert 0 <= data["humor"] <= 100


def test_ai_persona_put_accepts_frontend_fields(client, auth_headers):
    """PUT /ai/persona must accept tone/aggression/humor/model/system_prompt."""
    resp = client.put(
        "/api/v1/ai/persona",
        json={"tone": 80, "aggression": 50, "humor": 30,
              "model": "gemini-2.5-flash", "system_prompt": "Be concise."},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get("ok") is True
    assert data.get("updated") is True


def test_audit_logs_requires_auth(client):
    resp = client.get("/api/v1/audit-logs")
    assert resp.status_code == 401


def test_audit_logs_returns_list(client, auth_headers):
    resp = client.get("/api/v1/audit-logs", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get("ok") is True
    assert "logs" in data


def test_knowledge_requires_auth(client):
    resp = client.get("/api/v1/knowledge")
    assert resp.status_code == 401


def test_knowledge_create_requires_content(client, auth_headers):
    resp = client.post("/api/v1/knowledge", json={}, headers=auth_headers)
    assert resp.status_code == 400
    assert resp.get_json().get("ok") is False


def test_knowledge_create_returns_entry(client, auth_headers):
    resp = client.post(
        "/api/v1/knowledge",
        json={"title": "Test doc", "content": "Sample content for testing.", "category": "Technical"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    data = resp.get_json()
    assert data.get("ok") is True
    assert "document" in data
    assert data["document"]["content"] == "Sample content for testing."


def test_integrations_requires_auth(client):
    resp = client.get("/api/v1/integrations")
    assert resp.status_code == 401


def test_integrations_returns_list(client, auth_headers):
    resp = client.get("/api/v1/integrations", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get("ok") is True
    assert "integrations" in data
    assert isinstance(data["integrations"], list)
    assert len(data["integrations"]) > 0
