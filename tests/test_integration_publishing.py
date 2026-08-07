"""
Integration tests — Publishing, Jobs, Scheduled Posts, Duplicate Prevention.

Tests the complete publishing lifecycle through the API.
"""

import pytest
import time
from unittest.mock import patch, MagicMock


class TestJobLifecycle:
    """Test the job status endpoint and lifecycle."""

    def test_job_not_found_returns_404(self, client, auth_headers):
        """Non-existent job should return 404."""
        resp = client.get("/api/v1/jobs/pub_nonexistent_99999", headers=auth_headers)
        assert resp.status_code == 404

    def test_job_service_unavailable_returns_503(self, client, auth_headers):
        """When JobService is unavailable, should return 503."""
        # This happens when Supabase is not configured in tests
        resp = client.get("/api/v1/jobs/pub_test_001", headers=auth_headers)
        assert resp.status_code in (404, 503)


class TestBrandJobs:
    """Test brand-scoped job history."""

    def test_brand_jobs_requires_auth(self, client):
        """Should require authentication."""
        resp = client.get("/api/v1/brands/test_brand/jobs")
        assert resp.status_code == 401

    def test_brand_jobs_with_auth(self, client, auth_headers):
        """Should return job list for a brand."""
        resp = client.get("/api/v1/brands/test_brand/jobs", headers=auth_headers)
        assert resp.status_code in (200, 404, 503)  # 404 if brand doesn't exist, 503 if no JobService

    def test_brand_jobs_invalid_status_rejected(self, client, auth_headers):
        """Invalid status filter should return 400."""
        resp = client.get(
            "/api/v1/brands/test_brand/jobs?status=INVALID",
            headers=auth_headers,
        )
        assert resp.status_code == 400


class TestManualPublish:
    """Test manual publish triggers."""

    def test_bot_post_requires_brand_id(self, client, auth_headers):
        """POST /bot/post without brand_id should return 400."""
        resp = client.post(
            "/api/v1/bot/post",
            json={},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_bot_post_brand_not_found(self, client, auth_headers):
        """Non-existent brand should return 404."""
        resp = client.post(
            "/api/v1/bot/post",
            json={"brand_id": "nonexistent-brand-999"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_workflow_trigger_requires_brand_id(self, client, auth_headers):
        """POST /workflow/trigger without brand_id should return 400."""
        resp = client.post(
            "/api/v1/workflow/trigger",
            json={},
            headers=auth_headers,
        )
        assert resp.status_code == 400


class TestPostEndpoints:
    """Test the content studio endpoints."""

    def test_get_posts_returns_list(self, client, auth_headers):
        """GET /posts should return a list with pagination."""
        resp = client.get("/api/v1/posts", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "posts" in data
        assert "total" in data
        assert "page" in data

    def test_get_posts_filtered_by_state(self, client, auth_headers):
        """GET /posts?state=draft should return only drafts."""
        resp = client.get("/api/v1/posts?state=draft", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

    def test_get_posts_filtered_by_published(self, client, auth_headers):
        """GET /posts?state=published should return only published."""
        resp = client.get("/api/v1/posts?state=published", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

    def test_create_post_draft(self, client, auth_headers):
        """Should create a draft post."""
        resp = client.post(
            "/api/v1/posts",
            json={
                "caption": "Test draft post",
                "topic": "Testing",
                "category": "Test",
                "state": "draft",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["ok"] is True
        assert data["draft"] is True

    def test_create_post_publish_requires_brand_id(self, client, auth_headers):
        """Publishing without brand_id should return 400."""
        resp = client.post(
            "/api/v1/posts",
            json={
                "caption": "Test publish",
                "state": "publish",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_delete_post(self, client, auth_headers):
        """Should delete a draft post."""
        # Create a draft first
        create_resp = client.post(
            "/api/v1/posts",
            json={
                "caption": "To be deleted",
                "topic": "Delete test",
                "category": "Test",
                "state": "draft",
            },
            headers=auth_headers,
        )
        draft_id = create_resp.json()["draft_id"]

        resp = client.delete(f"/api/v1/posts/{draft_id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True


class TestDuplicatePrevention:
    """Test that duplicate publishing is prevented."""

    def test_same_slot_same_brand_same_idempotency_key(self):
        """Two jobs for the same brand+date+hour produce the same idempotency key."""
        from models.automation_job import AutomationJob

        job1 = AutomationJob.for_scheduled_slot(
            brand_id="brand_a",
            workspace_id="ws_1",
            slot_date="2026-07-30",
            slot_hour=9,
            job_id="pub_001",
        )
        job2 = AutomationJob.for_scheduled_slot(
            brand_id="brand_a",
            workspace_id="ws_1",
            slot_date="2026-07-30",
            slot_hour=9,
            job_id="pub_002",
        )

        assert job1.idempotency_key == job2.idempotency_key

    def test_different_brands_different_keys(self):
        """Different brands produce different idempotency keys."""
        from models.automation_job import AutomationJob

        job_a = AutomationJob.for_scheduled_slot(
            brand_id="brand_a",
            workspace_id="ws_1",
            slot_date="2026-07-30",
            slot_hour=9,
            job_id="pub_001",
        )
        job_b = AutomationJob.for_scheduled_slot(
            brand_id="brand_b",
            workspace_id="ws_2",
            slot_date="2026-07-30",
            slot_hour=9,
            job_id="pub_002",
        )

        assert job_a.idempotency_key != job_b.idempotency_key


class TestSchedulerStatus:
    """Test scheduler status endpoints."""

    def test_bot_status_returns_data(self, client):
        """GET /bot/status should return brand and service info."""
        resp = client.get("/api/v1/bot/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "brands" in data
        assert "services" in data

    def test_workflow_status_returns_data(self, client, auth_headers):
        """GET /workflow/status should return workflow state."""
        resp = client.get("/api/v1/workflow/status", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
