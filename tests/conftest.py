"""
Kanyoza Systems AI Platform v10 — pytest test fixtures.

Usage:
    pytest tests/ -v

The test client is created in a dedicated "testing" environment so that
no real secrets are required.  All external services (Gemini, Facebook,
GitHub) are mocked at the module level.
"""

from __future__ import annotations

import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Environment setup — must happen before importing the app
# ---------------------------------------------------------------------------

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("GEMINI_KEY", "test-gemini-key")
os.environ.setdefault("PAGE_ACCESS_TOKEN", "test-page-token")
os.environ.setdefault("PAGE_ID", "12345")
os.environ.setdefault("VERIFY_TOKEN", "test-verify-token")
os.environ.setdefault("APP_SECRET", "test-app-secret")


# ---------------------------------------------------------------------------
# Lightweight stubs for heavy optional dependencies
# ---------------------------------------------------------------------------

def _stub_module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


# Stub playwright so tests don't need the Chromium binary
_playwright_stub = _stub_module("playwright")
_playwright_sync = _stub_module("playwright.sync_api", sync_playwright=MagicMock())
sys.modules.setdefault("playwright", _playwright_stub)
sys.modules.setdefault("playwright.sync_api", _playwright_sync)

# Stub google.generativeai
_genai_stub = _stub_module("google.generativeai", GenerativeModel=MagicMock(), configure=MagicMock())
_google_stub = _stub_module("google")
sys.modules.setdefault("google", _google_stub)
sys.modules.setdefault("google.generativeai", _genai_stub)


# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def app():
    """Create the Flask application in testing mode (no lifecycle / no schedulers)."""
    with patch("lifecycle.register_lifecycle", return_value=None):
        from application_factory import create_app
        from dependency_registry import build_container
        from config.settings import settings as _s
        container = build_container()
        application = create_app(settings=_s, container=container)
    application.config["TESTING"] = True
    application.config["WTF_CSRF_ENABLED"] = False
    return application


@pytest.fixture()
def client(app):
    """Flask test client for making HTTP requests."""
    with app.test_client() as c:
        yield c


@pytest.fixture()
def auth_headers():
    """Headers with a valid X-API-Key for protected endpoints."""
    return {"X-API-Key": os.environ["APP_SECRET"]}


@pytest.fixture()
def verify_token():
    return os.environ["VERIFY_TOKEN"]
