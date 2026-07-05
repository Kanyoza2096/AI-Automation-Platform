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
  3. Add a register() call in the appropriate section below.
"""

from __future__ import annotations

import logging

from config.settings import settings
from service_container import ServiceContainer

logger = logging.getLogger("kanyoza.registry")


def build_container() -> ServiceContainer:
    """Construct and populate the application service container."""
    container = ServiceContainer()
    _register_infrastructure(container)
    _register_repositories(container)
    _register_ai_layer(container)
    _register_services(container)
    _register_plugins(container)
    _register_workflows(container)
    logger.info(
        "Service container built — %d bindings registered.",
        len(container.registered_types()),
    )
    return container


# ---------------------------------------------------------------------------
# Infrastructure
# ---------------------------------------------------------------------------

def _register_infrastructure(c: ServiceContainer) -> None:
    from config.settings import Settings
    c.register_instance(Settings, settings)

    from core.event_bus import EventBus
    c.register(EventBus, lambda: _build_event_bus(), singleton=True)

    from workers.task_queue import TaskQueue
    c.register(TaskQueue, lambda: _build_task_queue(), singleton=True)

    from plugins.base import PluginRegistry
    c.register(PluginRegistry, lambda: _build_plugin_registry(), singleton=True)

    from domain.security.replay_guard import ReplayGuard
    c.register(ReplayGuard, lambda: ReplayGuard(ttl_seconds=300), singleton=True)

    _register_supabase(c)


def _build_event_bus():
    from core.event_bus import EventBus
    bus = EventBus()
    logger.info("EventBus created.")
    return bus


def _build_task_queue():
    from workers.task_queue import TaskQueue
    q = TaskQueue(workers=3)
    q.start()
    logger.info("TaskQueue started with 3 workers.")
    return q


def _build_plugin_registry():
    from plugins.base import PluginRegistry
    registry = PluginRegistry()
    logger.info("PluginRegistry created.")
    return registry


def _register_supabase(c: ServiceContainer) -> None:
    if not settings.supabase_enabled:
        logger.info("Supabase not configured — persistence layer disabled.")
        return

    def _factory():
        from supabase import create_client
        client = create_client(settings.supabase_url, settings.supabase_anon_key)
        logger.info("Supabase client created.")
        return client

    try:
        from supabase import Client as SupabaseClient
        c.register(SupabaseClient, _factory, singleton=True)
        logger.info("Supabase client registered.")
    except ImportError:
        logger.warning("supabase-py not installed — Supabase unavailable.")


# ---------------------------------------------------------------------------
# Repositories
# ---------------------------------------------------------------------------

def _register_repositories(c: ServiceContainer) -> None:
    from repositories.profile_repository import ProfileRepository
    from repositories.conversation_repository import ConversationRepository
    from repositories.analytics_repository import AnalyticsRepository
    from repositories.scheduled_post_repository import ScheduledPostRepository

    c.register(ProfileRepository, ProfileRepository, singleton=True)
    c.register(ConversationRepository, ConversationRepository, singleton=True)
    c.register(AnalyticsRepository, AnalyticsRepository, singleton=True)
    c.register(ScheduledPostRepository, ScheduledPostRepository, singleton=True)

    logger.info("Repositories registered.")


# ---------------------------------------------------------------------------
# AI Layer
# ---------------------------------------------------------------------------

def _register_ai_layer(c: ServiceContainer) -> None:
    from ai.orchestrator import AIOrchestrator

    def _build_orchestrator():
        from ai.gemini_provider import GeminiProvider
        from ai.huggingface_provider import HuggingFaceProvider
        from services.gemini import gemini as gemini_svc
        from services.huggingface import HuggingFaceService

        providers = [GeminiProvider(gemini_svc)]
        try:
            hf = HuggingFaceService()
            providers.append(HuggingFaceProvider(hf))
        except Exception as exc:
            logger.debug("HuggingFace provider unavailable: %s", exc)

        orchestrator = AIOrchestrator(providers)
        logger.info("AIOrchestrator created with %d provider(s).", len(providers))
        return orchestrator

    c.register(AIOrchestrator, _build_orchestrator, singleton=True)


# ---------------------------------------------------------------------------
# Application services
# ---------------------------------------------------------------------------

def _register_services(c: ServiceContainer) -> None:
    from domain.analytics.event_recorder import EventRecorder
    from core.event_bus import EventBus
    from repositories.analytics_repository import AnalyticsRepository

    def _build_recorder():
        bus = c.resolve(EventBus)
        repo = c.resolve(AnalyticsRepository)
        recorder = EventRecorder(repo=repo, bus=bus)
        recorder.start()
        logger.info("EventRecorder started.")
        return recorder

    c.register(EventRecorder, _build_recorder, singleton=True)

    from services.browser_manager import BrowserManager, browser_manager as _bm
    c.register_instance(BrowserManager, _bm)
    logger.info("BrowserManager registered (module singleton).")

    from services.render_cache import RenderCache, render_cache as _rc
    c.register_instance(RenderCache, _rc)
    logger.info("RenderCache registered (module singleton).")

    if settings.guardian_configured:
        from services.guardian_service import GuardianService

        def _build_guardian():
            from guardian.github_client import GitHubClient
            from guardian.scanner import CodeGuardianScanner
            from guardian.auto_fix import AutoFixEngine
            from services.facebook import facebook as fb_svc

            bus = c.resolve(EventBus)
            svc = GuardianService(
                github_client=GitHubClient(settings.github_username, settings.github_token),
                scanner=CodeGuardianScanner(),
                auto_fix_engine=AutoFixEngine(),
                event_bus=bus,
                facebook_svc=fb_svc,
            )
            logger.info("GuardianService created.")
            return svc

        c.register(GuardianService, _build_guardian, singleton=True)
        logger.info("GuardianService registered.")


# ---------------------------------------------------------------------------
# Plugins
# ---------------------------------------------------------------------------

def _register_plugins(c: ServiceContainer) -> None:
    from plugins.base import PluginRegistry

    def _build_registry_with_plugins():
        registry = PluginRegistry()

        # Facebook (always primary)
        try:
            from plugins.facebook.plugin import FacebookPlugin
            from services.facebook import facebook as fb_svc
            registry.register(FacebookPlugin(fb_svc))
        except Exception as exc:
            logger.warning("FacebookPlugin could not register: %s", exc)

        # Twitter/X (OAuth 2.0)
        if settings.twitter_access_token:
            try:
                from plugins.twitter.plugin import TwitterPlugin
                from plugins.base import PluginConfig

                twitter_plugin = TwitterPlugin(
                    PluginConfig(
                        name="twitter",
                        settings={
                            "access_token": settings.twitter_access_token,
                            "refresh_token": settings.twitter_refresh_token,
                        }
                    )
                )
                registry.register(twitter_plugin)
                logger.info("TwitterPlugin registered.")
            except Exception as exc:
                logger.warning("TwitterPlugin could not register: %s", exc)

        # LinkedIn (optional)
        if settings.linkedin_access_token:
            try:
                from plugins.linkedin.plugin import LinkedInPlugin
                from plugins.base import PluginConfig

                linkedin_plugin = LinkedInPlugin(
                    PluginConfig(
                        name="linkedin",
                        settings={
                            "access_token": settings.linkedin_access_token,
                            "person_urn": settings.linkedin_person_urn,
                        }
                    )
                )
                registry.register(linkedin_plugin)
                logger.info("LinkedInPlugin registered.")
            except Exception as exc:
                logger.warning("LinkedInPlugin could not register: %s", exc)

        registry.start_all()
        return registry

    c.register(PluginRegistry, _build_registry_with_plugins, singleton=True)


# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------

def _register_workflows(c: ServiceContainer) -> None:
    from workflows.post_workflow import PostPublishWorkflow
    from plugins.base import PluginRegistry
    from repositories.analytics_repository import AnalyticsRepository
    from core.event_bus import EventBus

    def _build_post_workflow():
        from services.content_engine import content_engine
        registry = c.resolve(PluginRegistry)
        analytics_repo = c.resolve(AnalyticsRepository)
        bus = c.resolve(EventBus)
        return PostPublishWorkflow(
            content_engine=content_engine,
            registry=registry,
            analytics_repo=analytics_repo,
            bus=bus,
        )

    c.register(PostPublishWorkflow, _build_post_workflow, singleton=False)
    logger.info("PostPublishWorkflow registered.")
