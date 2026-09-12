"""
Persistence policy — production must not silently claim durable success
when only in-memory stores are available.
"""

from __future__ import annotations

from core.fail_closed import resolve_runtime_mode, InfrastructureUnavailable


def assert_durable_backend_available(dependency: str = "database") -> None:
    try:
        from config.settings import settings
        env = getattr(settings, "environment", None) or getattr(settings, "env", None)
        mode = resolve_runtime_mode(env)
    except Exception:
        mode = resolve_runtime_mode("development")

    if mode.allow_memory_fallback:
        return

    # Production: require supabase-like durability signal
    try:
        from services.supabase import supabase_service
        if supabase_service and getattr(supabase_service, "client", None):
            return
    except Exception:
        pass

    raise InfrastructureUnavailable(
        dependency,
        "production forbids silent in-memory persistence",
    )


def memory_fallback_allowed() -> bool:
    try:
        from config.settings import settings
        env = getattr(settings, "environment", None)
        return resolve_runtime_mode(env).allow_memory_fallback
    except Exception:
        return True
