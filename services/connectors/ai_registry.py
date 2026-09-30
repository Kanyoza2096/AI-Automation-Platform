"""Explicit registry for AI interpretation providers."""
from __future__ import annotations
from typing import Any


def create_ai_provider_adapter(provider: str, **kwargs: Any):
    name = str(provider or "").strip().lower()
    if name == "cloudflare_ai":
        from services.connectors.cloudflare_ai_adapter import CloudflareAIAdapter
        return CloudflareAIAdapter(**kwargs)
    raise ValueError(f"unsupported AI provider {name!r}; supported: cloudflare_ai")


def supported_ai_providers() -> tuple[str, ...]:
    return ("cloudflare_ai",)
