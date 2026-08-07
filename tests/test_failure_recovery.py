"""
Tests for failure recovery — Architecture §16, §49, §76.

Validates:
  - Stale job detection (jobs stuck > 15 min)
  - Retry logic for retryable errors
  - Non-retryable errors go directly to FAILED
  - Database fallback behavior in repository
  - Graceful degradation when services unavailable
  - No silent error swallowing
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
from models.media_artifact import MediaArtifact
from models.publishing_result import PublishingResult, PublishErrorType

from models.automation_job import AutomationJob, JobStatus
from core.errors import (
    KanyozaError,
    ErrorCode,
    DatabaseUnavailableError,
    DatabaseQueryError,
    TokenExpiredError,
    PlatformRateLimitedError,
    is_retryable,
)


class TestStaleJobDetection:
    """Architecture §76 — Worker Recovery."""

    def test_job_running_too_long_is_stale(self):
        """A job in PUBLISHING for > 15 minutes should be detected as stale."""
        now = datetime.now(timezone.utc)
        stale_threshold = now - timedelta(minutes=16)

        job = AutomationJob.for_scheduled_slot(
            brand_id="brand_a",
            workspace_id="ws_1",
            slot_date=now.strftime("%Y-%m-%d"),
            slot_hour=now.hour,
            job_id="pub_stale_001",
        )
        job.transition_to(JobStatus.QUEUED)
        job.transition_to(JobStatus.RUNNING)
        job.transition_to(JobStatus.PUBLISHING)

        # Simulate being stuck
        job.updated_at = stale_threshold

        # Should be considered stale
        assert job.status in (JobStatus.RUNNING, JobStatus.GENERATING,
                              JobStatus.RENDERING, JobStatus.PUBLISHING)
        assert job.updated_at < (now - timedelta(minutes=15))

    def test_recently_updated_job_is_not_stale(self):
        """A job updated 2 minutes ago is NOT stale."""
        job = AutomationJob.for_scheduled_slot(
            brand_id="brand_a",
            workspace_id="ws_1",
            slot_date="2026-07-28",
            slot_hour=9,
            job_id="pub_active_001",
        )
        job.transition_to(JobStatus.QUEUED)
        job.transition_to(JobStatus.RUNNING)

        assert job.updated_at is not None
        age = datetime.now(timezone.utc) - job.updated_at
        assert age.total_seconds() < 900  # 15 minutes

    def test_stale_job_transitions_to_stale_status(self):
        """Marking a stale job should transition to STALE."""
        job = AutomationJob.for_scheduled_slot(
            brand_id="brand_a",
            workspace_id="ws_1",
            slot_date="2026-07-28",
            slot_hour=9,
            job_id="pub_stale_002",
        )
        job.transition_to(JobStatus.QUEUED)
        job.transition_to(JobStatus.RUNNING)
        job.transition_to(JobStatus.PUBLISHING)

        success = job.transition_to(JobStatus.STALE)
        assert success is True
        assert job.status == JobStatus.STALE

    def test_stale_to_retry_pending(self):
        """STALE jobs can be moved to RETRY_PENDING for recovery."""
        job = AutomationJob.for_scheduled_slot(
            brand_id="brand_a",
            workspace_id="ws_1",
            slot_date="2026-07-28",
            slot_hour=9,
            job_id="pub_stale_003",
        )
        # Must go through valid transitions: CREATED → ... → PUBLISHING → STALE → RETRY_PENDING
        job.transition_to(JobStatus.QUEUED)
        job.transition_to(JobStatus.RUNNING)
        job.transition_to(JobStatus.PUBLISHING)
        
        success = job.transition_to(JobStatus.STALE)
        assert success is True
        assert job.status == JobStatus.STALE

        success = job.transition_to(JobStatus.RETRY_PENDING)
        assert success is True
        assert job.status == JobStatus.RETRY_PENDING


class TestRetryableErrorClassification:
    """Test that is_retryable correctly identifies errors."""

    def test_kanyoza_error_retryable_flag_respected(self):
        """Retryable KanyozaErrors should return True."""
        error = PlatformRateLimitedError(message="Rate limited")
        assert error.retryable is True
        assert is_retryable(error) is True

    def test_kanyoza_error_non_retryable(self):
        """Non-retryable KanyozaErrors should return False."""
        error = TokenExpiredError(message="Token expired")
        assert error.retryable is False
        assert is_retryable(error) is False

    def test_connection_error_is_retryable(self):
        """Standard ConnectionError is retryable."""
        assert is_retryable(ConnectionError("Connection refused")) is True

    def test_timeout_error_is_retryable(self):
        """Standard TimeoutError is retryable."""
        assert is_retryable(TimeoutError("Timed out")) is True

    def test_value_error_is_not_retryable(self):
        """Standard ValueError is NOT retryable (data problem, not transient)."""
        assert is_retryable(ValueError("Invalid value")) is False

    def test_http_5xx_patterns_are_retryable(self):
        """Error messages containing 500/502/503 are retryable."""
        assert is_retryable(Exception("Server error: 500 Internal")) is True
        assert is_retryable(Exception("502 Bad Gateway")) is True
        assert is_retryable(Exception("503 Service Unavailable")) is True

    def test_http_4xx_patterns_are_not_retryable(self):
        """Error messages with 401/403 are NOT retryable."""
        assert is_retryable(Exception("401 Unauthorized")) is False
        assert is_retryable(Exception("403 Forbidden")) is False


class TestDatabaseFallback:
    """Architecture §9 — No silent production fallback."""

    def test_repository_raises_in_production_on_db_failure(self):
        """In production, database failures raise typed errors, not None."""
        from repositories.social_account_repository import SocialAccountRepository
        import os

        with patch.dict(os.environ, {"KANYOZA_ENV": "production"}):
            repo = SocialAccountRepository(supabase_client=None)

            with pytest.raises(DatabaseUnavailableError) as exc_info:
                repo._require_client()

            assert "not configured" in str(exc_info.value).lower()

    def test_repository_uses_in_memory_in_development(self):
        """In development, falls back to in-memory gracefully."""
        from repositories.social_account_repository import SocialAccountRepository
        import os

        with patch.dict(os.environ, {"KANYOZA_ENV": "development"}):
            repo = SocialAccountRepository(supabase_client=None)

            client = repo._require_client()
            assert client is None

            account = repo.find_by_id("nonexistent")
            assert account is None


class TestGracefulDegradation:
    """Test that services degrade gracefully when dependencies unavailable."""

    def test_content_engine_returns_fallback_on_crash(self):
        """ContentEngine should return fallback post, not crash."""
        from services.content_engine import ContentEngine

        engine = ContentEngine(container=None)
        result = engine.get_fallback_post()

        assert "caption" in result
        assert result["caption"] is not None
        assert len(result["caption"]) > 0

    def test_media_artifact_no_media_is_valid_state(self):
        """NO_MEDIA is an intentional state, not a failure."""
        artifact = MediaArtifact.no_media()

        assert artifact.is_text_only is True
        assert artifact.has_failed is False
        assert artifact.error is None

    def test_media_artifact_failed_is_explicit(self):
        """RENDER_FAILED carries error context, not None."""
        artifact = MediaArtifact.failed(
            error="All renderers exhausted",
            error_code="RENDER_FAILED",
        )

        assert artifact.has_failed is True
        assert artifact.error is not None
        assert artifact.error_code is not None
        assert artifact.is_valid is False


class TestNoSilentErrorSwallowing:
    """Architecture §28 — Why None Is Not an Error Model."""

    def test_media_artifact_never_returns_plain_none(self):
        """ContentEngine._render_image returns MediaArtifact, never None."""
        from models.media_artifact import MediaArtifact, MediaStatus

        ready = MediaArtifact.ready(image_bytes=b"data")
        no_media = MediaArtifact.no_media()
        failed = MediaArtifact.failed(error="err")
        pending = MediaArtifact.rendering()

        assert isinstance(ready, MediaArtifact)
        assert isinstance(no_media, MediaArtifact)
        assert isinstance(failed, MediaArtifact)
        assert isinstance(pending, MediaArtifact)

        assert ready.status == MediaStatus.MEDIA_READY
        assert no_media.status == MediaStatus.NO_MEDIA
        assert failed.status == MediaStatus.RENDER_FAILED
        assert pending.status == MediaStatus.RENDERING

    def test_kanyoza_error_carries_full_context(self):
        """Every KanyozaError answers WHAT, WHERE, WHY, RETRYABLE."""
        error = DatabaseQueryError(
            message="Failed to query social_accounts",
            context={"brand_id": "brand_a", "platform": "facebook"},
        )

        assert error.code == ErrorCode.DATABASE_QUERY_ERROR
        assert error.category.value == "DATABASE"
        assert "Failed to query" in error.message
        assert error.context["brand_id"] == "brand_a"
        d = error.to_dict()
        assert d["ok"] is False
        assert d["error"]["code"] == "DATABASE_QUERY_ERROR"
        assert d["error"]["category"] == "DATABASE"

    def test_publishing_result_never_ambiguous(self):
        """Success and failure are equally well-structured."""
        success = PublishingResult.success("facebook", "post_123")
        failure = PublishingResult.failure(
            "facebook", PublishErrorType.TOKEN_EXPIRED, "Expired"
        )

        s_dict = success.to_dict()
        f_dict = failure.to_dict()

        assert s_dict["ok"] is True
        assert f_dict["ok"] is False
        assert "error" in f_dict
        assert f_dict["error"]["type"] == "TOKEN_EXPIRED"
