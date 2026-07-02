"""
Tests for config/settings.py — startup validation enforces required secrets in production.
"""

import os
import sys
import pytest


class TestSettingsValidation:
    """Verify that production validation is strict and dev is permissive."""

    def test_missing_required_returns_var_names(self, monkeypatch):
        """missing_required() lists every unset required variable."""
        # Import fresh with all required vars cleared
        for key in ("GEMINI_KEY", "PAGE_ACCESS_TOKEN", "PAGE_ID", "VERIFY_TOKEN", "APP_SECRET"):
            monkeypatch.delenv(key, raising=False)

        # Reimport settings to pick up cleared env
        import importlib
        import config.settings as settings_mod
        importlib.reload(settings_mod)

        missing = settings_mod.settings.missing_required()
        assert "GEMINI_KEY" in missing
        assert "PAGE_ACCESS_TOKEN" in missing
        assert "APP_SECRET" in missing

    def test_no_missing_when_all_set(self, monkeypatch):
        """missing_required() returns empty list when all secrets are present."""
        monkeypatch.setenv("GEMINI_KEY", "gkey")
        monkeypatch.setenv("PAGE_ACCESS_TOKEN", "pat")
        monkeypatch.setenv("PAGE_ID", "123")
        monkeypatch.setenv("VERIFY_TOKEN", "vt")
        monkeypatch.setenv("APP_SECRET", "secret")

        import importlib
        import config.settings as settings_mod
        importlib.reload(settings_mod)

        assert settings_mod.settings.missing_required() == []

    def test_validate_required_exits_in_production(self, monkeypatch):
        """In production mode, validate_required() calls sys.exit(1) when secrets missing."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        for key in ("GEMINI_KEY", "PAGE_ACCESS_TOKEN", "PAGE_ID", "VERIFY_TOKEN", "APP_SECRET"):
            monkeypatch.delenv(key, raising=False)

        import importlib
        import config.settings as settings_mod
        importlib.reload(settings_mod)

        with pytest.raises(SystemExit) as exc_info:
            settings_mod.settings.validate_required()
        assert exc_info.value.code == 1

    def test_validate_required_does_not_exit_in_development(self, monkeypatch):
        """In development mode, validate_required() warns but does NOT exit."""
        monkeypatch.setenv("ENVIRONMENT", "development")
        for key in ("GEMINI_KEY", "PAGE_ACCESS_TOKEN", "PAGE_ID", "VERIFY_TOKEN", "APP_SECRET"):
            monkeypatch.delenv(key, raising=False)

        import importlib
        import config.settings as settings_mod
        importlib.reload(settings_mod)

        # Should not raise SystemExit
        settings_mod.settings.validate_required()
