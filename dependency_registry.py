"""
Kanyoza Systems AI Platform v10 — Dependency Registry.

This is the ONLY place where concrete implementations are wired to their
interfaces inside the service container.
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

    from repositories.workspace_repository import WorkspaceRepository
    from repositories.brand_repository import BrandRepository
    from repositories.ai_profile_repository import AIProfileRepository
    from repositories.social_account_repository import SocialAccountRepository
    from repositories.knowledge_repository import KnowledgeRepository

    try:
        from supabase import Client as SupabaseClient
    except ImportError:
        SupabaseClient = None

    def _build_repo(repo_class):
        try:
            client = c.resolve(SupabaseClient) if SupabaseClient and c.is_registered(SupabaseClient) else None
        except Exception:
            client = None
        return repo_class(supabase_client=client)

    c.register(WorkspaceRepository, lambda: _build_repo(WorkspaceRepository), singleton=True)
    c.register(BrandRepository, lambda: _build_repo(BrandRepository), singleton=True)
    c.register(AIProfileRepository, lambda: _build_repo(AIProfileRepository), singleton=True)
    c.register(SocialAccountRepository, lambda: _build_repo(SocialAccountRepository), singleton=True)
    c.register(KnowledgeRepository, lambda: _build_repo(KnowledgeRepository), singleton=True)

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
    
    from core.multi_tenant import WorkspaceManager
    from repositories.workspace_repository import WorkspaceRepository

    c.register(WorkspaceManager, lambda: WorkspaceManager(c.resolve(WorkspaceRepository)), singleton=True)
    logger.info("WorkspaceManager registered.")

    from scheduler.brand_scheduler import BrandSchedulerManager
    from repositories.brand_repository import BrandRepository
    from repositories.ai_profile_repository import AIProfileRepository
    
    def _build_brand_scheduler_manager():
        manager = BrandSchedulerManager(
            c.resolve(BrandRepository),
            c.resolve(AIProfileRepository)
        )
        return manager

    c.register(BrandSchedulerManager, _build_brand_scheduler_manager, singleton=True)
    logger.info("BrandSchedulerManager registered.")

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

    # ── Phase 5: Security & Reliability ──────────────────────────────
    from core.credential_vault import CredentialVault
    c.register(CredentialVault, lambda: CredentialVault(), singleton=True)
    logger.info("CredentialVault registered.")

    from core.api_tokens import APITokenManager

    def _build_token_manager():
        try:
            from supabase import Client as SupabaseClient
            client = c.resolve(SupabaseClient) if c.is_registered(SupabaseClient) else None
        except Exception:
            client = None
        return APITokenManager(supabase_client=client)

    c.register(APITokenManager, _build_token_manager, singleton=True)
    logger.info("APITokenManager registered.")

    from core.auth import AuditLogger

    def _build_audit_logger():
        try:
            from supabase import Client as SupabaseClient
            client = c.resolve(SupabaseClient) if c.is_registered(SupabaseClient) else None
        except Exception:
            client = None
        return AuditLogger(supabase_client=client)

    c.register(AuditLogger, _build_audit_logger, singleton=True)
    logger.info("AuditLogger registered.")

    from core.reliability import DeadLetterQueue, HealthChecker
    c.register(DeadLetterQueue, lambda: DeadLetterQueue(), singleton=True)

    def _build_health_checker():
        from plugins.base import PluginRegistry as _PluginRegistry
        registry = c.resolve(_PluginRegistry)
        return HealthChecker(registry)

    c.register(HealthChecker, _build_health_checker, singleton=True)
    logger.info("DeadLetterQueue and HealthChecker registered.")

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
            from repositories.social_account_repository import SocialAccountRepository
            social_repo = c.resolve(SocialAccountRepository)
            registry.register(FacebookPlugin(fb_svc, social_repo))
        except Exception as exc:
            logger.warning("FacebookPlugin could not register: %s", exc)

        # Twitter/X (OAuth 2.0)
        if settings.twitter_access_token:
            try:
                from plugins.twitter.plugin import TwitterPlugin

                twitter_plugin = TwitterPlugin(social_repo)
                twitter_plugin.configure(
                    access_token=settings.twitter_access_token,
                )
                registry.register(twitter_plugin)
                logger.info("TwitterPlugin registered.")
            except Exception as exc:
                logger.warning("TwitterPlugin could not register: %s", exc)

        # LinkedIn (optional)
        if settings.linkedin_access_token:
            try:
                from plugins.linkedin.plugin import LinkedInPlugin
                from services.linkedIn import linkedin as linkedin_svc

                linkedin_plugin = LinkedInPlugin(linkedin_svc, social_repo)
                linkedin_plugin.configure(
                    access_token=settings.linkedin_access_token,
                    person_urn=settings.linkedin_person_urn,
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

    def _build_post_workflow_factory():
        from services.content_engine import ContentEngine
        from services.prompt_builder import PromptBuilder
        from repositories.knowledge_repository import KnowledgeRepository
        from services.pipeline.pipeline import ContentGenerationPipeline
        
        content_engine = ContentEngine()
        prompt_builder = PromptBuilder()
        registry = c.resolve(PluginRegistry)
        analytics_repo = c.resolve(AnalyticsRepository)
        bus = c.resolve(EventBus)
        knowledge_repo = c.resolve(KnowledgeRepository)

        def factory(brand, ai_profile, category=None):
            return ContentGenerationPipeline(
                content_engine=content_engine,
                registry=registry,
                analytics_repo=analytics_repo,
                bus=bus,
                knowledge_repo=knowledge_repo,
                prompt_builder=prompt_builder,
                brand=brand,
                ai_profile=ai_profile,
                category=category,
            )
        return factory

    # Register both names for full backward compatibility
    c.register("PostPublishWorkflowFactory", _build_post_workflow_factory, singleton=True)
    c.register("ContentGenerationPipelineFactory", _build_post_workflow_factory, singleton=True)
    logger.info("ContentGenerationPipelineFactory and legacy PostPublishWorkflowFactory registered.")

    # Register PostPublishWorkflow as a resolvable type (transient — fresh instance per
    # resolve) so lifecycle.py can wire it directly into the scheduler.
    # Uses Brand.create_default / AIProfile.create_default for the legacy single-tenant path.
    def _build_default_post_workflow():
        from models.brand import Brand
        from models.ai_profile import AIProfile
        from services.content_engine import ContentEngine
        brand       = Brand.create_default("default")
        ai_profile  = AIProfile.create_default("default")
        registry    = c.resolve(PluginRegistry)
        an_repo     = c.resolve(AnalyticsRepository)
        bus         = c.resolve(EventBus)
        return PostPublishWorkflow(
            content_engine=ContentEngine(),
            registry=registry,
            analytics_repo=an_repo,
            bus=bus,
            brand=brand,
            ai_profile=ai_profile,
        )

    c.register(PostPublishWorkflow, _build_default_post_workflow, singleton=False)
    logger.info("PostPublishWorkflow registered as resolvable transient binding.")
