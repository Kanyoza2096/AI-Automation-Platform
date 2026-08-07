"""
Tests for config/settings.py — startup validation enforces required secrets in production.

Architecture §41 — Configuration Validation:
  - Only GEMINI_KEY is truly required (multi-tenant mode)
  - Facebook credentials are per-brand, not global
  - Missing optional vars warn but don't prevent startup
"""

import os
import sys
import pytest


class TestSettingsValidation:
    """Verify that production validation is strict and dev is permissive."""

    def test_missing_required_returns_gemini_key(self, monkeypatch):
        """missing_required() lists GEMINI_KEY when unset.
        
        In multi-tenant mode, only GEMINI_KEY is truly required.
        Facebook credentials come from social_accounts table per brand.
        """
        for key in ("GEMINI_KEY",):
            monkeypatch.delenv(key, raising=False)

        import importlib
        import config.settings as settings_mod
        importlib.reload(settings_mod)

        missing = settings_mod.settings.missing_required()
        assert "GEMINI_KEY" in missing
        # Facebook credentials are NOT required globally anymore
        assert "PAGE_ACCESS_TOKEN" not in missing

    def test_no_missing_when_gemini_set(self, monkeypatch):
        """missing_required() returns empty list when GEMINI_KEY is present."""
        monkeypatch.setenv("GEMINI_KEY", "gkey")

        import importlib
        import config.settings as settings_mod
        importlib.reload(settings_mod)

        assert settings_mod.settings.missing_required() == []

    def test_missing_warn_shows_supabase_when_unset(self, monkeypatch):
        """missing_warn() lists optional but important vars like Supabase."""
        monkeypatch.setenv("GEMINI_KEY", "gkey")
        for key in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"):
            monkeypatch.delenv(key, raising=False)

        import importlib
        import config.settings as settings_mod
        importlib.reload(settings_mod)

        warn_missing = settings_mod.settings.missing_warn()
        assert "SUPABASE_URL" in warn_missing
        assert "SUPABASE_SERVICE_ROLE_KEY" in warn_missing

    def test_validate_required_exits_in_production_when_gemini_missing(self, monkeypatch):
        """In production mode, validate_required() calls sys.exit(1) when GEMINI_KEY missing."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("SUPABASE_URL", "https://proj.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service_key")
        monkeypatch.setenv("SUPABASE_ANON_KEY", "anon_key")
        monkeypatch.setenv("ENCRYPTION_KEY", "QwmBBkOkj3GebJSYjGAhcigSjWaftx6T7hJG_n_ZBqo=")
        monkeypatch.delenv("GEMINI_KEY", raising=False)

        import importlib
        import config.settings as settings_mod
        importlib.reload(settings_mod)

        with pytest.raises(SystemExit) as exc_info:
            settings_mod.settings.validate_required()
        assert exc_info.value.code == 1

    def test_validate_required_raises_in_production_when_supabase_missing(self, monkeypatch):
        """In production, missing Supabase credentials raise ValueError (§14 — mandatory persistence).

        Architecture §14: Production persistence is mandatory.
        SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in production or
        validate_required() raises ValueError to prevent a stateless production boot.
        Facebook credentials are still optional (per-brand in social_accounts table).
        """
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("GEMINI_KEY", "gkey")
        for key in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.delenv("PAGE_ACCESS_TOKEN", raising=False)

        import importlib
        import config.settings as settings_mod
        importlib.reload(settings_mod)

        # §14: Supabase is now mandatory in production — raises ValueError
        with pytest.raises(ValueError, match="SUPABASE"):
            settings_mod.settings.validate_required()

    def test_validate_required_passes_in_production_when_supabase_set(self, monkeypatch):
        """In production, validate_required() passes when GEMINI_KEY + Supabase are set."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("GEMINI_KEY", "gkey")
        monkeypatch.setenv("SUPABASE_URL", "https://proj.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service_key")
        monkeypatch.setenv("SUPABASE_ANON_KEY", "anon_key")
        monkeypatch.setenv("ENCRYPTION_KEY", "QwmBBkOkj3GebJSYjGAhcigSjWaftx6T7hJG_n_ZBqo=")
        monkeypatch.delenv("PAGE_ACCESS_TOKEN", raising=False)

        import importlib
        import config.settings as settings_mod
        importlib.reload(settings_mod)

        # Should not raise — all mandatory production vars are present
        settings_mod.settings.validate_required()

    def test_validate_required_does_not_exit_in_development(self, monkeypatch):
        """In development mode, validate_required() warns but does NOT exit."""
        monkeypatch.setenv("ENVIRONMENT", "development")
        for key in ("GEMINI_KEY", "SUPABASE_URL", "PAGE_ACCESS_TOKEN"):
            monkeypatch.delenv(key, raising=False)

        import importlib
        import config.settings as settings_mod
        importlib.reload(settings_mod)

        # Should not raise SystemExit
        settings_mod.settings.validate_required()

    def test_facebook_configured_is_false_without_global_creds(self, monkeypatch):
        """facebook_configured returns False when no global Facebook credentials.
        
        In multi-tenant mode, Facebook creds come from social_accounts table.
        Global PAGE_ACCESS_TOKEN is optional.
        """
        monkeypatch.setenv("GEMINI_KEY", "gkey")
        monkeypatch.delenv("PAGE_ACCESS_TOKEN", raising=False)
        monkeypatch.delenv("PAGE_ID", raising=False)

        import importlib
        import config.settings as settings_mod
        importlib.reload(settings_mod)

        assert settings_mod.settings.facebook_configured is False

    def test_facebook_configured_is_true_with_global_creds(self, monkeypatch):
        """facebook_configured returns True when global creds exist (dev mode)."""
        monkeypatch.setenv("GEMINI_KEY", "gkey")
        monkeypatch.setenv("PAGE_ACCESS_TOKEN", "pat")
        monkeypatch.setenv("PAGE_ID", "123")

        import importlib
        import config.settings as settings_mod
        importlib.reload(settings_mod)

        assert settings_mod.settings.facebook_configured is True

    def test_all_new_settings_have_defaults(self, monkeypatch):
        """All new timeout/retry/fallback settings have sensible defaults."""
        monkeypatch.setenv("GEMINI_KEY", "gkey")

        import importlib
        import config.settings as settings_mod
        importlib.reload(settings_mod)

        s = settings_mod.settings

        # Timeouts
        assert s.ai_request_timeout_seconds == 60
        assert s.facebook_connect_timeout_seconds == 10
        assert s.supabase_connect_timeout_seconds == 10

        # Retries
        assert s.ai_max_retries == 3
        assert s.facebook_max_retries == 3
        assert s.job_max_attempts == 3

        # Fallback
        assert s.allow_text_only_fallback is True

        # Scheduler
        assert s.job_stale_threshold_minutes == 15
        assert s.scheduler_startup_grace_seconds == 30

        # Render limits
        assert s.max_image_size_bytes == 10 * 1024 * 1024

    def test_retry_config_helpers_return_dicts(self, monkeypatch):
        """get_ai_retry_config, get_publish_retry_config, get_render_retry_config."""
        monkeypatch.setenv("GEMINI_KEY", "gkey")

        import importlib
        import config.settings as settings_mod
        importlib.reload(settings_mod)

        s = settings_mod.settings

        ai_config = s.get_ai_retry_config()
        assert ai_config["max_attempts"] == 3
        assert "base_delay_seconds" in ai_config

        publish_config = s.get_publish_retry_config()
        assert publish_config["max_attempts"] == 3
        assert "backoff_multiplier" in publish_config

        render_config = s.get_render_retry_config()
        assert render_config["max_attempts"] == 3
