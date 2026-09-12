"""Canonical Kanyoza platform release identity.

Component modules may carry their own component versions, but anything that
identifies the deployable platform release MUST import PLATFORM_VERSION from
this module. Keeping release identity in one place prevents health endpoints,
logs, packaging, and release gates from drifting apart.
"""

from __future__ import annotations

PLATFORM_NAME = "Kanyoza Systems AI Platform"
PLATFORM_VERSION = "14.7.5"

__all__ = ["PLATFORM_NAME", "PLATFORM_VERSION"]
