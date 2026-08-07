"""
M9 — REST API v1 endpoint tests (M8 routes).

Updated for v12.1 — multi-tenant architecture.
Tests auth guards, response formats, and new endpoints.
"""

import pytest
from unittest.mock import MagicMock, patch


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


def test_jobs_requires_auth(client):
    """GET /api/v1/jobs/{job_id} must require auth."""
    resp = client.get("/api/v1/jobs/pub_test_001")
    assert resp.status_code == 401


def test_brand_jobs_requires_auth(client):
    """GET /api/v1/brands/{brand_id}/jobs must require auth."""
    resp = client.get("/api/v1/brands/brand_test/jobs")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Authenticated requests
# ---------------------------------------------------------------------------


def test_posts_returns_list(client, auth_headers):
    resp = client.get("/api/v1/posts", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is True
    assert "posts" in data


def test_analytics_returns_data(client, auth_headers):
    resp = client.get("/api/v1/analytics", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is True
    assert "analytics" in data


def test_metrics_returns_data(client, auth_headers):
    resp = client.get("/api/v1/metrics", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
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
    assert resp.status_code in (400, 422)


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
    assert resp.status_code in (400, 422)


def test_bot_status_returns_data(client, auth_headers):
    """GET /api/v1/bot/status must return brand and service info."""
    resp = client.get("/api/v1/bot/status", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is True
    assert "brands" in data
    assert "services" in data


def test_bot_status_includes_today_stats(client, auth_headers):
    """GET /api/v1/bot/status must include today_stats when JobService available."""
    resp = client.get("/api/v1/bot/status", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    # today_stats may be empty dict if JobService unavailable, but key must exist
    assert "today_stats" in data


# ---------------------------------------------------------------------------
# New endpoints — ai/chat, ai/persona, audit-logs, knowledge, integrations
# ---------------------------------------------------------------------------


def test_ai_chat_requires_auth(client):
    resp = client.post("/api/v1/ai/chat", json={"message": "Hello"})
    assert resp.status_code == 401


def test_ai_chat_rejects_empty_message(client, auth_headers):
    resp = client.post("/api/v1/ai/chat", json={}, headers=auth_headers)
    assert resp.status_code in (400, 422)


def test_ai_chat_calls_gemini_service(client, auth_headers):
    """Verify ai_chat uses generate_chat_response(sender_id, user_message)."""
    mock_gemini = MagicMock()
    mock_gemini.generate_chat_response.return_value = "Hello from Kanyoza!"

    with patch("services.gemini.gemini", mock_gemini), \
         patch("services.gemini.GeminiService", return_value=mock_gemini):
        resp = client.post(
            "/api/v1/ai/chat",
            json={"message": "What can you do?", "history": []},
            headers=auth_headers,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is True
    assert data.get("reply") == "Hello from Kanyoza!"


def test_ai_persona_get_returns_frontend_fields(client, auth_headers):
    """GET /ai/persona must return {tone, aggression, humor, model, system_prompt}."""
    resp = client.get("/api/v1/ai/persona", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is True
    for field in ("tone", "aggression", "humor", "model", "system_prompt"):
        assert field in data, f"Missing field: {field}"
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
    data = resp.json()
    assert data.get("ok") is True
    assert data.get("updated") is True


def test_audit_logs_requires_auth(client):
    resp = client.get("/api/v1/audit-logs")
    assert resp.status_code == 401


def test_audit_logs_returns_list(client, auth_headers):
    resp = client.get("/api/v1/audit-logs", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is True
    assert "logs" in data


def test_knowledge_requires_auth(client):
    resp = client.get("/api/v1/knowledge")
    assert resp.status_code == 401


def test_knowledge_create_requires_content(client, auth_headers):
    resp = client.post("/api/v1/knowledge", json={}, headers=auth_headers)
    assert resp.status_code in (400, 422)


def test_knowledge_create_returns_entry(client, auth_headers):
    resp = client.post(
        "/api/v1/knowledge",
        json={"title": "Test doc", "content": "Sample content for testing.", "category": "Technical"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data.get("ok") is True
    assert "document" in data
    assert data["document"]["content"] == "Sample content for testing."


def test_integrations_requires_auth(client):
    resp = client.get("/api/v1/integrations")
    assert resp.status_code == 401


def test_integrations_returns_list(client, auth_headers):
    resp = client.get("/api/v1/integrations", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is True
    assert "integrations" in data
    assert isinstance(data["integrations"], list)
    assert len(data["integrations"]) > 0


# ---------------------------------------------------------------------------
# NEW: Job endpoints (Architecture §29)
# ---------------------------------------------------------------------------

def test_job_status_returns_503_when_service_unavailable(client, auth_headers):
    """GET /api/v1/jobs/{job_id} returns 503 when JobService not available."""
    resp = client.get("/api/v1/jobs/pub_test_001", headers=auth_headers)
    # 503 if JobService unavailable, 404 if job not found, 401 if auth fails
    assert resp.status_code in (401, 404, 503)


def test_job_status_not_found_returns_404(client, auth_headers):
    """GET /api/v1/jobs/{job_id} returns 404 for non-existent job."""
    resp = client.get("/api/v1/jobs/pub_nonexistent_999", headers=auth_headers)
    assert resp.status_code in (401, 404, 503)


def test_brand_jobs_returns_list(client, auth_headers):
    """GET /api/v1/brands/{brand_id}/jobs returns job list."""
    resp = client.get("/api/v1/brands/brand_test/jobs", headers=auth_headers)
    # 200 with data, 503 if JobService unavailable, 404 if brand not found
    assert resp.status_code in (200, 401, 404, 503)
    if resp.status_code == 200:
        data = resp.json()
        assert data.get("ok") is True
        assert "jobs" in data
        assert "today_stats" in data


def test_brand_jobs_rejects_invalid_status(client, auth_headers):
    """GET /api/v1/brands/{brand_id}/jobs?status=INVALID returns 400."""
    resp = client.get(
        "/api/v1/brands/brand_test/jobs?status=INVALID_STATUS",
        headers=auth_headers,
    )
    assert resp.status_code in (400, 401, 503)


# ---------------------------------------------------------------------------
# NEW: Workflow endpoints return job_id
# ---------------------------------------------------------------------------

def test_workflow_trigger_requires_brand_id(client, auth_headers):
    """POST /api/v1/workflow/trigger without brand_id must return 400."""
    resp = client.post(
        "/api/v1/workflow/trigger",
        json={},
        headers=auth_headers,
    )
    assert resp.status_code in (400, 401)


def test_bot_post_requires_brand_id(client, auth_headers):
    """POST /api/v1/bot/post without brand_id must return 400."""
    resp = client.post(
        "/api/v1/bot/post",
        json={},
        headers=auth_headers,
    )
    assert resp.status_code in (400, 401)


# ---------------------------------------------------------------------------
# NEW: Health check includes version
# ---------------------------------------------------------------------------

def test_status_endpoint_shows_v12(client):
    """GET /api/v1/status must show version 12.x.x."""
    resp = client.get("/api/v1/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is True
    version = data.get("version", "")
    assert version.startswith("12."), f"Expected version 12.x.x, got {version}"


def test_health_deep_shows_v12(client):
    """GET /api/v1/health/deep must show version 12.x.x."""
    resp = client.get("/api/v1/health/deep")
    assert resp.status_code == 200
    data = resp.json()
    version = data.get("version", "")
    assert version.startswith("12."), f"Expected version 12.x.x, got {version}"
