"""
Tests for MediaArtifact — Architecture §5, §15.

Validates:
  - Factory methods produce correct states
  - Status checks (is_valid, has_failed, etc.)
  - Serialization safety (bytes never leaked to dict)
  - Backward compatibility with image_bytes
"""

import pytest
from models.media_artifact import MediaArtifact, MediaStatus


class TestMediaArtifactFactories:
    """Test all factory methods produce correct state."""

    def test_ready_creates_valid_artifact(self):
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        artifact = MediaArtifact.ready(
            image_bytes=png_bytes,
            topic="Test Topic",
            brand_id="brand_123",
        )

        assert artifact.is_valid is True
        assert artifact.has_failed is False
        assert artifact.is_text_only is False
        assert artifact.is_pending is False
        assert artifact.status == MediaStatus.MEDIA_READY
        assert artifact.bytes == png_bytes
        assert artifact.size_kb > 0
        assert artifact.topic == "Test Topic"
        assert artifact.brand_id == "brand_123"

    def test_no_media_creates_text_only_artifact(self):
        artifact = MediaArtifact.no_media(
            topic="Text Post",
            brand_id="brand_456",
        )

        assert artifact.is_valid is False
        assert artifact.has_failed is False
        assert artifact.is_text_only is True
        assert artifact.is_pending is False
        assert artifact.status == MediaStatus.NO_MEDIA
        assert artifact.bytes == b""
        assert artifact.error is None

    def test_failed_creates_error_artifact(self):
        artifact = MediaArtifact.failed(
            error="All renderers exhausted",
            error_code="RENDER_FAILED",
            topic="Failed Topic",
            brand_id="brand_789",
        )

        assert artifact.is_valid is False
        assert artifact.has_failed is True
        assert artifact.is_text_only is False
        assert artifact.is_pending is False
        assert artifact.status == MediaStatus.RENDER_FAILED
        assert artifact.error == "All renderers exhausted"
        assert artifact.error_code == "RENDER_FAILED"
        assert artifact.bytes == b""

    def test_rendering_creates_pending_artifact(self):
        artifact = MediaArtifact.rendering(
            source="render_queue",
            topic="Pending Topic",
        )

        assert artifact.is_valid is False
        assert artifact.has_failed is False
        assert artifact.is_text_only is False
        assert artifact.is_pending is True
        assert artifact.status == MediaStatus.RENDERING
        assert artifact.source == "render_queue"


class TestMediaArtifactSerialization:
    """Test serialization safety — bytes never exposed."""

    def test_to_dict_excludes_bytes(self):
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 500
        artifact = MediaArtifact.ready(image_bytes=png_bytes)

        result = artifact.to_dict()

        assert "bytes" not in result
        assert result["valid"] is True
        assert result["status"] == "media_ready"
        assert result["size_kb"] > 0

    def test_to_dict_includes_error_for_failed(self):
        artifact = MediaArtifact.failed(
            error="Something broke",
            error_code="RENDER_FAILED",
        )

        result = artifact.to_dict()

        assert result["error"] == "Something broke"
        assert result["error_code"] == "RENDER_FAILED"
        assert result["valid"] is False
        assert result["status"] == "render_failed"

    def test_to_publish_dict_is_subset(self):
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        artifact = MediaArtifact.ready(image_bytes=png_bytes)

        result = artifact.to_publish_dict()

        assert "bytes" not in result
        assert "topic" not in result  # Not needed for publish result
        assert result["status"] == "media_ready"
        assert result["valid"] is True


class TestMediaArtifactStatuses:
    """Test that all four statuses are mutually exclusive."""

    def test_valid_is_only_media_ready(self):
        ready = MediaArtifact.ready(image_bytes=b"data")
        no_media = MediaArtifact.no_media()
        failed = MediaArtifact.failed(error="err")
        pending = MediaArtifact.rendering()

        assert ready.is_valid is True
        assert no_media.is_valid is False
        assert failed.is_valid is False
        assert pending.is_valid is False

    def test_text_only_is_only_no_media(self):
        ready = MediaArtifact.ready(image_bytes=b"data")
        no_media = MediaArtifact.no_media()
        failed = MediaArtifact.failed(error="err")
        pending = MediaArtifact.rendering()

        assert ready.is_text_only is False
        assert no_media.is_text_only is True
        assert failed.is_text_only is False
        assert pending.is_text_only is False
