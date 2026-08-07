"""
Kanyoza Systems AI Platform v12.1 — pytest test fixtures.

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
os.environ.setdefault("MASTER_API_TOKEN", "test-master-token")


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
    """Create the FastAPI application in testing mode.
    
    Patches boot_infrastructure to prevent schedulers/emitters
    from starting during tests.
    """
    # Suppress scheduler startup during tests
    with patch("lifecycle.boot_infrastructure", return_value=None):
        from main import create_app
        application = create_app()
    return application



@pytest.fixture()
def client(app):
    """Starlette TestClient for making HTTP requests."""
    from starlette.testclient import TestClient
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def auth_headers():
    """Headers with a valid master API token for protected endpoints."""
    return {"Authorization": f"Bearer {os.environ['MASTER_API_TOKEN']}"}


@pytest.fixture()
def verify_token():
    return os.environ["VERIFY_TOKEN"]
