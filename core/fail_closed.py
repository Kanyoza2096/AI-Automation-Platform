"""
Production fail-closed policy — Species Law.

Development may use fallbacks.
Production must not silently persist to memory when infrastructure fails.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


class InfrastructureUnavailable(RuntimeError):
    """Raised when a required dependency is down and production forbids fallbacks."""

    def __init__(self, dependency: str, detail: str = ""):
        self.dependency = dependency
        super().__init__(f"{dependency} unavailable" + (f": {detail}" if detail else ""))


@dataclass(frozen=True)
class RuntimeMode:
    environment: str  # development | staging | production | test
    allow_memory_fallback: bool
    allow_chaos: bool


def resolve_runtime_mode(
    environment: Optional[str] = None,
    *,
    chaos_allow_production: bool = False,
) -> RuntimeMode:
    env = (environment or "development").strip().lower()
    is_prod = env in ("production", "prod")
    return RuntimeMode(
        environment=env,
        allow_memory_fallback=not is_prod,
        allow_chaos=(not is_prod) or bool(chaos_allow_production),
    )


def assert_persistable(mode: RuntimeMode, dependency: str = "database") -> None:
    """Call before accepting a write that would otherwise fall back to memory."""
    if not mode.allow_memory_fallback:
        raise InfrastructureUnavailable(
            dependency,
            "production forbids silent in-memory persistence",
        )


def assert_chaos_allowed(mode: RuntimeMode) -> None:
    if not mode.allow_chaos:
        raise PermissionError("Chaos engineering is forbidden in this environment")
