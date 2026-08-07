"""
Tests for multi-tenant account isolation — Architecture §17, §31, §38.

THE MOST IMPORTANT TEST IN THE PLATFORM (§38):
  Brand A → Facebook Page A
  Brand B → Facebook Page B
  publish(Brand A) must only use Page A
  publish(Brand B) must only use Page B

Validates:
  - Brand-level credential isolation
  - find_by_brand_and_platform returns correct account
  - Cross-brand credential leakage impossible
  - Workspace isolation enforced
"""

import importlib
import inspect
import pytest
from unittest.mock import MagicMock, patch
from models.social_account import SocialAccount


# ── Resolve FacebookPlugin regardless of actual class name in the module ──

def _get_facebook_plugin_class():
    """Find FacebookPlugin class in plugins.facebook.plugin.
    
    Uses multiple strategies to find the class regardless of its exact name.
    """
    module = importlib.import_module("plugins.facebook.plugin")
    
    # Strategy 1: Look for any class with "Facebook" and "Plugin" in the name
    for name in dir(module):
        obj = getattr(module, name)
        if isinstance(obj, type) and "Facebook" in name and "Plugin" in name:
            return obj
    
    # Strategy 2: Look for any class with "plugin" in the name (case-insensitive)
    for name, obj in inspect.getmembers(module, inspect.isclass):
        if "facebook" in name.lower() and "plugin" in name.lower():
            return obj
    
    # Strategy 3: Try the standard import path
    try:
        from plugins.facebook.plugin import FacebookPlugin
        return FacebookPlugin
    except ImportError:
        pass
    
    # Strategy 4: List available classes and raise helpful error
    available = [n for n, o in inspect.getmembers(module, inspect.isclass)]
    raise ImportError(
        f"Could not find FacebookPlugin class in plugins.facebook.plugin. "
        f"Available classes: {available}"
    )


FacebookPlugin = _get_facebook_plugin_class()


# ═══════════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestSocialAccountRepositoryIsolation:
    """Test that account resolution is deterministic per brand."""

    def make_account(self, brand_id, platform, page_id, workspace_id="ws_1"):
        """Helper to create a mock social account."""
        return SocialAccount(
            id=f"acc_{brand_id}_{platform}",
            brand_id=brand_id,
            workspace_id=workspace_id,
            platform=platform,
            platform_account_id=page_id,
            access_token=f"token_{brand_id}_{platform}",
            account_name=f"Page for {brand_id}",
            enabled=True,
        )

    def test_find_by_brand_and_platform_returns_correct_account(self):
        """Brand A + facebook → Page A, Brand B + facebook → Page B."""
        from repositories.social_account_repository import SocialAccountRepository
        repo = SocialAccountRepository(supabase_client=None)

        account_a = self.make_account("brand_a", "facebook", "page_a_id")
        account_b = self.make_account("brand_b", "facebook", "page_b_id")
        repo._in_memory[account_a.id] = account_a
        repo._in_memory[account_b.id] = account_b

        result_a = repo.find_by_brand_and_platform("brand_a", "facebook")
        result_b = repo.find_by_brand_and_platform("brand_b", "facebook")

        assert result_a is not None
        assert result_b is not None
        assert result_a.platform_account_id == "page_a_id"
        assert result_b.platform_account_id == "page_b_id"
        assert result_a.platform_account_id != result_b.platform_account_id

    def test_brand_a_cannot_resolve_brand_b_account(self):
        """find_by_brand_and_platform only returns matching brand."""
        from repositories.social_account_repository import SocialAccountRepository
        repo = SocialAccountRepository(supabase_client=None)

        account_a = self.make_account("brand_a", "facebook", "page_a_id")
        repo._in_memory[account_a.id] = account_a

        result = repo.find_by_brand_and_platform("brand_b", "facebook")
        assert result is None

    def test_multiple_platforms_per_brand(self):
        """Brand A has Facebook AND Instagram — resolution is deterministic."""
        from repositories.social_account_repository import SocialAccountRepository
        repo = SocialAccountRepository(supabase_client=None)

        fb = self.make_account("brand_a", "facebook", "page_fb")
        ig = self.make_account("brand_a", "instagram", "page_ig")
        repo._in_memory[fb.id] = fb
        repo._in_memory[ig.id] = ig

        fb_result = repo.find_by_brand_and_platform("brand_a", "facebook")
        ig_result = repo.find_by_brand_and_platform("brand_a", "instagram")

        assert fb_result.platform_account_id == "page_fb"
        assert ig_result.platform_account_id == "page_ig"

    def test_workspace_isolation(self):
        """Accounts in workspace_1 are not visible in workspace_2 queries."""
        from repositories.social_account_repository import SocialAccountRepository
        repo = SocialAccountRepository(supabase_client=None)

        ws1_account = self.make_account("brand_a", "facebook", "page_a", workspace_id="ws_1")
        ws2_account = self.make_account("brand_b", "facebook", "page_b", workspace_id="ws_2")
        repo._in_memory[ws1_account.id] = ws1_account
        repo._in_memory[ws2_account.id] = ws2_account

        ws1_results = repo.find_by_workspace("ws_1")
        ws2_results = repo.find_by_workspace("ws_2")

        assert len(ws1_results) == 1
        assert ws1_results[0].workspace_id == "ws_1"
        assert len(ws2_results) == 1
        assert ws2_results[0].workspace_id == "ws_2"


class TestFacebookPluginCredentialResolution:
    """Test that FacebookPlugin resolves the correct brand credentials."""

    def make_mock_brand(self, brand_id, name):
        brand = MagicMock()
        brand.id = brand_id
        brand.name = name
        return brand

    def test_credential_resolution_uses_brand_not_global(self):
        """Plugin should query SocialAccountRepository, not use global env."""
        mock_fb_service = MagicMock()
        mock_social_repo = MagicMock()

        plugin = FacebookPlugin(mock_fb_service, mock_social_repo)
        plugin._active = True

        mock_account = MagicMock()
        mock_account.id = "acc_123"
        mock_account.platform_account_id = "page_brand_a"
        mock_account.access_token = "token_brand_a"
        mock_account.platform_config = {}

        mock_social_repo.find_by_brand_and_platform.return_value = mock_account

        brand = self.make_mock_brand("brand_a", "Brand A")
        page_id, page_token = plugin._resolve_credentials(brand)

        mock_social_repo.find_by_brand_and_platform.assert_called_once_with(
            "brand_a", "facebook"
        )
        assert page_id == "page_brand_a"
        assert page_token == "token_brand_a"

    def test_no_account_found_returns_none(self):
        """When no account exists, credentials should be None."""
        mock_fb_service = MagicMock()
        mock_social_repo = MagicMock()
        mock_social_repo.find_by_brand_and_platform.return_value = None

        plugin = FacebookPlugin(mock_fb_service, mock_social_repo)
        plugin._active = True

        brand = self.make_mock_brand("brand_no_account", "No Account Brand")
        page_id, page_token = plugin._resolve_credentials(brand)

        assert page_id is None
        assert page_token is None


class TestMultiTenantPublishing:
    """Architecture §38 — The most important test in the entire platform."""

    def test_two_brands_publish_to_correct_pages(self):
        """
        Brand A → Facebook Page A
        Brand B → Facebook Page B
        
        This must be deterministic. Never: accounts[0].
        """
        from repositories.social_account_repository import SocialAccountRepository

        repo = SocialAccountRepository(supabase_client=None)

        brand_a_fb = SocialAccount(
            id="acc_a_fb",
            brand_id="brand_a",
            workspace_id="ws_1",
            platform="facebook",
            platform_account_id="fb_page_a_123",
            access_token="token_a",
            account_name="Brand A Facebook Page",
            enabled=True,
        )
        brand_b_fb = SocialAccount(
            id="acc_b_fb",
            brand_id="brand_b",
            workspace_id="ws_1",
            platform="facebook",
            platform_account_id="fb_page_b_456",
            access_token="token_b",
            account_name="Brand B Facebook Page",
            enabled=True,
        )

        repo._in_memory[brand_a_fb.id] = brand_a_fb
        repo._in_memory[brand_b_fb.id] = brand_b_fb

        result_a = repo.find_by_brand_and_platform("brand_a", "facebook")
        result_b = repo.find_by_brand_and_platform("brand_b", "facebook")

        # CRITICAL ASSERTIONS
        assert result_a.platform_account_id == "fb_page_a_123", \
            "Brand A MUST use Page A"
        assert result_b.platform_account_id == "fb_page_b_456", \
            "Brand B MUST use Page B"
        assert result_a.platform_account_id != result_b.platform_account_id, \
            "Different brands MUST use different pages"
        assert result_a.access_token == "token_a", \
            "Brand A MUST use its own token"
        assert result_b.access_token == "token_b", \
            "Brand B MUST use its own token"
