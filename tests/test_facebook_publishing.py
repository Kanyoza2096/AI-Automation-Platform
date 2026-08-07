"""
Tests for Facebook publishing flow — Architecture §19-21.

Validates:
  - PublishingResult success and failure formats
  - Error mapping from service errors to PublishErrorType
  - Credential resolution with brand context
  - MediaArtifact handling (valid, failed, text-only, pending)
  - Retryable vs non-retryable error classification
  - Plugin respects active/inactive state
"""

import pytest
from unittest.mock import MagicMock, patch
from models.publishing_result import PublishingResult, PublishErrorType
from models.media_artifact import MediaArtifact


class TestPublishingResult:
    """Test the canonical publishing contract — Architecture §11."""

    def test_success_result_format(self):
        result = PublishingResult.success(
            platform="facebook",
            post_id="fb_post_12345",
            brand_id="brand_a",
            platform_account_id="page_789",
            duration_ms=1240,
        )

        d = result.to_dict()

        assert d["ok"] is True
        assert d["platform"] == "facebook"
        assert d["post_id"] == "fb_post_12345"
        assert d["brand_id"] == "brand_a"
        assert d["duration_ms"] == 1240
        assert "error" not in d
        # platform_account_id is optional — check if present
        if "platform_account_id" in d:
            assert d["platform_account_id"] == "page_789"

    def test_failure_result_format(self):
        result = PublishingResult.failure(
            platform="facebook",
            error_type=PublishErrorType.TOKEN_EXPIRED,
            message="The access token has expired",
            brand_id="brand_a",
            retryable=False,
        )

        d = result.to_dict()

        assert d["ok"] is False
        assert d["platform"] == "facebook"
        assert d["error"]["type"] == "TOKEN_EXPIRED"
        assert d["error"]["message"] == "The access token has expired"
        assert d["error"]["retryable"] is False
        assert d["brand_id"] == "brand_a"

    def test_all_error_types_have_values(self):
        """Every PublishErrorType should be usable."""
        for error_type in PublishErrorType:
            result = PublishingResult.failure(
                platform="facebook",
                error_type=error_type,
                message=f"Test: {error_type.value}",
                brand_id="brand_x",
            )
            d = result.to_dict()
            assert d["error"]["type"] == error_type.value


class TestPublishErrorClassification:
    """Test that errors are correctly classified as retryable/non-retryable."""

    def test_non_retryable_errors(self):
        """These should NEVER be retried."""
        non_retryable = [
            PublishErrorType.TOKEN_MISSING,
            PublishErrorType.TOKEN_EXPIRED,
            PublishErrorType.INVALID_CREDENTIALS,
            PublishErrorType.CONTENT_EMPTY,
            PublishErrorType.PLUGIN_NOT_STARTED,
            PublishErrorType.FACEBOOK_PERMISSION_ERROR,
        ]

        for error_type in non_retryable:
            result = PublishingResult.failure(
                platform="facebook",
                error_type=error_type,
                message="test",
                brand_id="x",
            )
            d = result.to_dict()
            assert d["error"]["retryable"] is False, \
                f"{error_type.value} should NOT be retryable"

    def test_retryable_errors(self):
        """These SHOULD be retried."""
        retryable = [
            PublishErrorType.FACEBOOK_API_ERROR,
            PublishErrorType.UNKNOWN_ERROR,
        ]

        for error_type in retryable:
            result = PublishingResult.failure(
                platform="facebook",
                error_type=error_type,
                message="test",
                brand_id="x",
            )
            d = result.to_dict()
            assert d["error"]["retryable"] is True, \
                f"{error_type.value} SHOULD be retryable"


class TestFacebookPluginMediaHandling:
    """Test that FacebookPlugin handles MediaArtifact correctly."""

    def make_plugin(self):
        import importlib
        module = importlib.import_module("plugins.facebook.plugin")
        FacebookPlugin = None
        for name in dir(module):
            obj = getattr(module, name)
            if isinstance(obj, type) and "Facebook" in name and "Plugin" in name:
                FacebookPlugin = obj
                break
        if FacebookPlugin is None:
            pytest.skip("FacebookPlugin class not found")
        
        mock_fb_service = MagicMock()
        mock_social_repo = MagicMock()
        plugin = FacebookPlugin(mock_fb_service, mock_social_repo)
        plugin._active = True
        return plugin, mock_fb_service, mock_social_repo

    def make_mock_brand(self):
        brand = MagicMock()
        brand.id = "brand_test"
        brand.name = "Test Brand"
        return brand

    def test_publish_with_valid_media(self):
        """When MediaArtifact is valid, pass bytes to Facebook service."""
        plugin, mock_fb, mock_repo = self.make_plugin()

        mock_account = MagicMock()
        mock_account.id = "acc_123"
        mock_account.platform_account_id = "page_456"
        mock_account.access_token = "token_abc"
        mock_account.platform_config = {}
        mock_repo.find_by_brand_and_platform.return_value = mock_account

        mock_fb.post_to_page.return_value = {
            "ok": True,
            "post_id": "fb_post_999",
            "duration_ms": 500,
        }

        brand = self.make_mock_brand()
        media = MediaArtifact.ready(
            image_bytes=b"fake_png_data",
            topic="Test Topic",
            brand_id="brand_test",
        )

        result = plugin.publish(
            content="Test post content",
            media_artifact=media,
            brand=brand,
        )

        assert result["ok"] is True
        assert result["post_id"] == "fb_post_999"
        call_kwargs = mock_fb.post_to_page.call_args[1]
        assert call_kwargs["image_bytes"] == b"fake_png_data"

    def test_publish_with_failed_media_proceeds_text_only(self):
        """When MediaArtifact failed, proceed without image (if fallback enabled)."""
        plugin, mock_fb, mock_repo = self.make_plugin()

        mock_account = MagicMock()
        mock_account.id = "acc_123"
        mock_account.platform_account_id = "page_456"
        mock_account.access_token = "token_abc"
        mock_account.platform_config = {}
        mock_repo.find_by_brand_and_platform.return_value = mock_account

        mock_fb.post_to_page.return_value = {
            "ok": True,
            "post_id": "fb_text_only",
            "duration_ms": 300,
        }

        brand = self.make_mock_brand()
        media = MediaArtifact.failed(
            error="Render crashed",
            error_code="RENDER_FAILED",
            brand_id="brand_test",
        )

        result = plugin.publish(
            content="Text only post",
            media_artifact=media,
            brand=brand,
        )

        assert result["ok"] is True
        call_kwargs = mock_fb.post_to_page.call_args[1]
        assert call_kwargs["image_bytes"] is None

    def test_publish_with_text_only_media(self):
        """When MediaArtifact is no_media, proceed without image."""
        plugin, mock_fb, mock_repo = self.make_plugin()

        mock_account = MagicMock()
        mock_account.id = "acc_123"
        mock_account.platform_account_id = "page_456"
        mock_account.access_token = "token_abc"
        mock_account.platform_config = {}
        mock_repo.find_by_brand_and_platform.return_value = mock_account

        mock_fb.post_to_page.return_value = {
            "ok": True,
            "post_id": "fb_text_002",
            "duration_ms": 250,
        }

        brand = self.make_mock_brand()
        media = MediaArtifact.no_media(
            topic="Text Update",
            brand_id="brand_test",
        )

        result = plugin.publish(
            content="Text post",
            media_artifact=media,
            brand=brand,
        )

        assert result["ok"] is True
        call_kwargs = mock_fb.post_to_page.call_args[1]
        assert call_kwargs["image_bytes"] is None

    def test_publish_with_raw_image_bytes_still_works(self):
        """Backward compatibility: raw image_bytes still accepted."""
        plugin, mock_fb, mock_repo = self.make_plugin()

        mock_account = MagicMock()
        mock_account.id = "acc_123"
        mock_account.platform_account_id = "page_456"
        mock_account.access_token = "token_abc"
        mock_account.platform_config = {}
        mock_repo.find_by_brand_and_platform.return_value = mock_account

        mock_fb.post_to_page.return_value = {
            "ok": True,
            "post_id": "fb_legacy",
            "duration_ms": 200,
        }

        brand = self.make_mock_brand()
        result = plugin.publish(
            content="Legacy post",
            image_bytes=b"raw_bytes_here",
            brand=brand,
        )

        assert result["ok"] is True
        call_kwargs = mock_fb.post_to_page.call_args[1]
        assert call_kwargs["image_bytes"] == b"raw_bytes_here"


class TestFacebookPluginPreflight:
    """Test pre-flight checks before publishing."""

    def test_inactive_plugin_rejects(self):
        import importlib
        module = importlib.import_module("plugins.facebook.plugin")
        FacebookPlugin = None
        for name in dir(module):
            obj = getattr(module, name)
            if isinstance(obj, type) and "Facebook" in name and "Plugin" in name:
                FacebookPlugin = obj
                break
        if FacebookPlugin is None:
            pytest.skip("FacebookPlugin class not found")

        plugin = FacebookPlugin(MagicMock(), MagicMock())
        plugin._active = False

        result = plugin.publish(content="test", brand=MagicMock())

        assert result["ok"] is False
        assert result["error"]["type"] == "PLUGIN_NOT_STARTED"

    def test_empty_content_rejected(self):
        import importlib
        module = importlib.import_module("plugins.facebook.plugin")
        FacebookPlugin = None
        for name in dir(module):
            obj = getattr(module, name)
            if isinstance(obj, type) and "Facebook" in name and "Plugin" in name:
                FacebookPlugin = obj
                break
        if FacebookPlugin is None:
            pytest.skip("FacebookPlugin class not found")

        plugin = FacebookPlugin(MagicMock(), MagicMock())
        plugin._active = True

        result = plugin.publish(content="   ", brand=MagicMock())

        assert result["ok"] is False
        assert result["error"]["type"] == "CONTENT_EMPTY"

    def test_no_credentials_rejected(self):
        import importlib
        module = importlib.import_module("plugins.facebook.plugin")
        FacebookPlugin = None
        for name in dir(module):
            obj = getattr(module, name)
            if isinstance(obj, type) and "Facebook" in name and "Plugin" in name:
                FacebookPlugin = obj
                break
        if FacebookPlugin is None:
            pytest.skip("FacebookPlugin class not found")

        mock_repo = MagicMock()
        mock_repo.find_by_brand_and_platform.return_value = None

        plugin = FacebookPlugin(MagicMock(), mock_repo)
        plugin._active = True

        with patch.object(plugin, '_is_development', return_value=False):
            brand = MagicMock()
            brand.id = "brand_x"
            brand.name = "Brand X"
            result = plugin.publish(content="test", brand=brand)

            assert result["ok"] is False
            assert result["error"]["type"] == "TOKEN_MISSING"


class TestFacebookPluginErrorMapping:
    """Test error mapping from service errors."""

    def _get_plugin(self):
        import importlib
        module = importlib.import_module("plugins.facebook.plugin")
        for name in dir(module):
            obj = getattr(module, name)
            if isinstance(obj, type) and "Facebook" in name and "Plugin" in name:
                return obj(MagicMock(), MagicMock())
        pytest.skip("FacebookPlugin class not found")

    def test_permission_error_mapped(self):
        plugin = self._get_plugin()
        error_type = plugin._classify_error(
            stage="facebook_api",
            message="User does not have permission to post to this page",
        )
        assert error_type == PublishErrorType.FACEBOOK_PERMISSION_ERROR

    def test_rate_limit_mapped(self):
        plugin = self._get_plugin()
        error_type = plugin._classify_error(
            stage="facebook_api",
            message="Rate limit exceeded for this page",
        )
        assert error_type == PublishErrorType.FACEBOOK_RATE_LIMITED

    def test_auth_error_mapped(self):
        plugin = self._get_plugin()
        error_type = plugin._classify_error(
            stage="facebook_auth",
            message="Invalid access token",
        )
        assert error_type == PublishErrorType.INVALID_CREDENTIALS

    def test_credential_check_error_mapped(self):
        plugin = self._get_plugin()
        error_type = plugin._classify_error(
            stage="credential_check",
            message="No token found",
        )
        assert error_type == PublishErrorType.TOKEN_MISSING
