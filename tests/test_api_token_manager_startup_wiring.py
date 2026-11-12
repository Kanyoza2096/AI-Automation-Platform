"""Regression coverage for the API token singleton's startup database wiring."""

from core.api_tokens import api_token_manager
from dependency_registry import _wire_module_api_token_manager


def test_startup_wiring_connects_the_module_level_token_manager():
    """API routes use this singleton directly rather than resolving the DI binding."""
    previous_client = api_token_manager._db
    sentinel_client = object()
    try:
        _wire_module_api_token_manager(sentinel_client)
        assert api_token_manager._db is sentinel_client
    finally:
        api_token_manager._db = previous_client
