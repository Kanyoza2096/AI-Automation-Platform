"""Regression coverage for the API-key router's trusted workspace resolver."""

from api.routers import api_keys
from core.plugin_execution import resolve_plugin_workspace


def test_api_key_router_imports_canonical_workspace_resolver():
    """All key lifecycle handlers must use the canonical request-context resolver."""
    assert api_keys.resolve_plugin_workspace is resolve_plugin_workspace
