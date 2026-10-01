"""Explicit registry for AI interpretation providers."""
from __future__ import annotations
from typing import Any


def create_ai_provider_adapter(provider: str, **kwargs: Any):
    """Instantiate the adapter for a provider name.

    The registry is the single source of truth for which providers exist.
    Adding a provider means adding a branch here and a class in
    services/connectors/<name>_adapter.py. No other file changes.
    """
    name = str(provider or "").strip().lower()
    if name == "cloudflare_ai":
        from services.connectors.cloudflare_ai_adapter import CloudflareAIAdapter
        return CloudflareAIAdapter(**kwargs)
    if name == "gemini":
        from services.connectors.gemini_ai_adapter import GeminiAIAdapter
        return GeminiAIAdapter(**kwargs)
    raise ValueError(
        f"unsupported AI provider {name!r}; "
        f"supported: {', '.join(supported_ai_providers())}"
    )


def supported_ai_providers() -> tuple[str, ...]:
    return ("cloudflare_ai", "gemini")
