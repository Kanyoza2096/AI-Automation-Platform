"""
Kanyoza Systems AI Platform v13.0 — Dependency Registry.

THE single source of truth for all DI bindings. Every concrete
implementation is wired to its interface here.

Architecture §70 — Backend operations use SERVICE_ROLE key.
SocialAccountRepository, JobService, and all backend writes
resolve SupabaseClient with service_role credentials.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from config.settings import settings
from service_container import ServiceContainer, set_global_container

logger = logging.getLogger("kanyoza.registry")


class ServiceCriticality(Enum):
    CRITICAL = "critical"
    REQUIRED = "required"
    OPTIONAL = "optional"


@dataclass
class BindingMetadata:
    interface_name: str
    criticality: ServiceCriticality
    has_fallback: bool = False
    description: str = ""


def build_container() -> ServiceContainer:
    container = ServiceContainer()
    set_global_container(container)
    _bindings: list[BindingMetadata] = []

    _register_settings(container, _bindings)
    _register_infrastructure(container, _bindings)
    _register_repositories(container, _bindings)
    _register_ai_layer(container, _bindings)
    _register_business_services(container, _bindings)
    _register_connector_plugins(container, _bindings)
    _register_workflows(container, _bindings)
    _register_observability(container, _bindings)

    _log_registration_summary(container, _bindings)
    return container



def _register_settings(c: ServiceContainer, bindings: list[BindingMetadata]) -> None:
    from config.settings import Settings
    c.register_instance(Settings, settings)
    bindings.append(BindingMetadata("Settings", ServiceCriticality.CRITICAL, description="Application configuration"))


def _register_infrastructure(c: ServiceContainer, bindings: list[BindingMetadata]) -> None:
    from core.event_bus import EventBus
    c.register(EventBus, EventBus, singleton=True)
    bindings.append(BindingMetadata("EventBus", ServiceCriticality.CRITICAL, description="In-process pub/sub event system"))

    _register_redis(c, bindings)
    _register_supabase(c, bindings)

    from workers.task_queue import TaskQueue
    c.register(TaskQueue, lambda: _build_task_queue(), singleton=True)
    bindings.append(BindingMetadata("TaskQueue", ServiceCriticality.REQUIRED, description="Background task worker pool"))

    from domain.security.replay_guard import ReplayGuard
    c.register(ReplayGuard, lambda: ReplayGuard(ttl_seconds=300), singleton=True)
    bindings.append(BindingMetadata("ReplayGuard", ServiceCriticality.CRITICAL, description="Nonce-based replay attack prevention"))


def _register_redis(c: ServiceContainer, bindings: list[BindingMetadata]) -> None:
    url = getattr(settings, 'upstash_redis_rest_url', None)
    if not url:
        logger.info("Redis not configured — using in-memory fallback.")
        bindings.append(BindingMetadata("UpstashRedisClient", ServiceCriticality.OPTIONAL, has_fallback=True, description="Redis client (not configured)"))
        return

    from core.redis_client import UpstashRedisClient, RedisConfig
    config = RedisConfig(url=url, token=getattr(settings, 'upstash_redis_rest_token', ''), enabled=True)

    def _factory() -> UpstashRedisClient:
        return UpstashRedisClient(config)

    c.register(UpstashRedisClient, _factory, singleton=True)
    bindings.append(BindingMetadata("UpstashRedisClient", ServiceCriticality.OPTIONAL, has_fallback=True, description="Redis client"))


def _register_supabase(c: ServiceContainer, bindings: list[BindingMetadata]) -> None:
    """Register Supabase client.

    ARCHITECTURE §70: Backend operations use SERVICE_ROLE key.
    All repositories, JobService, and backend writes resolve this client.
    The service_role key bypasses RLS — REQUIRED for production.
    """
    if not settings.supabase_enabled:
        logger.info("Supabase not configured.")
        bindings.append(BindingMetadata("SupabaseClient", ServiceCriticality.OPTIONAL, has_fallback=True, description="Supabase client (not configured)"))
        return

    try:
        from supabase import Client as SupabaseClient
    except ImportError:
        logger.warning("supabase-py not installed.")
        bindings.append(BindingMetadata("SupabaseClient", ServiceCriticality.OPTIONAL, has_fallback=True, description="Supabase client (not installed)"))
        return

    def _factory() -> SupabaseClient:
        from supabase import create_client
        # PREFER service_role key for backend writes
        # FALL BACK to anon key only in development
        key = settings.supabase_service_role_key or settings.supabase_anon_key
        if settings.supabase_service_role_key:
            logger.info("Supabase client created with SERVICE_ROLE key (backend writes enabled).")
        else:
            logger.warning("Supabase client created with ANON key — backend writes WILL FAIL on RLS-protected tables.")
        return create_client(settings.supabase_url, key)

    c.register(SupabaseClient, _factory, singleton=True)
    bindings.append(BindingMetadata("SupabaseClient", ServiceCriticality.REQUIRED, has_fallback=True, description="Supabase client (service_role preferred)"))


def _build_task_queue():
    from workers.task_queue import TaskQueue
    q = TaskQueue(workers=3)
    q.start()
    logger.info("TaskQueue started with 3 workers.")
    return q


def _register_repositories(c: ServiceContainer, bindings: list[BindingMetadata]) -> None:
    repository_classes = [
        ("ProfileRepository", "repositories.profile_repository", "ProfileRepository"),
        ("ConversationRepository", "repositories.conversation_repository", "ConversationRepository"),
        ("AnalyticsRepository", "repositories.analytics_repository", "AnalyticsRepository"),
        ("ScheduledPostRepository", "repositories.scheduled_post_repository", "ScheduledPostRepository"),
        ("WorkspaceRepository", "repositories.workspace_repository", "WorkspaceRepository"),
        ("WorkspaceMemberRepository", "repositories.workspace_member_repository", "WorkspaceMemberRepository"),
        ("BrandRepository", "repositories.brand_repository", "BrandRepository"),
        ("AIProfileRepository", "repositories.ai_profile_repository", "AIProfileRepository"),
        ("KnowledgeRepository", "repositories.knowledge_repository", "KnowledgeRepository"),
        ("SocialAccountRepository", "repositories.social_account_repository", "SocialAccountRepository"),
    ]
    for name, module_path, class_name in repository_classes:
        _register_repository(c, bindings, name, module_path, class_name)
    logger.info("Repositories registered: %d", len(repository_classes))


def _register_repository(c: ServiceContainer, bindings: list[BindingMetadata], name: str, module_path: str, class_name: str) -> None:
    module = __import__(module_path, fromlist=[class_name])
    repo_class = getattr(module, class_name)

    def _factory(repo_cls=repo_class):
        client = _resolve_supabase_safely(c)
        try:
            return repo_cls(supabase_client=client)
        except TypeError:
            return repo_cls()

    c.register(repo_class, _factory, singleton=True)
    bindings.append(BindingMetadata(name, ServiceCriticality.REQUIRED, has_fallback=True, description=f"Repository: {name}"))


def _register_ai_layer(c: ServiceContainer, bindings: list[BindingMetadata]) -> None:
    from ai.orchestrator import AIOrchestrator

    def _build_orchestrator():
        from ai.gemini_provider import GeminiProvider
        from ai.huggingface_provider import HuggingFaceProvider
        from services.gemini import gemini as gemini_svc
        providers = [GeminiProvider(gemini_svc)]
        try:
            from services.huggingface import HuggingFaceService
            providers.append(HuggingFaceProvider(HuggingFaceService()))
        except Exception as exc:
            logger.info("HuggingFace unavailable — continuing with Gemini only: %s", exc)
        return AIOrchestrator(providers)

    c.register(AIOrchestrator, _build_orchestrator, singleton=True)
    bindings.append(BindingMetadata("AIOrchestrator", ServiceCriticality.REQUIRED, description="Multi-provider AI orchestration"))


def _register_business_services(c: ServiceContainer, bindings: list[BindingMetadata]) -> None:
    from core.event_bus import EventBus
    from core.multi_tenant import WorkspaceManager
    from repositories.workspace_repository import WorkspaceRepository

    c.register(WorkspaceManager, lambda: WorkspaceManager(c.resolve(WorkspaceRepository)), singleton=True)
    bindings.append(BindingMetadata("WorkspaceManager", ServiceCriticality.REQUIRED, description="Multi-tenant workspace management"))

    from services.job_service import JobService

    def _build_job_service():
        client = _resolve_supabase_safely(c)
        if client is None:
            try:
                from services.supabase import get_service_client_from_settings
                client = get_service_client_from_settings()
            except Exception:
                pass
        svc = JobService(supabase_client=client)
        logger.info("JobService created (persistent=%s).", client is not None)
        return svc

    c.register(JobService, _build_job_service, singleton=True)
    bindings.append(BindingMetadata("JobService", ServiceCriticality.REQUIRED, has_fallback=True, description="Job lifecycle management"))

    from services.content_validator import ContentValidator
    c.register(ContentValidator, ContentValidator, singleton=True)
    bindings.append(BindingMetadata("ContentValidator", ServiceCriticality.OPTIONAL, description="AI content validation"))

    from scheduler.brand_scheduler import BrandSchedulerManager
    from repositories.brand_repository import BrandRepository
    from repositories.ai_profile_repository import AIProfileRepository

    def _build_brand_scheduler():
        return BrandSchedulerManager(c.resolve(BrandRepository), c.resolve(AIProfileRepository))

    c.register(BrandSchedulerManager, _build_brand_scheduler, singleton=True)
    bindings.append(BindingMetadata("BrandSchedulerManager", ServiceCriticality.REQUIRED, description="Per-brand post scheduling"))

    from domain.analytics.event_recorder import EventRecorder
    from repositories.analytics_repository import AnalyticsRepository

    def _build_recorder():
        return EventRecorder(repo=c.resolve(AnalyticsRepository), bus=c.resolve(EventBus))

    c.register(EventRecorder, _build_recorder, singleton=True)
    bindings.append(BindingMetadata("EventRecorder", ServiceCriticality.OPTIONAL, description="Analytics event recording"))

    from services.browser_manager import BrowserManager, browser_manager as _bm
    c.register_instance(BrowserManager, _bm)
    bindings.append(BindingMetadata("BrowserManager", ServiceCriticality.OPTIONAL, description="Headless browser"))

    from services.render_cache import RenderCache, render_cache as _rc
    c.register_instance(RenderCache, _rc)
    bindings.append(BindingMetadata("RenderCache", ServiceCriticality.OPTIONAL, description="Render cache"))

    if settings.guardian_configured:
        _register_guardian(c, bindings)
    else:
        logger.info("Code Guardian not configured — skipping.")
        bindings.append(BindingMetadata("GuardianService", ServiceCriticality.OPTIONAL, description="Code Guardian (not configured)"))


def _register_guardian(c: ServiceContainer, bindings: list[BindingMetadata]) -> None:
    from services.guardian_service import GuardianService
    from core.event_bus import EventBus

    def _build_guardian():
        from guardian.github_client import GitHubClient
        from guardian.scanner import CodeGuardianScanner
        from guardian.auto_fix import AutoFixEngine
        from services.facebook import facebook as fb_svc
        return GuardianService(
            github_client=GitHubClient(settings.github_username, settings.github_token),
            scanner=CodeGuardianScanner(),
            auto_fix_engine=AutoFixEngine(),
            event_bus=c.resolve(EventBus),
            facebook_svc=fb_svc,
        )

    c.register(GuardianService, _build_guardian, singleton=True)
    bindings.append(BindingMetadata("GuardianService", ServiceCriticality.OPTIONAL, description="Code scanning engine"))


def _register_connector_plugins(c: ServiceContainer, bindings: list[BindingMetadata]) -> None:
    """Register connector plugins using the ORIGINAL working pattern.

    Single factory, no circular resolve. Plugins are created
    when PluginRegistry is first resolved by the lifecycle boot.
    """
    from plugins.base import PluginRegistry
    from repositories.social_account_repository import SocialAccountRepository

    def _build_registry_with_plugins():
        registry = PluginRegistry()

        try:
            social_repo = c.resolve(SocialAccountRepository)
        except Exception:
            social_repo = None

        # Facebook
        try:
            from plugins.facebook.plugin import FacebookPlugin
            from services.facebook import facebook as fb_svc
            registry.register(FacebookPlugin(fb_svc, social_repo))
            logger.info("FacebookPlugin registered.")
        except Exception as exc:
            logger.error("FacebookPlugin could not register: %s", exc)

        # Twitter
        if settings.twitter_access_token:
            try:
                from plugins.twitter.plugin import TwitterPlugin
                plugin = TwitterPlugin(social_repo)
                plugin.configure(
                    access_token=settings.twitter_access_token,
                    refresh_token=settings.twitter_refresh_token,
                    client_id=settings.twitter_api_key,
                    client_secret=settings.twitter_api_secret,
                )
                registry.register(plugin)
                logger.info("TwitterPlugin registered.")
            except Exception as exc:
                logger.error("TwitterPlugin could not register: %s", exc)

        # LinkedIn
        if settings.linkedin_access_token:
            try:
                from plugins.linkedin.plugin import LinkedInPlugin
                from services.linkedIn import linkedin as linkedin_svc
                plugin = LinkedInPlugin(linkedin_svc, social_repo)
                plugin.configure(
                    access_token=settings.linkedin_access_token,
                    person_urn=settings.linkedin_person_urn,
                )
                registry.register(plugin)
                logger.info("LinkedInPlugin registered.")
            except Exception as exc:
                logger.error("LinkedInPlugin could not register: %s", exc)

        logger.info("PluginRegistry built with %d plugin(s): %s", len(registry.names), registry.names)
        registry.start_all()
        return registry

    c.register(PluginRegistry, _build_registry_with_plugins, singleton=True)
    bindings.append(BindingMetadata("PluginRegistry", ServiceCriticality.REQUIRED, has_fallback=True, description="Social media connector plugins"))


def _register_workflows(c: ServiceContainer, bindings: list[BindingMetadata]) -> None:
    from core.event_bus import EventBus
    from repositories.analytics_repository import AnalyticsRepository
    from repositories.knowledge_repository import KnowledgeRepository

    def _build_pipeline_factory():
        from services.content_engine import ContentEngine
        from services.prompt_builder import PromptBuilder
        from services.pipeline.pipeline import ContentGenerationPipeline

        content_engine = ContentEngine(container=c)
        prompt_builder = PromptBuilder()
        analytics_repo = c.resolve(AnalyticsRepository)
        bus = c.resolve(EventBus)
        knowledge_repo = c.resolve(KnowledgeRepository)

        content_validator = None
        try:
            from services.content_validator import ContentValidator
            content_validator = c.resolve(ContentValidator)
        except Exception:
            pass

        def factory(brand, ai_profile, category=None):
            return ContentGenerationPipeline(
                content_engine=content_engine, analytics_repo=analytics_repo,
                bus=bus, knowledge_repo=knowledge_repo, prompt_builder=prompt_builder,
                brand=brand, ai_profile=ai_profile, container=c,
                category=category, content_validator=content_validator,
            )
        return factory

    factory = _build_pipeline_factory()
    c.register("ContentGenerationPipelineFactory", lambda: factory, singleton=True)
    c.register("PostPublishWorkflowFactory", lambda: factory, singleton=True)
    bindings.append(BindingMetadata("ContentGenerationPipelineFactory", ServiceCriticality.REQUIRED, description="Content pipeline factory"))


def _register_observability(c: ServiceContainer, bindings: list[BindingMetadata]) -> None:
    from core.credential_vault import CredentialVault
    c.register(CredentialVault, CredentialVault, singleton=True)
    bindings.append(BindingMetadata("CredentialVault", ServiceCriticality.CRITICAL, description="Encrypted credential storage"))

    from core.rate_limiter import RateLimiter
    def _build_rate_limiter():
        return RateLimiter(redis=_resolve_redis_safely(c))
    c.register(RateLimiter, _build_rate_limiter, singleton=True)
    bindings.append(BindingMetadata("RateLimiter", ServiceCriticality.OPTIONAL, has_fallback=True, description="API rate limiting"))

    from core.api_tokens import APITokenManager
    def _build_token_manager():
        return APITokenManager(supabase_client=_resolve_supabase_safely(c))
    c.register(APITokenManager, _build_token_manager, singleton=True)
    bindings.append(BindingMetadata("APITokenManager", ServiceCriticality.REQUIRED, has_fallback=True, description="API token management"))

    from core.auth import AuditLogger
    def _build_audit_logger():
        return AuditLogger(supabase_client=_resolve_supabase_safely(c))
    c.register(AuditLogger, _build_audit_logger, singleton=True)
    bindings.append(BindingMetadata("AuditLogger", ServiceCriticality.REQUIRED, has_fallback=True, description="Security audit logging"))

    from core.reliability import DeadLetterQueue, HealthChecker
    c.register(DeadLetterQueue, DeadLetterQueue, singleton=True)
    bindings.append(BindingMetadata("DeadLetterQueue", ServiceCriticality.OPTIONAL, description="Failed job persistence"))

    def _build_health_checker():
        from plugins.base import PluginRegistry
        registry = c.try_resolve(PluginRegistry)
        return HealthChecker(registry) if registry else HealthChecker(None)
    c.register(HealthChecker, _build_health_checker, singleton=True)
    bindings.append(BindingMetadata("HealthChecker", ServiceCriticality.OPTIONAL, description="Plugin health checks"))


def _resolve_supabase_safely(c: ServiceContainer):
    try:
        from supabase import Client as SupabaseClient
        if c.is_registered(SupabaseClient):
            return c.resolve(SupabaseClient)
    except Exception as exc:
        logger.debug("Supabase resolution failed: %s", exc)
    return None


def _resolve_redis_safely(c: ServiceContainer):
    try:
        from core.redis_client import UpstashRedisClient
        if c.is_registered(UpstashRedisClient):
            return c.resolve(UpstashRedisClient)
    except Exception:
        pass
    return None


def _log_registration_summary(c: ServiceContainer, bindings: list[BindingMetadata]) -> None:
    critical = [b for b in bindings if b.criticality == ServiceCriticality.CRITICAL]
    required = [b for b in bindings if b.criticality == ServiceCriticality.REQUIRED]
    optional = [b for b in bindings if b.criticality == ServiceCriticality.OPTIONAL]
    logger.info("Service container built — %d bindings (CRITICAL=%d, REQUIRED=%d, OPTIONAL=%d, registered=%d)",
                len(bindings), len(critical), len(required), len(optional), len(c.registered_types()))
