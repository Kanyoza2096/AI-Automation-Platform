"""
Kanyoza Systems AI Platform v10 — Dependency Registry.

This is the ONLY place where concrete implementations are wired to their
interfaces inside the service container. Import nothing here except:
  - the ServiceContainer
  - the Settings singleton
  - concrete service classes (lazily, inside register lambdas to avoid
    circular imports at module load time)

Add a new service in three steps:
  1. Define the interface (ABC / Protocol) in its module.
  2. Create the concrete implementation.
  3. Add a register() call in _register_services() below.
"""

from __future__ import annotations

import logging

from config.settings import settings
from service_container import ServiceContainer

logger = logging.getLogger("kanyoza.registry")


def build_container() -> ServiceContainer:
    """
    Construct and populate the application service container.

    Returns a fully-wired ServiceContainer ready for use by the
    application factory.  Called once at bootstrap.
    """
    container = ServiceContainer()
    _register_infrastructure(container)
    _register_services(container)
    logger.info(
        "Service container built — %d bindings registered.",
        len(container.registered_types()),
    )
    return container


# ---------------------------------------------------------------------------
# Infrastructure (low-level, no business logic)
# ---------------------------------------------------------------------------


def _register_infrastructure(c: ServiceContainer) -> None:
    """Register config and cross-cutting infrastructure services."""

    # Settings — already a singleton module-level object; wrap it
    from config.settings import Settings
    c.register_instance(Settings, settings)

    # Supabase client (optional — None when not configured)
    _register_supabase(c)


def _register_supabase(c: ServiceContainer) -> None:
    """Register Supabase client when credentials are present."""
    if not settings.supabase_enabled:
        logger.info("Supabase not configured — persistence layer disabled.")
        return

    def _factory():
        from supabase import create_client  # type: ignore[import-untyped]
        client = create_client(settings.supabase_url, settings.supabase_anon_key)
        logger.info("Supabase client created.")
        return client

    # We use a string tag because supabase.Client is not importable without
    # the package, and we want to avoid a hard dependency at registry load time.
    # Services resolve it via container.try_resolve(SupabaseClient) after
    # importing SupabaseClient from this module.
    try:
        from supabase import Client as SupabaseClient  # type: ignore[import-untyped]
        c.register(SupabaseClient, _factory, singleton=True)
        logger.info("Supabase client registered.")
    except ImportError:
        logger.warning("supabase-py not installed — Supabase unavailable.")


# ---------------------------------------------------------------------------
# Application services
# ---------------------------------------------------------------------------
# NOTE: Each lambda defers the import until first resolve().
#       This prevents circular imports and speeds up startup when optional
#       services are not needed on a particular code path.
# ---------------------------------------------------------------------------


def _register_services(c: ServiceContainer) -> None:
    """
    Register all application-layer services.

    Milestone mapping:
      M2 — Config / DI (this file)                  ← current milestone
      M3 — Memory system                             ← uncomment when ready
      M4 — AI abstraction + content engine           ← uncomment when ready
      M5 — Rendering engine                          ← uncomment when ready
      M6 — Facebook integration + scheduler          ← uncomment when ready
      M7 — Code Guardian                             ← uncomment when ready
    """

    # ------------------------------------------------------------------
    # Milestone 3 — Memory (placeholder until M3 is implemented)
    # ------------------------------------------------------------------
    # from core.memory import MemoryService
    # c.register(MemoryService, lambda: MemoryService(
    #     supabase=c.try_resolve(SupabaseClient),
    #     settings=settings,
    # ))

    # ------------------------------------------------------------------
    # Milestone 4 — AI Layer (placeholder until M4 is implemented)
    # ------------------------------------------------------------------
    # from core.ai.provider import AIProvider
    # from core.ai.gemini import GeminiProvider
    # c.register(AIProvider, lambda: GeminiProvider(api_key=settings.gemini_key))

    # ------------------------------------------------------------------
    # Milestone 6 — Facebook (placeholder until M6 is implemented)
    # ------------------------------------------------------------------
    # from core.facebook import FacebookService
    # c.register(FacebookService, lambda: FacebookService(
    #     token=settings.page_access_token,
    #     page_id=settings.page_id,
    # ))

    pass  # Remove when first real service is added
