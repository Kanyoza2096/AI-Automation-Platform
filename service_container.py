"""
Kanyoza Systems AI Platform v10 — Dependency Registry.

All service registrations for the application's DI container.
Called once from bootstrap.py during application startup.

Design principles:
  - Every service is registered against its interface (Protocol/ABC).
  - Factories capture dependencies via closure or container.resolve().
  - Singletons are thread-safe (handled by ServiceContainer).
  - Registration order matters: foundational services first, then dependent services.
  - Graceful fallback: optional services (Redis) degrade, never crash startup.
"""

from __future__ import annotations

import logging
from typing import Callable

from core.service_container import ServiceContainer
from config.settings import settings

logger = logging.getLogger(__name__)


# ============================================================================
# Factory Functions
# ============================================================================

def _create_redis_client():
    """Create Upstash Redis client — self-tests on creation."""
    from core.redis_client import UpstashRedisClient
    return UpstashRedisClient()


def _create_rate_limiter(container: ServiceContainer):
    """Rate limiter depends on Redis."""
    from core.interfaces.redis_client import IRedisClient
    from core.rate_limiter import RateLimiter
    redis = container.resolve(IRedisClient)
    return RateLimiter(redis)


def _create_prompt_cache(container: ServiceContainer):
    """Prompt cache with Redis backend and in-memory fallback."""
    from core.interfaces.redis_client import IRedisClient
    from services.prompt_cache import RedisPromptCache
    redis = container.resolve(IRedisClient)
    return RedisPromptCache(redis)


def _create_task_queue(container: ServiceContainer):
    """Task queue using Redis lists."""
    from core.interfaces.redis_client import IRedisClient
    from core.task_queue import RedisTaskQueue
    redis = container.resolve(IRedisClient)
    return RedisTaskQueue(redis)


def _create_gemini_service():
    """Gemini AI service — stateless, can be recreated."""
    from services.gemini_service import GeminiService
    return GeminiService(api_key=settings.gemini_key)


def _create_content_engine(container: ServiceContainer):
    """Content engine depends on Gemini and prompt cache."""
    from services.gemini_service import GeminiService
    from services.prompt_cache import RedisPromptCache
    from services.content_engine import ContentEngine
    
    gemini = container.resolve(GeminiService)
    prompt_cache = container.resolve(RedisPromptCache)
    return ContentEngine(gemini=gemini, prompt_cache=prompt_cache)


def _create_facebook_service():
    """Facebook Graph API service."""
    from services.facebook import FacebookService
    return FacebookService(
        page_access_token=settings.page_access_token,
        page_id=settings.page_id,
        app_secret=settings.app_secret,
        verify_token=settings.verify_token,
    )


def _create_twitter_service():
    """Twitter API v2 service."""
    from services.twitter import TwitterService
    return TwitterService(
        api_key=settings.twitter_api_key,
        api_secret=settings.twitter_api_secret,
        access_token=settings.twitter_access_token,
        refresh_token=settings.twitter_refresh_token,
    )


def _create_linkedin_service():
    """LinkedIn API service."""
    from services.linkedin import LinkedInService
    return LinkedInService()


def _create_supabase_client():
    """Supabase client for database operations."""
    from supabase import create_client
    return create_client(
        settings.supabase_url,
        settings.supabase_service_role_key,
    )


def _create_workspace_repository(container: ServiceContainer):
    """Workspace repository — depends on Supabase."""
    from repositories.workspace_repository import WorkspaceRepository
    supabase = container.resolve("supabase_client")
    return WorkspaceRepository(supabase)


def _create_brand_repository(container: ServiceContainer):
    """Brand repository — depends on Supabase."""
    from repositories.brand_repository import BrandRepository
    supabase = container.resolve("supabase_client")
    return BrandRepository(supabase)


def _create_ai_profile_repository(container: ServiceContainer):
    """AI Profile repository — depends on Supabase."""
    from repositories.ai_profile_repository import AIProfileRepository
    supabase = container.resolve("supabase_client")
    return AIProfileRepository(supabase)


def _create_social_account_repository(container: ServiceContainer):
    """Social account repository — depends on Supabase and credential vault."""
    from repositories.social_account_repository import SocialAccountRepository
    from core.credential_vault import CredentialVault
    
    supabase = container.resolve("supabase_client")
    vault = container.resolve(CredentialVault)
    return SocialAccountRepository(supabase, vault)


def _create_knowledge_repository(container: ServiceContainer):
    """Knowledge base repository — depends on Supabase."""
    from repositories.knowledge_repository import KnowledgeRepository
    supabase = container.resolve("supabase_client")
    return KnowledgeRepository(supabase)


def _create_credential_vault():
    """Credential vault for encrypting social tokens."""
    from core.credential_vault import CredentialVault
    return CredentialVault.from_settings(settings)


def _create_workspace_context_manager():
    """Workspace context — thread-safe context holder."""
    from core.workspace_context import WorkspaceContextManager
    return WorkspaceContextManager()


def _create_connector_factory(container: ServiceContainer):
    """Connector factory — creates social platform connectors."""
    from services.connectors.connector_factory import ConnectorFactory
    from repositories.social_account_repository import SocialAccountRepository
    
    social_repo = container.resolve(SocialAccountRepository)
    return ConnectorFactory(social_repo)


def _create_event_bus():
    """Event bus for pub/sub within the application."""
    from core.event_bus import EventBus
    return EventBus()


def _create_socket_server(container: ServiceContainer):
    """SocketIO server for real-time dashboard."""
    from core.socket_server import SocketServer
    event_bus = container.resolve(EventBus)
    return SocketServer(event_bus)


def _create_browser_manager():
    """Playwright browser manager for card rendering."""
    from core.browser_manager import BrowserManager
    return BrowserManager(max_pages=settings.browser_max_pages)


def _create_card_renderer(container: ServiceContainer):
    """Card renderer — depends on browser manager."""
    from core.browser_manager import BrowserManager
    from services.card_renderer import CardRenderer
    
    browser = container.resolve(BrowserManager)
    return CardRenderer(browser)


def _create_post_scheduler(container: ServiceContainer):
    """Brand scheduler manager — depends on multiple services."""
    from scheduler.post_scheduler import BrandSchedulerManager
    from repositories.brand_repository import BrandRepository
    from services.content_engine import ContentEngine
    from services.connectors.connector_factory import ConnectorFactory
    
    brand_repo = container.resolve(BrandRepository)
    content_engine = container.resolve(ContentEngine)
    connector_factory = container.resolve(ConnectorFactory)
    
    return BrandSchedulerManager(
        brand_repository=brand_repo,
        content_engine=content_engine,
        connector_factory=connector_factory,
    )


def _create_pipeline(container: ServiceContainer):
    """10-stage content pipeline."""
    from services.pipeline.pipeline import ContentPipeline
    from services.content_engine import ContentEngine
    from services.card_renderer import CardRenderer
    from repositories.knowledge_repository import KnowledgeRepository
    
    content_engine = container.resolve(ContentEngine)
    card_renderer = container.resolve(CardRenderer)
    knowledge_repo = container.resolve(KnowledgeRepository)
    
    return ContentPipeline(
        content_engine=content_engine,
        card_renderer=card_renderer,
        knowledge_repo=knowledge_repo,
    )


def _create_audit_logger(container: ServiceContainer):
    """Security audit logger."""
    from core.audit_logger import AuditLogger
    from core.event_bus import EventBus
    
    supabase = container.resolve("supabase_client")
    event_bus = container.resolve(EventBus)
    return AuditLogger(supabase, event_bus)


def _create_api_token_manager(container: ServiceContainer):
    """API token manager for authentication."""
    from core.api_tokens import APITokenManager
    
    supabase = container.resolve("supabase_client")
    return APITokenManager(supabase)


# ============================================================================
# Registration
# ============================================================================


def register_all_dependencies(container: ServiceContainer) -> ServiceContainer:
    """
    Register all application dependencies.
    
    Called once at startup from bootstrap.py.
    Registration order follows dependency chain:
    foundation → repositories → services → orchestrators.
    """

    # ------------------------------------------------------------------
    # Foundation Layer — No Dependencies
    # ------------------------------------------------------------------

    # Settings (already imported at module level)
    container.register_instance("settings", settings)

    # Redis Client (Upstash REST API — self-tests, never crashes)
    from core.interfaces.redis_client import IRedisClient
    container.register(
        IRedisClient,
        _create_redis_client,
        singleton=True,
    )

    # Event Bus (in-process pub/sub)
    from core.event_bus import EventBus
    container.register(
        EventBus,
        _create_event_bus,
        singleton=True,
    )

    # Workspace Context Manager (thread-safe)
    from core.workspace_context import WorkspaceContextManager
    container.register(
        WorkspaceContextManager,
        _create_workspace_context_manager,
        singleton=True,
    )

    # Credential Vault (encryption for tokens)
    from core.credential_vault import CredentialVault
    container.register(
        CredentialVault,
        _create_credential_vault,
        singleton=True,
    )

    # Browser Manager (Playwright pool)
    from core.browser_manager import BrowserManager
    container.register(
        BrowserManager,
        _create_browser_manager,
        singleton=True,
    )

    # ------------------------------------------------------------------
    # Infrastructure Layer — External Services
    # ------------------------------------------------------------------

    # Supabase Client
    container.register(
        "supabase_client",
        _create_supabase_client,
        singleton=True,
    )

    # Gemini AI Service
    from services.gemini_service import GeminiService
    container.register(
        GeminiService,
        _create_gemini_service,
        singleton=True,
    )

    # Facebook Service (legacy — migrate to FacebookConnector)
    from services.facebook import FacebookService
    container.register(
        FacebookService,
        _create_facebook_service,
        singleton=True,
    )

    # Twitter Service (legacy — migrate to TwitterConnector)
    from services.twitter import TwitterService
    container.register(
        TwitterService,
        _create_twitter_service,
        singleton=True,
    )

    # LinkedIn Service (legacy — migrate to LinkedInConnector)
    from services.linkedin import LinkedInService
    container.register(
        LinkedInService,
        _create_linkedin_service,
        singleton=True,
    )

    # ------------------------------------------------------------------
    # Repository Layer
    # ------------------------------------------------------------------

    from repositories.workspace_repository import WorkspaceRepository
    container.register(
        WorkspaceRepository,
        lambda c=_create_workspace_repository: c(container),
        singleton=True,
    )

    from repositories.brand_repository import BrandRepository
    container.register(
        BrandRepository,
        lambda c=_create_brand_repository: c(container),
        singleton=True,
    )

    from repositories.ai_profile_repository import AIProfileRepository
    container.register(
        AIProfileRepository,
        lambda c=_create_ai_profile_repository: c(container),
        singleton=True,
    )

    from repositories.social_account_repository import SocialAccountRepository
    container.register(
        SocialAccountRepository,
        lambda c=_create_social_account_repository: c(container),
        singleton=True,
    )

    from repositories.knowledge_repository import KnowledgeRepository
    container.register(
        KnowledgeRepository,
        lambda c=_create_knowledge_repository: c(container),
        singleton=True,
    )

    # ------------------------------------------------------------------
    # Core Services Layer
    # ------------------------------------------------------------------

    # Rate Limiter (depends on Redis)
    from core.rate_limiter import RateLimiter
    container.register(
        RateLimiter,
        lambda c=_create_rate_limiter: c(container),
        singleton=True,
    )

    # Prompt Cache (depends on Redis)
    from services.prompt_cache import RedisPromptCache
    container.register(
        RedisPromptCache,
        lambda c=_create_prompt_cache: c(container),
        singleton=True,
    )

    # Task Queue (depends on Redis)
    from core.task_queue import RedisTaskQueue
    container.register(
        RedisTaskQueue,
        lambda c=_create_task_queue: c(container),
        singleton=True,
    )

    # Content Engine (depends on Gemini + Prompt Cache)
    from services.content_engine import ContentEngine
    container.register(
        ContentEngine,
        lambda c=_create_content_engine: c(container),
        singleton=True,
    )

    # Card Renderer (depends on Browser Manager)
    from services.card_renderer import CardRenderer
    container.register(
        CardRenderer,
        lambda c=_create_card_renderer: c(container),
        singleton=True,
    )

    # Connector Factory (depends on Social Account Repository)
    from services.connectors.connector_factory import ConnectorFactory
    container.register(
        ConnectorFactory,
        lambda c=_create_connector_factory: c(container),
        singleton=True,
    )

    # Socket Server (depends on Event Bus)
    from core.socket_server import SocketServer
    container.register(
        SocketServer,
        lambda c=_create_socket_server: c(container),
        singleton=True,
    )

    # ------------------------------------------------------------------
    # Orchestration Layer
    # ------------------------------------------------------------------

    # Content Pipeline (10-stage pipeline)
    from services.pipeline.pipeline import ContentPipeline
    container.register(
        ContentPipeline,
        lambda c=_create_pipeline: c(container),
        singleton=True,
    )

    # Brand Scheduler Manager
    from scheduler.post_scheduler import BrandSchedulerManager
    container.register(
        BrandSchedulerManager,
        lambda c=_create_post_scheduler: c(container),
        singleton=True,
    )

    # ------------------------------------------------------------------
    # Security Layer
    # ------------------------------------------------------------------

    # Audit Logger (depends on Supabase + Event Bus)
    from core.audit_logger import AuditLogger
    container.register(
        AuditLogger,
        lambda c=_create_audit_logger: c(container),
        singleton=True,
    )

    # API Token Manager (depends on Supabase)
    from core.api_tokens import APITokenManager
    container.register(
        APITokenManager,
        lambda c=_create_api_token_manager: c(container),
        singleton=True,
    )

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    logger.info(
        "Dependency registry initialized — %d services registered",
        len(container.registered_types()),
    )

    return container


# ============================================================================
# Module-level convenience (optional)
# ============================================================================

# Global container — initialized in bootstrap.py
_container: ServiceContainer | None = None


def get_container() -> ServiceContainer:
    """Get the global service container (set by bootstrap.py)."""
    if _container is None:
        raise RuntimeError(
            "Service container not initialized. "
            "Call register_all_dependencies() from bootstrap.py first."
        )
    return _container


def set_container(container: ServiceContainer) -> None:
    """Set the global service container (called from bootstrap.py)."""
    global _container
    _container = container


def resolve(interface):
    """Convenience function — resolve from global container."""
    return get_container().resolve(interface)
