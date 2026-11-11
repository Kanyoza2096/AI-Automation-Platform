"""
Kanyoza Systems AI Platform v16.4 — Dependency Registry.

THE single source of truth for all DI bindings. Every concrete
implementation is wired to its interface here.

v16.4 CHANGES:
  - build_container() eagerly resolves the SupabaseClient binding before
    returning. Previously the client was only created when something
    downstream called c.resolve(SupabaseClient), which happens on the
    first request that needs a repository — not at boot. The module-level
    `supabase_service` shim in services/supabase.py is separate from the
    container and stays uninitialized until that resolve fires. Routes
    and V71 services that read through the shim
    (supabase_service.service_client) got None, reported empty results,
    and the AI Control Plane / workforce hydration silently stayed
    dormant. Eager resolution at boot closes the gap.
  - If Supabase is unavailable at boot, a warning is logged and boot
    continues in a degraded state. The health endpoint reports the
    condition; no request handler crashes on a None client.
  - v16.4.1 (this revision): the eager resolution is placed IMMEDIATELY
    after _register_infrastructure() — before _register_workflows() and
    any other function that calls c.resolve(XRepository) at registration
    time. Previously the eager block ran at the very end of
    build_container(), which is too late: _register_workflows()
    resolves AnalyticsRepository / KnowledgeRepository / UnifiedEventBus
    eagerly, and those factories construct with supabase_client=None
    because the SupabaseClient binding has not yet been resolved. The
    repos become permanent singletons holding a None client, and every
    subsequent repo call falls back to in-memory mode.

v16.3 CHANGES (preserved):
  - Director's `publish_scheduled_content` capability is NO LONGER BOUND.
    The Director was autonomously calling it on its `review_content_strategy`
    cadence (~every 30 minutes), producing duplicate `source=manual` publishes
    on top of the scheduler's own fires. The scheduler is the authoritative
    publisher; the Director is an advisor. It may still boost/safe-mode
    content, run health scans, and queue revivals — but it does not trigger
    publishes.
  - The `publish_scheduled_content` function remains defined below for
    backward compatibility with any external callers. It is simply not
    bound to a Director capability.

v16.2 CHANGES (preserved):
  - ADDED: ServiceRegistry integration for auto-registration
  - ADDED: Service definitions for all platform services
  - All services now auto-register with Meta Engine via service_definitions.py

v16.1 CHANGES (preserved):
  - Added engine bindings (Director ↔ Content Engine ↔ Condition Engine ↔ Scheduler)
  - All v16.0 bindings preserved (UnifiedEventBus, repositories, AI layer, plugins)
  - FIXED: TaskQueue.start() properly awaited using async-aware factory
  - FIXED: Condition Engine binding with correct autonomous_engine import
  - FIXED: Health check coroutines properly awaited
  - FIXED: Import paths for core modules (services → core)
  - FIXED: MetaEngineV6 → MetaEngineV7 (class renamed)
  - ADDED: AutonomousContentEngine registration (from core)
  - ADDED: AutonomousLoop registration (from core)

V1.6 CHANGES (preserved):
  - ADDED: CouncilOrchestrator registration so the canonical publish
    pipeline (domain.publishing.canonical_pipeline._council_deliberate)
    can resolve it.
"""

from __future__ import annotations

import asyncio
import logging
import time
import warnings
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict

from config.settings import settings
from service_container import ServiceContainer, set_global_container

logger = logging.getLogger("kanyoza.registry")

# Suppress known library noise
logging.getLogger("supabase_auth").setLevel(logging.CRITICAL)
warnings.filterwarnings("ignore", message=".*_refresh_token_timer.*")


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
    bindings: list[BindingMetadata] = []

    _register_settings(container, bindings)
    _register_infrastructure(container, bindings)

    # ─── Eagerly initialize Supabase clients ────────────────────────
    # MUST run before any register function that calls c.resolve(XRepository)
    # at registration time (notably _register_workflows). Repositories are
    # registered as singletons; the first resolution constructs and freezes
    # the instance. If that resolution happens before the SupabaseClient
    # binding is resolved, the repo is permanently wired with a None client
    # and every later call falls back to in-memory mode.
    try:
        supabase = _resolve_supabase_safely(container)
        if supabase is not None:
            from services.supabase import _service_client, _anon_client
            logger.info(
                "Supabase clients initialized at boot "
                "(shim service=%s, shim anon=%s)",
                "present" if _service_client else "None",
                "present" if _anon_client else "None",
            )
        else:
            logger.warning(
                "Supabase client not available at boot — every Supabase "
                "read will return None until a caller resolves the "
                "SupabaseClient binding. Check SUPABASE_URL, "
                "SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY."
            )
    except Exception:
        logger.exception("Supabase eager initialization failed")

    _register_repositories(container, bindings)
    _register_ai_layer(container, bindings)
    _register_business_services(container, bindings)
    _register_intelligence_layer(container, bindings)
    _register_connector_plugins(container, bindings)
    _register_workflows(container, bindings)
    _register_observability(container, bindings)

    _log_registration_summary(container, bindings)
    return container


# ═══════════════════════════════════════════════════════════════════════════
# ENGINE BINDINGS
# ═══════════════════════════════════════════════════════════════════════════

def _bind_meta_engine(c: ServiceContainer) -> None:
    """Register recovery handlers on MetaEngine."""
    try:
        from services.meta_engine import MetaEngineV7
        meta = c.try_resolve(MetaEngineV7)
        if meta is None:
            logger.warning("MetaEngine not in container — recovery handlers skipped")
            return

        async def recover_director(_diagnosis=None):
            from services.director import get_director, init_director
            d = await get_director()
            if d is None:
                d = await init_director(auto_start=True)
            elif hasattr(d, "start") and not getattr(d, "running", False):
                await d.start()
            return {"ok": True, "engine": "director", "running": getattr(d, "running", True)}

        async def recover_condition(_diagnosis=None):
            from services.condition_engine import AutonomousConditionEngine
            eng = c.try_resolve(AutonomousConditionEngine)
            return {"ok": eng is not None, "engine": "condition"}

        async def recover_scheduler(_diagnosis=None):
            from scheduler.brand_scheduler import BrandSchedulerManager
            mgr = c.try_resolve(BrandSchedulerManager)
            return {"ok": mgr is not None, "engine": "scheduler"}

        async def recover_content(_diagnosis=None):
            from services.content_engine import content_engine
            return {"ok": content_engine is not None, "engine": "content"}

        if hasattr(meta, "register_recovery_handler"):
            meta.register_recovery_handler("director", recover_director)
            meta.register_recovery_handler("condition", recover_condition)
            meta.register_recovery_handler("scheduler", recover_scheduler)
            meta.register_recovery_handler("content", recover_content)
            logger.info("✅ Meta Engine recovery handlers registered")
        else:
            logger.warning("MetaEngine has no register_recovery_handler")
    except Exception as exc:
        logger.error("Meta Engine binding failed: %s", exc)


def bind_all_engines(c: ServiceContainer) -> None:
    """Bind all intelligent engines to real services."""
    try:
        _bind_condition_engine(c)
    except Exception as exc:
        logger.error("Condition Engine binding failed: %s", exc)

    try:
        _bind_director(c)
    except Exception as exc:
        logger.error("Director binding failed: %s", exc)

    try:
        _bind_meta_engine(c)
    except Exception as exc:
        logger.error("Meta Engine binding failed: %s", exc)


def _bind_condition_engine(c: ServiceContainer) -> None:
    """Bind Condition Engine actions to real Content Engine + Scheduler."""
    try:
        from services.condition_engine import AutonomousConditionEngine, ActionPlan
        from services.content_engine import content_engine
        from scheduler.brand_scheduler import BrandSchedulerManager

        autonomous_engine = None
        try:
            from services.condition_engine import autonomous_engine as _ae
            autonomous_engine = _ae
        except ImportError:
            autonomous_engine = c.try_resolve(AutonomousConditionEngine)

        if autonomous_engine is None:
            logger.error("Condition Engine not available - cannot bind actions")
            return

        async def real_executor(plan: ActionPlan) -> Dict[str, Any]:
            action = plan.action
            brand_id = plan.brand_id
            params = plan.parameters or {}

            # Council gate
            try:
                from council.integration import deliberate_action
                council_decision = await deliberate_action(
                    action_name=action,
                    source_engine="condition_engine",
                    description=getattr(plan, "reason", None) or f"Execute {action}",
                    proposal_type=(
                        "campaign_launch"
                        if action in {"run_experiment", "increase_similar_content"}
                        else "content_creation"
                    ),
                    metadata={"brand_id": brand_id},
                    action_params=params,
                )
                if council_decision.is_blocked:
                    return {
                        "success": False,
                        "blocked": True,
                        "reason": council_decision.reason,
                        "council": council_decision.to_dict(),
                    }
                if council_decision.needs_escalation:
                    return {
                        "success": False,
                        "escalated": True,
                        "reason": council_decision.reason,
                        "council": council_decision.to_dict(),
                    }
            except Exception as exc:
                logger.error("Condition Engine Council gate failed closed: %s", exc)
                return {"success": False, "error": "council_gate_unavailable"}

            try:
                if action == "increase_similar_content":
                    result = await content_engine.boost_content(
                        brand_id=brand_id,
                        factor=params.get("content_mix_change", 0.20),
                        duration_posts=params.get("duration_posts", 5),
                    )
                    return {"success": True, "service": "content_engine", "result": result}

                if action == "reduce_underperforming_content":
                    result = await content_engine.reduce_content(
                        brand_id=brand_id,
                        factor=params.get("content_mix_change", -0.20),
                        duration_posts=params.get("duration_posts", 5),
                    )
                    return {"success": True, "service": "content_engine", "result": result}

                if action == "reduce_content_risk":
                    result = await content_engine.safe_mode(
                        brand_id=brand_id,
                        duration_posts=params.get("duration_posts", 5),
                    )
                    return {"success": True, "service": "content_engine", "result": result}

                if action == "change_posting_time":
                    scheduler_manager = c.try_resolve(BrandSchedulerManager)
                    scheduler = (
                        scheduler_manager._schedulers.get(brand_id)
                        if scheduler_manager
                        else None
                    )
                    if not scheduler or not hasattr(scheduler, "update_config"):
                        return {"success": False, "error": "posting_time_change_unavailable"}
                    current = list(getattr(scheduler, "content_hours", []) or [])
                    if not current:
                        current = [9, 12, 18]
                    shifted = sorted({(int(h) + 2) % 24 for h in current})
                    result = scheduler.update_config(content_hours_utc=shifted)
                    return {"success": True, "service": "brand_scheduler", "result": result}

                if action == "change_content_type":
                    return {"success": False, "error": "content_type_change_unavailable"}

                if action == "run_experiment":
                    experiment = autonomous_engine.create_experiment(
                        brand_id=brand_id,
                        name=params.get("experiment_type", "hook_variation"),
                        variable=params.get("variable", "hook_type"),
                        control=params.get("control", "direct_technical_hook"),
                        variant=params.get("variant", "story_based_hook"),
                        auto_deploy=True,
                    )
                    return {"success": True, "experiment_id": experiment.id}

                if action == "apply_experiment_winner":
                    winner = params.get("winner")
                    result = await content_engine.boost_content(
                        brand_id=brand_id, factor=0.10, duration_posts=10
                    )
                    return {"success": True, "winner": winner, "result": result}

                if action == "monitor_positive_trend":
                    return {"success": True, "note": "passive"}

                if action == "investigate_decline":
                    return {
                        "success": True,
                        "service": "condition_engine",
                        "result": {
                            "action": "investigate_decline",
                            "observation_requested": True,
                        },
                    }

                return {"success": False, "error": f"Unknown action: {action}"}

            except Exception as exc:
                logger.error("Executor failed for %s: %s", action, exc, exc_info=True)
                return {"success": False, "error": str(exc)}

        autonomous_engine.set_action_executor(real_executor)
        logger.info("✅ Condition Engine executor bound to real services")

    except ImportError as exc:
        logger.error("Condition Engine binding failed - import error: %s", exc)
    except Exception as exc:
        logger.error("Condition Engine binding failed: %s", exc)


def _bind_director(c: ServiceContainer) -> None:
    """Bind Director capabilities to advisory services.

    v16.3 — The `publish_scheduled_content` capability is NOT bound.

    Previously the Director could autonomously trigger publishes via
    `publish_scheduled_content`, which called
    `BrandSchedulerManager.trigger_for_brand`. The Director's
    `review_content_strategy` decision (issued on its own cadence, roughly
    every 30 minutes) invoked that capability, producing `source=manual`
    publishes on top of the scheduler's own scheduled fires. The result
    was duplicate content and unpredictable timing.

    The scheduler is now the authoritative publisher. The Director remains
    an advisor: it may boost or safe-mode content, run health scans, and
    queue brand revivals. It does not trigger publishes.
    """
    try:
        from services.content_engine import content_engine
        from services.director import get_director

        async def publish_scheduled_content(params: dict) -> dict:
            """Legacy capability — retained for backward compatibility but
            no longer bound to the Director. Any direct caller still works,
            but the Director cannot invoke it autonomously.
            """
            brand_id = params.get("brand_id", "default")
            try:
                from scheduler.brand_scheduler import BrandSchedulerManager
                mgr = c.try_resolve(BrandSchedulerManager)
                if mgr:
                    key = params.get("idempotency_key") or f"dir_pub_{brand_id}_{int(time.time())}"
                    result = await mgr.trigger_for_brand(brand_id=brand_id, idempotency_key=key)
                    if isinstance(result, dict) and result.get("ok") is False:
                        return {"ok": False, "service": "brand_scheduler", "result": result}
                    return {"ok": True, "service": "brand_scheduler", "result": result}
            except Exception as exc:
                logger.warning("Scheduler publish path failed: %s", exc)
                err = str(exc)
                if any(tok in err for tok in ("CouncilDecision", "dissent", "decision_id", "AttributeError")):
                    return {
                        "ok": False,
                        "service": "brand_scheduler",
                        "error": err,
                        "note": "publish_path_aborted_council_or_decision_error",
                    }

            topic = params.get("topic")
            result = await content_engine.generate_post(topic=topic)
            return {"ok": True, "service": "content_engine", "result": result, "note": "generate_only"}

        async def optimize_content_schedule(params: dict) -> dict:
            brand_id = params.get("brand_id", "default")
            change = params.get("content_mix_change", 0.10)
            if change > 0:
                return await content_engine.boost_content(brand_id, factor=change)
            return await content_engine.reduce_content(brand_id, factor=change)

        async def calculate_brand_health(params: dict) -> dict:
            brand_id = params.get("brand_id", "default")
            from services.condition_engine import AutonomousConditionEngine

            ae = c.try_resolve(AutonomousConditionEngine)
            if ae is None:
                try:
                    from services.condition_engine import autonomous_engine as ae
                except ImportError:
                    ae = None

            if ae is None or not hasattr(ae, "calculate_brand_health"):
                return {
                    "ok": False,
                    "brand_id": brand_id,
                    "status": "unavailable",
                    "error": "condition_engine_unavailable",
                }

            result = ae.calculate_brand_health(brand_id)
            if asyncio.iscoroutine(result):
                result = await result
            return {"ok": True, "service": "condition_engine", "result": result}

        async def queue_brand_revival(params: dict) -> dict:
            brand_id = params.get("brand_id", "default")
            return await content_engine.boost_content(brand_id, factor=0.15, duration_posts=10)

        async def run_health_scan(params: dict) -> dict:
            from lifecycle import health_registry
            if health_registry and hasattr(health_registry, "check_all"):
                reports = await health_registry.check_all()
                return {"ok": True, "reports": reports}
            return {"ok": True, "note": "no_health_registry"}

        async def _bind_capabilities() -> None:
            director = await get_director()
            if not director:
                logger.warning("Director not initialized — capability bind skipped")
                return
            # v16.3 — 'publish_scheduled_content' deliberately NOT bound.
            # See the docstring above for the rationale.
            director.bind_capability("optimize_content_schedule", optimize_content_schedule)
            director.bind_capability("calculate_brand_health", calculate_brand_health)
            director.bind_capability("queue_brand_revival", queue_brand_revival)
            director.bind_capability("run_health_scan", run_health_scan)
            logger.info(
                "✅ Director capabilities bound (advisory only; "
                "publish_scheduled_content unbound)"
            )

        # Prefer scheduling on the running loop; never call asyncio.run from here
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_bind_capabilities())
        except RuntimeError:
            # No running loop yet (e.g. during early import). Schedule later.
            logger.debug("No running loop for Director bind — will be bound at boot time")
            # Store for later explicit call if needed
            c._pending_director_bind = _bind_capabilities  # type: ignore[attr-defined]

    except Exception as exc:
        logger.error("Director binding failed: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════
# SETTINGS
# ═══════════════════════════════════════════════════════════════════════════

def _register_settings(c: ServiceContainer, bindings: list[BindingMetadata]) -> None:
    from config.settings import Settings
    c.register_instance(Settings, settings)
    bindings.append(
        BindingMetadata("Settings", ServiceCriticality.CRITICAL, description="Application configuration")
    )


# ═══════════════════════════════════════════════════════════════════════════
# INFRASTRUCTURE
# ═══════════════════════════════════════════════════════════════════════════

def _register_infrastructure(c: ServiceContainer, bindings: list[BindingMetadata]) -> None:
    from core.unified_bus import (
        UnifiedEventBus,
        EventBusConfig,
        get_bus_sync,
        set_bus_instance,
    )

    def _build_unified_bus():
        # Reuse the module-level instance used by legacy direct-access callers.
        # This keeps DI and compatibility paths on one subscriber registry.
        bus = get_bus_sync()
        # Resolve the database client even when a direct-access caller created
        # the module singleton before the DI container finished booting.
        # Without this adoption step, lifecycle would start the same singleton
        # but its persistence layer would remain permanently disconnected.
        supabase_client = _resolve_supabase_safely(c)
        if bus is None:
            bus = UnifiedEventBus(
                supabase_client=supabase_client,
                config=EventBusConfig(),
            )
            set_bus_instance(bus)
            logger.info(
                "UnifiedEventBus created (persistence=%s)",
                supabase_client is not None,
            )
        elif bus.supabase is None and supabase_client is not None:
            bus.supabase = supabase_client
            logger.info("UnifiedEventBus adopted the bootstrapped Supabase client")
        elif bus.supabase is not None and supabase_client is not None and bus.supabase is not supabase_client:
            logger.warning(
                "UnifiedEventBus already owns a Supabase client; preserving its existing client"
            )
        else:
            logger.info("UnifiedEventBus reused from canonical module authority")
        return bus

    c.register(UnifiedEventBus, _build_unified_bus, singleton=True)
    bindings.append(
        BindingMetadata(
            "UnifiedEventBus",
            ServiceCriticality.CRITICAL,
            description="Unified async-native event system",
        )
    )

    from core.event_bus import EventBus, get_event_bus_bridge, EventBusBridge

    def _build_event_bus_bridge():
        unified = c.try_resolve(UnifiedEventBus)
        return get_event_bus_bridge(unified=unified)

    c.register(EventBus, _build_event_bus_bridge, singleton=True)
    c.register(EventBusBridge, _build_event_bus_bridge, singleton=True)
    bindings.append(
        BindingMetadata(
            "EventBus",
            ServiceCriticality.REQUIRED,
            description="Legacy DomainEvent API bridged to UnifiedEventBus",
        )
    )

    _register_redis(c, bindings)
    _register_supabase(c, bindings)

    from workers.task_queue import TaskQueue
    c.register(TaskQueue, _build_task_queue_sync, singleton=True)
    bindings.append(
        BindingMetadata(
            "TaskQueue",
            ServiceCriticality.REQUIRED,
            description="Background task worker pool",
        )
    )

    from domain.security.replay_guard import ReplayGuard
    c.register(ReplayGuard, lambda: ReplayGuard(ttl_seconds=300), singleton=True)
    bindings.append(
        BindingMetadata(
            "ReplayGuard",
            ServiceCriticality.CRITICAL,
            description="Nonce-based replay attack prevention",
        )
    )


def _build_task_queue_sync():
    from workers.task_queue import TaskQueue
    q = TaskQueue(workers=3)
    logger.info("TaskQueue constructed (start deferred to lifecycle)")
    return q


def _register_redis(c: ServiceContainer, bindings: list[BindingMetadata]) -> None:
    url = getattr(settings, "upstash_redis_rest_url", None)
    if not url:
        logger.info("Redis not configured — using in-memory fallback.")
        bindings.append(
            BindingMetadata(
                "UpstashRedisClient",
                ServiceCriticality.OPTIONAL,
                has_fallback=True,
                description="Redis client (not configured)",
            )
        )
        return

    from core.redis_client import UpstashRedisClient, RedisConfig
    config = RedisConfig(
        url=url,
        token=getattr(settings, "upstash_redis_rest_token", ""),
        enabled=True,
    )

    def _factory() -> UpstashRedisClient:
        return UpstashRedisClient(config)

    c.register(UpstashRedisClient, _factory, singleton=True)
    bindings.append(
        BindingMetadata(
            "UpstashRedisClient",
            ServiceCriticality.OPTIONAL,
            has_fallback=True,
            description="Redis client",
        )
    )


def _register_supabase(c: ServiceContainer, bindings: list[BindingMetadata]) -> None:
    if not settings.supabase_enabled:
        logger.info("Supabase not configured.")
        bindings.append(
            BindingMetadata(
                "SupabaseClient",
                ServiceCriticality.OPTIONAL,
                has_fallback=True,
                description="Supabase client (not configured)",
            )
        )
        return

    try:
        from supabase import Client as SupabaseClient
    except ImportError:
        logger.warning("supabase-py not installed.")
        bindings.append(
            BindingMetadata(
                "SupabaseClient",
                ServiceCriticality.OPTIONAL,
                has_fallback=True,
                description="Supabase client (not installed)",
            )
        )
        return

    def _factory() -> SupabaseClient:
        from services.supabase import get_service_client_from_settings, get_anon_client_from_settings
        client = get_service_client_from_settings()
        if client is not None:
            return client
        client = get_anon_client_from_settings()
        if client is not None:
            logger.warning("SupabaseClient resolved via anon singleton")
            return client
        raise RuntimeError("Supabase client could not be created")

    c.register(SupabaseClient, _factory, singleton=True)
    bindings.append(
        BindingMetadata(
            "SupabaseClient",
            ServiceCriticality.REQUIRED,
            has_fallback=True,
            description="Supabase client",
        )
    )


# ═══════════════════════════════════════════════════════════════════════════
# REPOSITORIES
# ═══════════════════════════════════════════════════════════════════════════

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


def _register_repository(
    c: ServiceContainer,
    bindings: list[BindingMetadata],
    name: str,
    module_path: str,
    class_name: str,
) -> None:
    module = __import__(module_path, fromlist=[class_name])
    repo_class = getattr(module, class_name)

    def _factory(repo_cls=repo_class):
        client = _resolve_supabase_safely(c)
        try:
            return repo_cls(supabase_client=client)
        except TypeError:
            return repo_cls()

    c.register(repo_class, _factory, singleton=True)
    bindings.append(
        BindingMetadata(
            name,
            ServiceCriticality.REQUIRED,
            has_fallback=True,
            description=f"Repository: {name}",
        )
    )


# ═══════════════════════════════════════════════════════════════════════════
# AI LAYER
# ═══════════════════════════════════════════════════════════════════════════

def _register_ai_layer(c: ServiceContainer, bindings: list[BindingMetadata]) -> None:
    from ai.orchestrator import AIOrchestrator, create_default_orchestrator
    from ai.gateway import LLMGateway, get_llm_gateway

    def _build_orchestrator():
        orch = create_default_orchestrator()
        try:
            gw = get_llm_gateway()
            gw.bind_orchestrator(orch)
        except Exception as exc:
            logger.debug("gateway bind_orchestrator: %s", exc)
        return orch

    c.register(AIOrchestrator, _build_orchestrator, singleton=True)
    bindings.append(
        BindingMetadata(
            "AIOrchestrator",
            ServiceCriticality.REQUIRED,
            description="Multi-provider AI orchestration (Gemini→DeepSeek→Groq→Mistral)",
        )
    )

    def _build_gateway():
        gw = get_llm_gateway()
        try:
            orch = c.try_resolve(AIOrchestrator)
            if orch is not None:
                gw.bind_orchestrator(orch)
        except Exception:
            pass
        return gw

    c.register(LLMGateway, _build_gateway, singleton=True)
    bindings.append(
        BindingMetadata(
            "LLMGateway",
            ServiceCriticality.REQUIRED,
            description="Single entry for probabilistic intelligence (budget/circuit/routing)",
        )
    )


# ═══════════════════════════════════════════════════════════════════════════
# INTELLIGENCE LAYER
# ═══════════════════════════════════════════════════════════════════════════

def _register_intelligence_layer(c: ServiceContainer, bindings: list[BindingMetadata]) -> None:
    from services.meta_engine import MetaEngineV7

    def _build_meta_engine():
        return MetaEngineV7(
            event_bus=None,
            container=c,
            supabase_client=_resolve_supabase_safely(c),
        )

    c.register(MetaEngineV7, _build_meta_engine, singleton=True)
    bindings.append(
        BindingMetadata(
            "MetaEngineV7",
            ServiceCriticality.REQUIRED,
            has_fallback=True,
            description="Resource-safe intelligent supervisor (async-native)",
        )
    )

    from services.condition_engine import AutonomousConditionEngine

    def _build_condition_engine():
        return AutonomousConditionEngine(supabase_client=_resolve_supabase_safely(c))

    c.register(AutonomousConditionEngine, _build_condition_engine, singleton=True)
    bindings.append(
        BindingMetadata(
            "AutonomousConditionEngine",
            ServiceCriticality.REQUIRED,
            has_fallback=True,
            description="Autonomous condition + decision engine",
        )
    )

    from services.director import Director

    def _build_director():
        return Director(supabase_client=_resolve_supabase_safely(c))

    c.register(Director, _build_director, singleton=True)
    bindings.append(
        BindingMetadata(
            "Director",
            ServiceCriticality.REQUIRED,
            has_fallback=True,
            description="Autonomous CEO (async-native)",
        )
    )

    from core.autonomous_content import AutonomousContentEngine

    def _build_autonomous_content():
        return AutonomousContentEngine(
            supabase_client=_resolve_supabase_safely(c),
            shared_bus=None,
            container=c,
        )

    c.register(AutonomousContentEngine, _build_autonomous_content, singleton=True)
    bindings.append(
        BindingMetadata(
            "AutonomousContentEngine",
            ServiceCriticality.REQUIRED,
            has_fallback=True,
            description="Self-driving content",
        )
    )

    from core.autonomous_loop import AutonomousLoop

    def _build_autonomous_loop():
        return AutonomousLoop(
            shared_bus=None,
            meta_engine=None,
            director=None,
            condition_engine=None,
            container=c,
        )

    c.register(AutonomousLoop, _build_autonomous_loop, singleton=True)
    bindings.append(
        BindingMetadata(
            "AutonomousLoop",
            ServiceCriticality.REQUIRED,
            has_fallback=True,
            description="Central closed-loop coordinator",
        )
    )

    # ✅ V1.6 — Register CouncilOrchestrator so the canonical publish pipeline
    # can resolve it in `_council_deliberate`.
    try:
        from council.orchestration.council_orchestrator import CouncilOrchestrator
        _council_orchestrator = CouncilOrchestrator()
        c.register_instance(CouncilOrchestrator, _council_orchestrator)
        logger.info(
            "✅ CouncilOrchestrator registered (version=%s, personas=%d)",
            _council_orchestrator.VERSION,
            len(_council_orchestrator._personas),
        )
    except Exception as exc:
        logger.warning("CouncilOrchestrator registration failed: %s", exc)
    bindings.append(
        BindingMetadata(
            "CouncilOrchestrator",
            ServiceCriticality.REQUIRED,
            has_fallback=False,
            description="Weighted-vote deliberation engine (12 personas)",
        )
    )

    # Store container reference for service registry (v16.2)
    try:
        meta = c.try_resolve(MetaEngineV7)
        if meta:
            setattr(meta, "_container_ref", c)
            logger.info("✅ MetaEngine container reference stored for service registry")
    except Exception as exc:
        logger.warning("Failed to store MetaEngine container reference: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════
# BUSINESS SERVICES
# ═══════════════════════════════════════════════════════════════════════════

def _register_business_services(c: ServiceContainer, bindings: list[BindingMetadata]) -> None:
    from services.business_automation import BusinessAutomationEngine

    def _build_business_automation_engine():
        client = _resolve_supabase_safely(c)
        return BusinessAutomationEngine(supabase_client=client)

    c.register(BusinessAutomationEngine, _build_business_automation_engine, singleton=True)
    bindings.append(
        BindingMetadata(
            "BusinessAutomationEngine",
            ServiceCriticality.REQUIRED,
            has_fallback=False,
            description="Canonical governed business-event/workflow admission engine",
        )
    )

    from core.unified_bus import UnifiedEventBus
    from core.multi_tenant import WorkspaceManager
    from repositories.workspace_repository import WorkspaceRepository

    c.register(
        WorkspaceManager,
        lambda: WorkspaceManager(c.resolve(WorkspaceRepository)),
        singleton=True,
    )
    bindings.append(
        BindingMetadata(
            "WorkspaceManager",
            ServiceCriticality.REQUIRED,
            description="Multi-tenant workspace management",
        )
    )

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
    bindings.append(
        BindingMetadata(
            "JobService",
            ServiceCriticality.REQUIRED,
            has_fallback=True,
            description="Job lifecycle management",
        )
    )

    from services.content_validator import ContentValidator
    c.register(ContentValidator, ContentValidator, singleton=True)
    bindings.append(
        BindingMetadata(
            "ContentValidator",
            ServiceCriticality.OPTIONAL,
            description="AI content validation",
        )
    )

    from scheduler.brand_scheduler import BrandSchedulerManager
    from repositories.brand_repository import BrandRepository
    from repositories.ai_profile_repository import AIProfileRepository

    def _build_brand_scheduler():
        return BrandSchedulerManager(
            c.resolve(BrandRepository),
            c.resolve(AIProfileRepository),
        )

    c.register(BrandSchedulerManager, _build_brand_scheduler, singleton=True)
    bindings.append(
        BindingMetadata(
            "BrandSchedulerManager",
            ServiceCriticality.REQUIRED,
            description="Per-brand post scheduling",
        )
    )

    from domain.analytics.event_recorder import EventRecorder
    from repositories.analytics_repository import AnalyticsRepository

    def _build_recorder():
        return EventRecorder(
            repo=c.resolve(AnalyticsRepository),
            # EventRecorder consumes synchronous DomainEvent callbacks. The
            # compatibility bridge adapts those callbacks onto the same
            # lifecycle-owned UnifiedEventBus instance.
            bus=c.resolve(EventBusBridge),
        )

    c.register(EventRecorder, _build_recorder, singleton=True)
    bindings.append(
        BindingMetadata(
            "EventRecorder",
            ServiceCriticality.OPTIONAL,
            description="Analytics event recording",
        )
    )

    from services.browser_manager import BrowserManager, browser_manager as _bm
    c.register_instance(BrowserManager, _bm)
    bindings.append(
        BindingMetadata(
            "BrowserManager",
            ServiceCriticality.OPTIONAL,
            description="Headless browser",
        )
    )

    from services.render_cache import RenderCache, render_cache as _rc
    c.register_instance(RenderCache, _rc)
    bindings.append(
        BindingMetadata(
            "RenderCache",
            ServiceCriticality.OPTIONAL,
            description="Render cache",
        )
    )

    if settings.guardian_configured:
        _register_guardian(c, bindings)
    else:
        logger.info("Code Guardian not configured — skipping.")
        bindings.append(
            BindingMetadata(
                "GuardianService",
                ServiceCriticality.OPTIONAL,
                description="Code Guardian (not configured)",
            )
        )


def _register_guardian(c: ServiceContainer, bindings: list[BindingMetadata]) -> None:
    from services.guardian_service import GuardianService
    from core.unified_bus import UnifiedEventBus

    def _build_guardian():
        from guardian.github_client import GitHubClient
        from guardian.scanner import CodeGuardianScanner
        from guardian.auto_fix import AutoFixEngine
        from services.facebook import facebook as fb_svc
        return GuardianService(
            github_client=GitHubClient(settings.github_username, settings.github_token),
            scanner=CodeGuardianScanner(),
            auto_fix_engine=AutoFixEngine(),
            event_bus=c.resolve(UnifiedEventBus),
            facebook_svc=fb_svc,
        )

    c.register(GuardianService, _build_guardian, singleton=True)
    bindings.append(
        BindingMetadata(
            "GuardianService",
            ServiceCriticality.OPTIONAL,
            description="Code scanning engine",
        )
    )


# ═══════════════════════════════════════════════════════════════════════════
# CONNECTOR PLUGINS
# ═══════════════════════════════════════════════════════════════════════════

def _register_connector_plugins(c: ServiceContainer, bindings: list[BindingMetadata]) -> None:
    from plugins.base import PluginRegistry
    from repositories.social_account_repository import SocialAccountRepository

    def _build_registry_with_plugins():
        registry = PluginRegistry()
        try:
            social_repo = c.resolve(SocialAccountRepository)
        except Exception:
            social_repo = None

        try:
            from plugins.facebook.plugin import FacebookPlugin
            from services.facebook import facebook as fb_svc
            registry.register(FacebookPlugin(fb_svc, social_repo))
            logger.info("FacebookPlugin registered.")
        except Exception as exc:
            logger.error("FacebookPlugin could not register: %s", exc)

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

        logger.info(
            "PluginRegistry built with %d plugin(s): %s",
            len(registry.names),
            registry.names,
        )
        return registry

    c.register(PluginRegistry, _build_registry_with_plugins, singleton=True)
    bindings.append(
        BindingMetadata(
            "PluginRegistry",
            ServiceCriticality.REQUIRED,
            has_fallback=True,
            description="Social media connector plugins",
        )
    )


# ═══════════════════════════════════════════════════════════════════════════
# WORKFLOWS
# ═══════════════════════════════════════════════════════════════════════════

def _register_workflows(c: ServiceContainer, bindings: list[BindingMetadata]) -> None:
    from core.unified_bus import UnifiedEventBus
    from repositories.analytics_repository import AnalyticsRepository
    from repositories.knowledge_repository import KnowledgeRepository
    from services.content_engine import ContentEngine
    from services.prompt_builder import PromptBuilder
    from services.content_semantic_reviewer import ContentSemanticReviewer
    from services.pipeline.pipeline import ContentGenerationPipeline
    from workflows.post_workflow import PostPublishWorkflow
    from plugins.base import PluginRegistry

    content_engine = ContentEngine(container=c)
    prompt_builder = PromptBuilder()
    semantic_reviewer = ContentSemanticReviewer()
    analytics_repo = c.resolve(AnalyticsRepository)
    bus = c.resolve(UnifiedEventBus)
    knowledge_repo = c.resolve(KnowledgeRepository)

    content_validator = None
    try:
        from services.content_validator import ContentValidator
        content_validator = c.resolve(ContentValidator)
    except Exception:
        pass

    def content_pipeline_factory(brand, ai_profile, category=None):
        return ContentGenerationPipeline(
            content_engine=content_engine,
            analytics_repo=analytics_repo,
            bus=bus,
            knowledge_repo=knowledge_repo,
            prompt_builder=prompt_builder,
            brand=brand,
            ai_profile=ai_profile,
            container=c,
            category=category,
            content_validator=content_validator,
            semantic_reviewer=semantic_reviewer,
        )

    def post_publish_workflow_factory(brand, ai_profile, category=None):
        registry = c.resolve(PluginRegistry)
        return PostPublishWorkflow(
            content_engine=content_engine,
            registry=registry,
            analytics_repo=analytics_repo,
            bus=bus,
            brand=brand,
            ai_profile=ai_profile,
            category=category,
            content_validator=content_validator,
            knowledge_repo=knowledge_repo,
            prompt_builder=prompt_builder,
            semantic_reviewer=semantic_reviewer,
            container=c,
        )

    c.register("ContentGenerationPipelineFactory", lambda: content_pipeline_factory, singleton=True)
    c.register("PostPublishWorkflowFactory", lambda: post_publish_workflow_factory, singleton=True)

    bindings.append(
        BindingMetadata(
            "ContentGenerationPipelineFactory",
            ServiceCriticality.REQUIRED,
            description="Content generation pipeline factory",
        )
    )
    bindings.append(
        BindingMetadata(
            "PostPublishWorkflowFactory",
            ServiceCriticality.REQUIRED,
            description="Canonical post publish workflow factory",
        )
    )


# ═══════════════════════════════════════════════════════════════════════════
# OBSERVABILITY
# ═══════════════════════════════════════════════════════════════════════════

def _register_observability(c: ServiceContainer, bindings: list[BindingMetadata]) -> None:
    from core.credential_vault import CredentialVault
    c.register(CredentialVault, CredentialVault, singleton=True)
    bindings.append(
        BindingMetadata(
            "CredentialVault",
            ServiceCriticality.CRITICAL,
            description="Encrypted credential storage",
        )
    )

    from core.rate_limiter import RateLimiter

    def _build_rate_limiter():
        return RateLimiter(redis=_resolve_redis_safely(c))

    c.register(RateLimiter, _build_rate_limiter, singleton=True)
    bindings.append(
        BindingMetadata(
            "RateLimiter",
            ServiceCriticality.OPTIONAL,
            has_fallback=True,
            description="API rate limiting",
        )
    )

    from core.api_tokens import APITokenManager

    def _build_token_manager():
        client = _resolve_supabase_safely(c)
        from core.api_tokens import api_token_manager
        api_token_manager.set_supabase_client(client)
        return APITokenManager(supabase_client=client)

    c.register(APITokenManager, _build_token_manager, singleton=True)
    bindings.append(
        BindingMetadata(
            "APITokenManager",
            ServiceCriticality.REQUIRED,
            has_fallback=True,
            description="API token management",
        )
    )

    from core.auth import AuditLogger
    from core.audit_service import AuditService, audit_service

    def _build_audit_logger():
        client = _resolve_supabase_safely(c)
        audit_service.set_supabase_client(client)
        return AuditLogger(supabase_client=client)

    def _build_audit_service():
        client = _resolve_supabase_safely(c)
        audit_service.set_supabase_client(client)
        return audit_service

    c.register(AuditLogger, _build_audit_logger, singleton=True)
    c.register(AuditService, _build_audit_service, singleton=True)
    bindings.append(
        BindingMetadata(
            "AuditLogger",
            ServiceCriticality.REQUIRED,
            has_fallback=True,
            description="Security audit logging (compat)",
        )
    )
    bindings.append(
        BindingMetadata(
            "AuditService",
            ServiceCriticality.REQUIRED,
            has_fallback=True,
            description="Canonical audit → audit_logs",
        )
    )

    from core.reliability import DeadLetterQueue, HealthChecker
    c.register(DeadLetterQueue, DeadLetterQueue, singleton=True)
    bindings.append(
        BindingMetadata(
            "DeadLetterQueue",
            ServiceCriticality.OPTIONAL,
            description="Failed job persistence",
        )
    )

    def _build_health_checker():
        from plugins.base import PluginRegistry
        registry = c.try_resolve(PluginRegistry)
        return HealthChecker(registry) if registry else HealthChecker(None)

    c.register(HealthChecker, _build_health_checker, singleton=True)
    bindings.append(
        BindingMetadata(
            "HealthChecker",
            ServiceCriticality.OPTIONAL,
            description="Plugin health checks",
        )
    )


# ═══════════════════════════════════════════════════════════════════════════
# SAFE RESOLVERS
# ═══════════════════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════════════════

def _log_registration_summary(c: ServiceContainer, bindings: list[BindingMetadata]) -> None:
    critical = [b for b in bindings if b.criticality == ServiceCriticality.CRITICAL]
    required = [b for b in bindings if b.criticality == ServiceCriticality.REQUIRED]
    optional = [b for b in bindings if b.criticality == ServiceCriticality.OPTIONAL]
    logger.info(
        "Service container built — %d bindings "
        "(CRITICAL=%d, REQUIRED=%d, OPTIONAL=%d, registered=%d)",
        len(bindings),
        len(critical),
        len(required),
        len(optional),
        len(c.registered_types()),
    )
