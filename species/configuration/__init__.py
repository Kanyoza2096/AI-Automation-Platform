"""Global startup configuration (immutable after boot)."""
from __future__ import annotations
try:
    from config.settings import settings, Settings
except ImportError:
    settings = Settings = None  # type: ignore
__all__ = ["settings", "Settings"]
