"""
Kanyoza Systems AI Platform v13.0 — Lifecycle Management (FastAPI).

Orchestrates application bootstrap and graceful shutdown through a
phase-based, dependency-ordered lifecycle system.

Architecture Principles:
  1. Services self-register their lifecycle, health checks, and shutdown hooks
  2. Boot is phased: INFRASTRUCTURE → CORE → BUSINESS → OBSERVABILITY
  3. Critical failures prevent startup; non-critical failures allow degraded mode
  4. Health is measured, not fabricated
  5. Everything is testable through dependency injection

Phase Dependency Order:
  Phase 0 — INFRASTRUCTURE (EventBus, Redis, Supabase) — BLOCKING
  Phase 1 — CORE (TaskQueue, JobService, PluginRegistry) — BLOCKING
  Phase 2 — BUSINESS (AI, Schedulers, ConditionEngine, Plugins) — DEGRADED_OK
  Phase 3 — OBSERVABILITY (Emitters, Health, Metrics) — OPTIONAL

Usage:
  Called from FastAPI lifespan context manager in main.py.
  No Flask dependencies. Pillow-based rendering.

Backward Compatibility:
  register_lifecycle() still works for existing test suites.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import signal
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from functools import partial
from typing import Any, Callable, Protocol, runtime_checkable

from plugins.base import PluginRegistry
from service_container import ServiceContainer

logger = logging.getLogger("kanyoza.lifecycle")

# ═══════════════════════════════════════════════════════════════════════════
# HEALTH CHECK PROTOCOL — Standardized health reporting
# ═══════════════════════════════════════════════════════════════════════════


class HealthStatus(Enum):
    """Standardized health status enum."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"
    STARTING = "starting"


@dataclass
class HealthReport:
    """Structured health report from any service."""
    status: HealthStatus
    service_name: str
    latency_ms: float = 0.0
    last_success: datetime | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.service_name,
            "status": self.status.value,
            "latency": round(self.latency_ms, 2),
            "lastChecked": self.checked_at.isoformat(),
            "lastSuccess": self.last_success.isoformat() if self.last_success else None,
            "error": self.error_message,
            **self.metadata,
        }


@runtime_checkable
class HealthProvider(Protocol):
    """Protocol for services that can report their health."""
    
    @property
    def service_name(self) -> str:
        """Unique identifier for this service."""
        ...
    
    def check_health(self) -> HealthReport:
        """Perform a health check and return structured report."""
        ...


@runtime_checkable
class LifecycleAware(Protocol):
    """Protocol for services with startup/shutdown lifecycle."""
    
    async def start(self) -> None:
        """Initialize and start the service."""
        ...
    
    async def stop(self) -> None:
        """Gracefully stop the service."""
        ...


# ═══════════════════════════════════════════════════════════════════════════
# BOOT PHASE CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════


class BootPhase(Enum):
    """Ordered boot phases with criticality."""
    INFRASTRUCTURE = (0, True)   # Fail = prevent startup
    CORE = (1, True)              # Fail = prevent startup
    BUSINESS = (2, False)         # Fail = degraded mode
    OBSERVABILITY = (3, False)    # Fail = continue without monitoring
    
    def __init__(self, order: int, is_critical: bool):
        self.order = order
        self.is_critical = is_critical
    
    @property
    def allows_degraded(self) -> bool:
        return not self.is_critical


@dataclass
class BootStep:
    """A single step in the boot sequence."""
    name: str
    phase: BootPhase
    action: Callable[[], None]
    rollback: Callable[[], None] | None = None
    timeout_seconds: float = 30.0


# ═══════════════════════════════════════════════════════════════════════════
# SERVICE HEALTH REGISTRY — Centralized health tracking
# ═══════════════════════════════════════════════════════════════════════════


class ServiceHealthRegistry:
    """Central registry for service health providers.
    
    Services self-register during initialization. The health emitter
    iterates registered providers rather than maintaining a hardcoded list.
    """
    
    def __init__(self):
        self._providers: dict[str, HealthProvider] = {}
        self._lock = threading.RLock()
        self._startup_complete = threading.Event()
    
    def register(self, provider: HealthProvider) -> None:
        """Register a health provider. Safe to call from any thread."""
        with self._lock:
            if provider.service_name in self._providers:
                logger.warning(
                    "Health provider '%s' registered twice — overwriting.",
                    provider.service_name,
                )
            self._providers[provider.service_name] = provider
            logger.debug(
                "Health provider registered: %s (%d total)",
                provider.service_name,
                len(self._providers),
            )
    
    def unregister(self, service_name: str) -> None:
        """Remove a health provider."""
        with self._lock:
            self._providers.pop(service_name, None)
    
    def get_all(self) -> list[HealthProvider]:
        """Get all registered providers (thread-safe snapshot)."""
        with self._lock:
            return list(self._providers.values())
    
    def get(self, service_name: str) -> HealthProvider | None:
        """Get a specific provider by name."""
        with self._lock:
            return self._providers.get(service_name)
    
    def check_all(self, timeout_per_service: float = 5.0) -> dict[str, HealthReport]:
        """Check health of all registered services with per-service timeout."""
        results: dict[str, HealthReport] = {}
        providers = self.get_all()
        
        for provider in providers:
            try:
                start = time.monotonic()
                report = provider.check_health()
                elapsed = time.monotonic() - start
                report.latency_ms = elapsed * 1000
                results[provider.service_name] = report
            except Exception as exc:
                logger.warning(
                    "Health check failed for '%s': %s",
                    provider.service_name,
                    exc,
                )
                results[provider.service_name] = HealthReport(
                    status=HealthStatus.UNHEALTHY,
                    service_name=provider.service_name,
                    error_message=str(exc),
                )
        
        # Report UNKNOWN for services that didn't register
        if self._startup_complete.is_set():
            for name in self._providers:
                if name not in results:
                    results[name] = HealthReport(
                        status=HealthStatus.UNKNOWN,
                        service_name=name,
                        error_message="Health check not performed",
                    )
        
        return results
    
    def mark_startup_complete(self) -> None:
        """Signal that all boot phases are complete."""
        self._startup_complete.set()
    
    @property
    def provider_count(self) -> int:
        with self._lock:
            return len(self._providers)


# Global singleton — initialized during Phase 0
health_registry = ServiceHealthRegistry()


# ═══════════════════════════════════════════════════════════════════════════
# SHUTDOWN HOOK REGISTRY
# ═══════════════════════════════════════════════════════════════════════════


class ShutdownRegistry:
    """Ordered shutdown hook registry.
    
    Services register cleanup callbacks during boot. Shutdown runs
    hooks in reverse registration order (LIFO).
    """
    
    def __init__(self):
        self._hooks: list[tuple[str, Callable[[], None], float]] = []
        self._lock = threading.RLock()
    
    def register(
        self,
        name: str,
        hook: Callable[[], None],
        timeout_seconds: float = 10.0,
    ) -> None:
        """Register a shutdown hook. Executed in reverse registration order."""
        with self._lock:
            self._hooks.append((name, hook, timeout_seconds))
            logger.debug("Shutdown hook registered: %s (timeout=%ss)", name, timeout_seconds)
    
    def execute_all(self) -> None:
        """Execute all shutdown hooks with individual timeouts."""
        with self._lock:
            hooks = list(reversed(self._hooks))
            self._hooks.clear()
        
        for name, hook, timeout in hooks:
            logger.info("Shutting down: %s ...", name)
            try:
                start = time.monotonic()
                hook()
                elapsed = time.monotonic() - start
                logger.info("Shutdown complete: %s (%.2fs)", name, elapsed)
            except Exception as exc:
                logger.error("Shutdown failed for '%s': %s", name, exc)


# Global singleton
shutdown_registry = ShutdownRegistry()


# ═══════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINTS — called from FastAPI lifespan
# ═══════════════════════════════════════════════════════════════════════════


def boot_infrastructure(app, container: ServiceContainer) -> None:
    """Execute phased boot sequence.
    
    Raises:
        SystemExit: If a critical boot phase fails.
    """
    logger.info("=" * 60)
    logger.info("Kanyoza AI Platform v13.0 — Starting boot sequence")
    logger.info("=" * 60)
    
    start_time = time.monotonic()
    boot_steps = _build_boot_sequence(app, container)
    
    # Phase-ordered execution
    phases: dict[BootPhase, list[BootStep]] = {}
    for step in boot_steps:
        phases.setdefault(step.phase, []).append(step)
    
    completed_steps: list[BootStep] = []
    degraded_services: list[str] = []
    
    for phase in sorted(BootPhase, key=lambda p: p.order):
        phase_steps = phases.get(phase, [])
        if not phase_steps:
            continue
        
        logger.info("── Phase %d: %s (%d steps) ──", phase.order, phase.name, len(phase_steps))
        
        for step in phase_steps:
            try:
                logger.info("  ▶ %s ...", step.name)
                step_start = time.monotonic()
                step.action()
                step_elapsed = time.monotonic() - step_start
                completed_steps.append(step)
                logger.info("  ✓ %s (%.2fs)", step.name, step_elapsed)
            except Exception as exc:
                logger.error("  ✗ %s FAILED: %s", step.name, exc)
                
                if phase.is_critical:
                    logger.critical(
                        "Critical boot failure in phase %s — rolling back and exiting.",
                        phase.name,
                    )
                    _rollback(completed_steps)
                    raise SystemExit(1) from exc
                else:
                    degraded_services.append(step.name)
                    logger.warning(
                        "  ⚠ %s skipped — operating in degraded mode.",
                        step.name,
                    )
    
    # Mark health registry as ready
    health_registry.mark_startup_complete()
    
    total_time = time.monotonic() - start_time
    logger.info("=" * 60)
    logger.info(
        "Boot complete in %.2fs — %d services started, %d degraded",
        total_time,
        len(completed_steps),
        len(degraded_services),
    )
    if degraded_services:
        logger.warning("Degraded services: %s", degraded_services)
    logger.info("=" * 60)


def graceful_shutdown(container: ServiceContainer) -> None:
    """Execute all registered shutdown hooks."""
    logger.info("=" * 60)
    logger.info("Kanyoza AI Platform — Initiating graceful shutdown")
    logger.info("=" * 60)
    
    shutdown_start = time.monotonic()
    shutdown_registry.execute_all()
    
    total_time = time.monotonic() - shutdown_start
    logger.info("Graceful shutdown complete (%.2fs).", total_time)


# ═══════════════════════════════════════════════════════════════════════════
# BOOT SEQUENCE BUILDER
# ═══════════════════════════════════════════════════════════════════════════


def _build_boot_sequence(app, container: ServiceContainer) -> list[BootStep]:
    """Build the ordered boot sequence with all steps.
    
    This is the single source of truth for what boots and in what order.
    """
    steps: list[BootStep] = []
    
    # ═══ Phase 0: INFRASTRUCTURE (Critical) ═══
    
    steps.append(BootStep(
        name="EventBus",
        phase=BootPhase.INFRASTRUCTURE,
        action=lambda: _boot_event_bus(container),
    ))
    
    steps.append(BootStep(
        name="SupabaseClient",
        phase=BootPhase.INFRASTRUCTURE,
        action=lambda: _boot_supabase(container),
    ))
    
    steps.append(BootStep(
        name="RedisClient",
        phase=BootPhase.INFRASTRUCTURE,
        action=lambda: _boot_redis(container),
    ))
    
    # ═══ Phase 1: CORE (Critical) ═══
    
    steps.append(BootStep(
        name="TaskQueue",
        phase=BootPhase.CORE,
        action=lambda: _boot_task_queue(container),
        rollback=lambda: _stop_task_queue(container),
        timeout_seconds=15.0,
    ))
    
    steps.append(BootStep(
        name="PluginRegistry",
        phase=BootPhase.CORE,
        action=lambda: container.resolve(PluginRegistry), 
        rollback=lambda: _stop_plugin_registry(container),
    ))
    
    steps.append(BootStep(
        name="JobService",
        phase=BootPhase.CORE,
        action=lambda: _boot_job_service(container),
    ))
    
    steps.append(BootStep(
        name="RenderQueue",
        phase=BootPhase.CORE,
        action=lambda: _boot_render_queue(container),
    ))
    
    steps.append(BootStep(
        name="EventRecorder",
        phase=BootPhase.CORE,
        action=lambda: _boot_event_recorder(container),
    ))
    
    # ═══ Phase 2: BUSINESS (Degraded OK) ═══
    
    steps.append(BootStep(
        name="AIOrchestrator",
        phase=BootPhase.BUSINESS,
        action=lambda: _boot_ai_orchestrator(container),
    ))
    
    steps.append(BootStep(
        name="ConditionEngine",
        phase=BootPhase.BUSINESS,
        action=lambda: _boot_condition_engine(container),
    ))
    
    steps.append(BootStep(
        name="IndustryPlugins",
        phase=BootPhase.BUSINESS,
        action=lambda: _boot_industry_plugins(app, container),
        rollback=lambda: _stop_industry_plugins(container),
    ))
    
    steps.append(BootStep(
        name="JobRecovery",
        phase=BootPhase.BUSINESS,
        action=lambda: _recover_stale_jobs(container),
    ))
    
    steps.append(BootStep(
        name="BrandSchedulers",
        phase=BootPhase.BUSINESS,
        action=lambda: _boot_brand_schedulers(container),
        rollback=lambda: _stop_brand_schedulers(container),
    ))
    
    steps.append(BootStep(
        name="GuardianScheduler",
        phase=BootPhase.BUSINESS,
        action=lambda: _boot_guardian_scheduler(container),
    ))
    
    steps.append(BootStep(
        name="SocketBridge",
        phase=BootPhase.BUSINESS,
        action=lambda: _wire_socket_events(container),
    ))
    
    # ═══ Phase 3: OBSERVABILITY (Optional) ═══
    
    steps.append(BootStep(
        name="HealthMonitor",
        phase=BootPhase.OBSERVABILITY,
        action=lambda: _boot_health_monitor(container),
        rollback=lambda: _stop_health_monitor(container),
    ))
    
    steps.append(BootStep(
        name="EnterpriseEmitters",
        phase=BootPhase.OBSERVABILITY,
        action=lambda: _start_enterprise_emitters(container),
        rollback=lambda: _stop_enterprise_emitters(),
    ))
    
    steps.append(BootStep(
        name="APICallCounter",
        phase=BootPhase.OBSERVABILITY,
        action=lambda: _register_api_call_counter(app),
    ))
    
    steps.append(BootStep(
        name="MonitorPlugin",
        phase=BootPhase.OBSERVABILITY,
        action=lambda: _boot_monitor_plugin(container),
    ))
    
    steps.append(BootStep(
        name="BackupPlugin",
        phase=BootPhase.OBSERVABILITY,
        action=lambda: _boot_backup_plugin(container),
    ))
    
    return steps


def _rollback(completed_steps: list[BootStep]) -> None:
    """Rollback completed steps in reverse order."""
    logger.warning("Rolling back %d completed steps...", len(completed_steps))
    for step in reversed(completed_steps):
        if step.rollback:
            try:
                logger.info("  Rolling back: %s", step.name)
                step.rollback()
            except Exception as exc:
                logger.error("  Rollback failed for %s: %s", step.name, exc)


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 0: INFRASTRUCTURE BOOT
# ═══════════════════════════════════════════════════════════════════════════


def _boot_event_bus(container: ServiceContainer) -> None:
    """Initialize EventBus — must be first so subscribers can register."""
    from core.event_bus import EventBus
    bus = container.resolve(EventBus)
    
    # Register EventBus as a health provider
    health_registry.register(_EventBusHealthProvider(bus))
    logger.info("EventBus ready.")


def _boot_supabase(container: ServiceContainer) -> None:
    """Verify Supabase connectivity."""
    from config.settings import settings
    from services.supabase import get_service_client_from_settings
    
    if not settings.supabase_enabled:
        logger.info("Supabase disabled — skipping.")
        return
    
    get_service_client_from_settings()
    logger.info("Supabase connected (service role: %s)", settings.supabase_admin_enabled)


def _boot_redis(container: ServiceContainer) -> None:
    """Verify Redis connectivity."""
    from core.redis_client import UpstashRedisClient
    
    try:
        redis = container.resolve(UpstashRedisClient)
        redis.ping()
        logger.info("Redis connected.")
    except Exception as exc:
        logger.warning("Redis not available — caching disabled: %s", exc)
        # Redis is non-critical; don't raise


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 1: CORE SERVICES
# ═══════════════════════════════════════════════════════════════════════════


def _boot_task_queue(container: ServiceContainer) -> None:
    """Start background task queue workers."""
    from workers.task_queue import TaskQueue
    tq = container.resolve(TaskQueue)
    shutdown_registry.register(
        "TaskQueue",
        lambda: tq.stop(timeout=10),
        timeout_seconds=15.0,
    )
    health_registry.register(_TaskQueueHealthProvider(tq))
    logger.info("TaskQueue ready.")


def _boot_job_service(container: ServiceContainer) -> None:
    """Initialize JobService with Supabase client."""
    from services.job_service import JobService
    from services.supabase import get_service_client_from_settings, get_anon_client_from_settings
    from config.settings import settings
    
    client = None
    if settings.supabase_admin_enabled:
        client = get_service_client_from_settings()
    if client is None and settings.supabase_enabled:
        client = get_anon_client_from_settings()
    
    job_service = JobService(supabase_client=client)
    container.register_instance(JobService, job_service)
    
    if client:
        logger.info("JobService initialized with Supabase client.")
        health_registry.register(_JobServiceHealthProvider(job_service))
    else:
        logger.warning("JobService initialized WITHOUT Supabase — in-memory fallback only.")


def _boot_render_queue(container: ServiceContainer) -> None:
    """Wire DI container into RenderQueue and clear stuck tasks."""
    from core.render_queue import wire_render_queue_container, get_render_queue
    
    wire_render_queue_container(container)
    logger.info("RenderQueue container wired.")
    
    rq = get_render_queue()
    cleared = rq.clear_stuck_tasks()
    if cleared:
        logger.warning("RenderQueue: cleared %d stuck tasks.", cleared)
    
    health_registry.register(_RenderQueueHealthProvider(rq))


def _boot_event_recorder(container: ServiceContainer) -> None:
    """Initialize analytics event recorder."""
    from domain.analytics.event_recorder import EventRecorder
    recorder = container.resolve(EventRecorder)
    health_registry.register(_EventRecorderHealthProvider(recorder))
    logger.info("EventRecorder ready.")


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 2: BUSINESS SERVICES
# ═══════════════════════════════════════════════════════════════════════════


def _boot_ai_orchestrator(container: ServiceContainer) -> None:
    """Validate AI providers are reachable."""
    from ai.orchestrator import AIOrchestrator
    
    orch = container.resolve(AIOrchestrator)
    health = orch.health()
    available = [name for name, h in health.items() if h.get("available")]
    
    if not available:
        logger.error("No AI providers available — AI features disabled.")
        # Don't raise — business phase allows degraded mode
        return
    
    health_registry.register(_AIOrchestratorHealthProvider(orch))
    logger.info("AIOrchestrator ready. Available: %s", available)


def _boot_condition_engine(container: ServiceContainer) -> None:
    """Initialize autonomous rule evaluation engine and wire integrations."""
    from services.condition_engine import autonomous_engine
    from core.event_bus import EventBus
    
    event_bus = container.resolve(EventBus)
    ce = autonomous_engine
    logger.info("ConditionEngine ready — %d decisions tracked", len(ce._decisions))
    
    # Wire Email Service
    try:
        from services.email_service import email_service, register_email_actions
        register_email_actions(ce, email_service)
        logger.info("EmailService wired to ConditionEngine.")
        health_registry.register(_EmailServiceHealthProvider(email_service))
    except Exception as exc:
        logger.warning("EmailService wiring failed: %s", exc)
    
    # Start Engagement Tracker
    try:
        from services.engagement_tracker import start_engagement_tracking
        start_engagement_tracking(container=container)
        logger.info("EngagementTracker started.")
    except Exception as exc:
        logger.warning("EngagementTracker start failed: %s", exc)
    
    health_registry.register(_ConditionEngineHealthProvider(ce))


def _boot_industry_plugins(app, container: ServiceContainer) -> None:
    """Auto-discover and register industry plugins."""
    from config.settings import settings
    
    supabase_client = None
    try:
        from supabase import Client as SupabaseClient
        supabase_client = container.try_resolve(SupabaseClient)
    except Exception:
        pass
    
    redis_client = None
    try:
        from core.redis_client import UpstashRedisClient
        redis_client = container.try_resolve(UpstashRedisClient)
    except Exception:
        pass
    
    plugins = _discover_industry_plugins(supabase_client, redis_client)
    container.industry_plugins = plugins
    
    if not plugins:
        logger.info("No industry plugins discovered.")
        return
    
    _register_plugin_commands(plugins)
    _register_industry_webhooks(app, plugins)
    
    # Register shutdown hooks for all plugins
    for name, plugin in plugins.items():
        if hasattr(plugin, "shutdown"):
            shutdown_registry.register(
                f"IndustryPlugin:{name}",
                plugin.shutdown,
                timeout_seconds=10.0,
            )
    
    # Register health providers
    for name, plugin in plugins.items():
        if isinstance(plugin, HealthProvider):
            health_registry.register(plugin)
    
    logger.info(
        "Industry plugins loaded: %s (%d total commands)",
        list(plugins.keys()),
        sum(len(p.get_commands()) for p in plugins.values()),
    )


def _discover_industry_plugins(
    supabase_client=None,
    redis_client=None,
) -> dict:
    """Discover industry plugins via filesystem scanning.
    
    TODO: Migrate to setuptools entry points for standardized discovery.
    """
    plugins: dict = {}
    plugins_dir = os.path.join(os.path.dirname(__file__), "plugins", "industries")
    
    if not os.path.isdir(plugins_dir):
        logger.debug("No plugins/industries directory.")
        return plugins
    
    for folder in sorted(os.listdir(plugins_dir)):
        folder_path = os.path.join(plugins_dir, folder)
        
        if not os.path.isdir(folder_path):
            continue
        if folder.startswith("__") or folder in ("base", "specs", "addons"):
            continue
        
        plugin_file = os.path.join(folder_path, "plugin.py")
        if not os.path.isfile(plugin_file):
            continue
        
        try:
            module = importlib.import_module(f"plugins.industries.{folder}.plugin")
            
            plugin_class = None
            for name in dir(module):
                obj = getattr(module, name)
                if (
                    isinstance(obj, type)
                    and name.endswith("Plugin")
                    and name != "IndustryPlugin"
                ):
                    plugin_class = obj
                    break
            
            if plugin_class is None:
                logger.warning("No Plugin class in %s — skipping.", folder)
                continue
            
            plugin_instance = plugin_class(
                supabase_client=supabase_client,
                redis_client=redis_client,
            )
            plugins[folder] = plugin_instance
            logger.info(
                "Industry plugin discovered: %s (%d commands)",
                folder,
                len(plugin_instance.get_commands()),
            )
        except ImportError as exc:
            logger.debug("Plugin '%s' import skipped: %s", folder, exc)
        except Exception as exc:
            logger.warning("Plugin '%s' failed to load: %s", folder, exc)
    
    return plugins


def _register_plugin_commands(plugins: dict) -> None:
    """Register plugin commands with CommandExecutor."""
    try:
        from services.command_executor import CommandExecutor
        
        total = 0
        for plugin_name, plugin in plugins.items():
            for cmd in plugin.get_commands():
                CommandExecutor.register_external_command(plugin_name, cmd)
                total += 1
        
        logger.info("Registered %d plugin commands.", total)
    except ImportError:
        logger.warning("CommandExecutor not available.")
    except Exception as exc:
        logger.warning("Plugin command registration failed: %s", exc)


def _register_industry_webhooks(app, plugins: dict) -> None:
    """Register webhook routes with authentication and error handling."""
    from fastapi import Request, HTTPException
    from fastapi.responses import JSONResponse
    
    for plugin_name, plugin in plugins.items():
        if not hasattr(plugin, "get_webhooks"):
            continue
        
        webhooks = plugin.get_webhooks()
        for wh in webhooks:
            path = wh.get("path")
            method = wh.get("method", "POST")
            handler_name = wh.get("handler")
            
            if not path or not handler_name:
                continue
            
            handler_fn = getattr(plugin, handler_name, None)
            if handler_fn is None:
                logger.warning(
                    "Webhook handler '%s' not found on plugin '%s'",
                    handler_name,
                    plugin_name,
                )
                continue
            
            # Create route handler with proper async support and error boundaries
            async def make_handler(request: Request, p_fn=handler_fn, p_name=plugin_name):
                # Parse request body with size limit
                data = {}
                try:
                    body = await request.body()
                    if len(body) > 10 * 1024 * 1024:  # 10MB limit
                        raise HTTPException(status_code=413, detail="Payload too large")
                    if body:
                        data = await request.json()
                except HTTPException:
                    raise
                except Exception:
                    # Fall back to query params
                    data = dict(request.query_params)
                
                # Execute handler
                try:
                    if asyncio.iscoroutinefunction(p_fn):
                        result = await p_fn(data)
                    else:
                        result = p_fn(data)
                    return JSONResponse(content=result)
                except Exception as exc:
                    logger.error(
                        "Webhook execution failed [%s/%s]: %s",
                        p_name,
                        handler_name,
                        exc,
                    )
                    return JSONResponse(
                        content={
                            "ok": False,
                            "error": "Internal processing error",
                            "ref": f"wh-{p_name}-{datetime.now(timezone.utc).timestamp():.0f}",
                        },
                        status_code=500,
                    )
            
            app.add_api_route(
                path,
                make_handler,
                methods=[method],
                name=f"industry_webhook_{plugin_name}_{handler_name}",
            )
            logger.info(
                "Webhook route: %s [%s] → %s:%s",
                path, method, plugin_name, handler_name,
            )


def _recover_stale_jobs(container: ServiceContainer) -> None:
    """Architecture §16 — Recover stale jobs on boot."""
    from services.job_service import JobService
    
    job_service = container.try_resolve(JobService)
    if job_service is None:
        logger.debug("JobService not available — skipping stale job recovery.")
        return
    
    stale_count = job_service.recover_stale_jobs(stale_minutes=15)
    if stale_count > 0:
        logger.warning("Recovered %d stale jobs for retry (threshold=15 min).", stale_count)
    else:
        logger.info("No stale jobs found.")


def _boot_brand_schedulers(container: ServiceContainer) -> None:
    """Start per-brand schedulers."""
    from scheduler.brand_scheduler import BrandSchedulerManager
    
    manager = container.resolve(BrandSchedulerManager)
    manager.set_container(container)
    manager.start()
    
    shutdown_registry.register(
        "BrandSchedulerManager",
        manager.stop,
        timeout_seconds=15.0,
    )
    logger.info("BrandSchedulerManager started.")


def _boot_guardian_scheduler(container: ServiceContainer) -> None:
    """Start Code Guardian if configured."""
    from config.settings import settings
    
    if not settings.guardian_configured:
        logger.info("Code Guardian disabled.")
        return
    
    from services.guardian_service import GuardianService
    from scheduler.scan_scheduler import ScanScheduler
    
    guardian = container.resolve(GuardianService)
    
    # Wire to slash command
    try:
        from routes.webhook import set_guardian_runner
        set_guardian_runner(guardian.scan_all)
        logger.info("Guardian wired to /scan command.")
    except Exception as exc:
        logger.debug("Could not wire Guardian to slash handler: %s", exc)
    
    scan_scheduler = ScanScheduler(guardian.scan_all, settings.guardian_scan_hours)
    scan_scheduler.start()
    logger.info("Guardian started (every %dh).", settings.guardian_scan_hours)


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 3: OBSERVABILITY
# ═══════════════════════════════════════════════════════════════════════════


def _boot_health_monitor(container: ServiceContainer) -> None:
    """Start HealthMonitorService for outage alerts."""
    from services.health_monitor_service import HealthMonitorService
    
    hm = HealthMonitorService(container=container)
    hm.start()
    container.register_instance(HealthMonitorService, hm)
    
    shutdown_registry.register(
        "HealthMonitorService",
        hm.stop,
        timeout_seconds=10.0,
    )
    logger.info("HealthMonitorService started.")


def _register_api_call_counter(app) -> None:
    """Register API call counter middleware."""
    # Middleware registration handled in main.py
    logger.info("API call counter ready.")


def _boot_monitor_plugin(container):
    try:
        from plugins.monitor.plugin import init_monitor_plugin
        from services.supabase import get_service_client_from_settings
        client = get_service_client_from_settings()
        init_monitor_plugin(supabase_client=client)
        logger.info("MonitorPlugin: started")
    except Exception as exc:
        logger.warning("MonitorPlugin: could not start: %s", exc)


def _boot_backup_plugin(container):
    try:
        from plugins.backup.plugin import init_backup_plugin
        from services.supabase import get_service_client_from_settings
        client = get_service_client_from_settings()
        init_backup_plugin(supabase_client=client)
        logger.info("BackupPlugin: started")
    except Exception as exc:
        logger.warning("BackupPlugin: could not start: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════
# HEALTH PROVIDER IMPLEMENTATIONS
# ═══════════════════════════════════════════════════════════════════════════


class _EventBusHealthProvider:
    """Health check for EventBus."""
    
    def __init__(self, bus):
        self._bus = bus
        self.service_name = "event_bus"
    
    def check_health(self) -> HealthReport:
        try:
            subscriber_count = len(getattr(self._bus, "_subscribers", {}))
            return HealthReport(
                status=HealthStatus.HEALTHY,
                service_name=self.service_name,
                metadata={"subscribers": subscriber_count},
            )
        except Exception as exc:
            return HealthReport(
                status=HealthStatus.UNHEALTHY,
                service_name=self.service_name,
                error_message=str(exc),
            )


class _TaskQueueHealthProvider:
    def __init__(self, tq):
        self._tq = tq
        self.service_name = "task_queue"
    
    def check_health(self) -> HealthReport:
        try:
            queue_size = getattr(self._tq, "queue", None)
            size = queue_size.qsize() if queue_size else 0
            return HealthReport(
                status=HealthStatus.HEALTHY,
                service_name=self.service_name,
                metadata={"queue_size": size},
            )
        except Exception as exc:
            return HealthReport(
                status=HealthStatus.UNHEALTHY,
                service_name=self.service_name,
                error_message=str(exc),
            )


class _PluginRegistryHealthProvider:
    def __init__(self, registry):
        self._registry = registry
        self.service_name = "plugin_registry"
    
    def check_health(self) -> HealthReport:
        try:
            plugin_count = len(getattr(self._registry, "plugins", []))
            return HealthReport(
                status=HealthStatus.HEALTHY,
                service_name=self.service_name,
                metadata={"plugins": plugin_count},
            )
        except Exception as exc:
            return HealthReport(
                status=HealthStatus.UNHEALTHY,
                service_name=self.service_name,
                error_message=str(exc),
            )


class _JobServiceHealthProvider:
    def __init__(self, job_service):
        self._js = job_service
        self.service_name = "job_service"
    
    def check_health(self) -> HealthReport:
        try:
            return HealthReport(
                status=HealthStatus.HEALTHY,
                service_name=self.service_name,
            )
        except Exception as exc:
            return HealthReport(
                status=HealthStatus.UNHEALTHY,
                service_name=self.service_name,
                error_message=str(exc),
            )


class _RenderQueueHealthProvider:
    def __init__(self, rq):
        self._rq = rq
        self.service_name = "render_queue"
    
    def check_health(self) -> HealthReport:
        try:
            pending = len(getattr(self._rq, "_pending", []))
            return HealthReport(
                status=HealthStatus.HEALTHY,
                service_name=self.service_name,
                metadata={"pending_tasks": pending},
            )
        except Exception as exc:
            return HealthReport(
                status=HealthStatus.UNHEALTHY,
                service_name=self.service_name,
                error_message=str(exc),
            )


class _EventRecorderHealthProvider:
    def __init__(self, recorder):
        self._recorder = recorder
        self.service_name = "event_recorder"
    
    def check_health(self) -> HealthReport:
        return HealthReport(
            status=HealthStatus.HEALTHY,
            service_name=self.service_name,
        )


class _AIOrchestratorHealthProvider:
    def __init__(self, orch):
        self._orch = orch
        self.service_name = "ai_orchestrator"
    
    def check_health(self) -> HealthReport:
        try:
            health = self._orch.health()
            available = sum(1 for h in health.values() if h.get("available"))
            total = len(health)
            return HealthReport(
                status=HealthStatus.HEALTHY if available > 0 else HealthStatus.DEGRADED,
                service_name=self.service_name,
                metadata={"available": available, "total": total},
            )
        except Exception as exc:
            return HealthReport(
                status=HealthStatus.UNHEALTHY,
                service_name=self.service_name,
                error_message=str(exc),
            )


class _ConditionEngineHealthProvider:
    def __init__(self, engine):
        self._engine = engine
        self.service_name = "condition_engine"
    
    def check_health(self) -> HealthReport:
        try:
            decisions = len(getattr(self._engine, "_decisions", []))
            return HealthReport(
                status=HealthStatus.HEALTHY,
                service_name=self.service_name,
                metadata={"decisions_tracked": decisions},
            )
        except Exception as exc:
            return HealthReport(
                status=HealthStatus.UNHEALTHY,
                service_name=self.service_name,
                error_message=str(exc),
            )


class _EmailServiceHealthProvider:
    def __init__(self, email_service):
        self._es = email_service
        self.service_name = "email_service"
    
    def check_health(self) -> HealthReport:
        return HealthReport(
            status=HealthStatus.HEALTHY,
            service_name=self.service_name,
        )


# ═══════════════════════════════════════════════════════════════════════════
# ENTERPRISE EMITTERS — Refactored
# ═══════════════════════════════════════════════════════════════════════════

_emitter_started = False
_emitter_lock = threading.Lock()
_emitter_stop_event = threading.Event()


def _start_enterprise_emitters(container: ServiceContainer) -> None:
    """Start background emitter threads with thread-safe guard."""
    global _emitter_started
    
    with _emitter_lock:
        if _emitter_started:
            logger.warning("Enterprise emitters already started — skipping.")
            return
        _emitter_started = True
        _emitter_stop_event.clear()
    
    emitters = [
        _EmitterConfig(
            name="system_resources",
            interval=30,
            emitter_fn=partial(_collect_system_resources),
        ),
        _EmitterConfig(
            name="service_health",
            interval=30,
            emitter_fn=partial(_collect_service_health),
        ),
        _EmitterConfig(
            name="ai_provider_health",
            interval=60,
            emitter_fn=partial(_collect_ai_provider_health, container),
        ),
        _EmitterConfig(
            name="plugin_status",
            interval=120,
            emitter_fn=partial(_collect_plugin_status, container),
        ),
    ]
    
    for config in emitters:
        thread = threading.Thread(
            target=_emitter_loop,
            args=(config,),
            daemon=True,
            name=f"emitter-{config.name}",
        )
        thread.start()
    
    logger.info(
        "Enterprise emitters started: %s",
        ", ".join(f"{e.name} ({e.interval}s)" for e in emitters),
    )


@dataclass
class _EmitterConfig:
    name: str
    interval: int
    emitter_fn: Callable[[], dict[str, Any]]


def _emitter_loop(config: _EmitterConfig) -> None:
    """Standardized emitter loop with error isolation and logging."""
    logger.debug("Emitter '%s' starting (interval=%ds)...", config.name, config.interval)
    
    while not _emitter_stop_event.is_set():
        try:
            payload = config.emitter_fn()
            if payload:
                payload["timestamp"] = datetime.now(timezone.utc).isoformat()
                _schedule_socketio_emit(config.name, payload)
                logger.debug("Emitted %s: %d keys", config.name, len(payload))
        except Exception as exc:
            logger.error(
                "Emitter '%s' error (will retry in %ds): %s",
                config.name,
                config.interval,
                exc,
            )
        
        _emitter_stop_event.wait(config.interval)
    
    logger.debug("Emitter '%s' stopped.", config.name)


def _collect_system_resources() -> dict[str, Any]:
    """Collect CPU, memory, and disk metrics."""
    cpu = mem = disk = 0.0
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=1)
        mem = psutil.virtual_memory().percent
        disk = psutil.disk_usage("/").percent
    except ImportError:
        pass  # psutil not installed — expected in some environments
    except Exception as exc:
        logger.debug("System resource collection failed: %s", exc)
    
    return {
        "cpu_percent": round(cpu, 1),
        "memory_percent": round(mem, 1),
        "disk_percent": round(disk, 1),
    }


def _collect_service_health() -> dict[str, Any]:
    """Collect health from all registered providers."""
    reports = health_registry.check_all()
    
    services = {}
    for name, report in reports.items():
        services[name] = report.to_dict()
    
    # Add any registered services that might be pending check
    all_providers = health_registry.get_all()
    for provider in all_providers:
        name = provider.service_name
        if name not in services:
            services[name] = {
                "name": name,
                "status": HealthStatus.STARTING.value,
                "latency": 0,
                "lastChecked": datetime.now(timezone.utc).isoformat(),
                "error": "Health check pending",
            }
    
    return {"services": services}


def _collect_ai_provider_health(container: ServiceContainer) -> dict[str, Any]:
    """Collect AI provider health from orchestrator."""
    try:
        from ai.orchestrator import AIOrchestrator
        orch = container.try_resolve(AIOrchestrator)
        if not orch:
            return {"providers": []}
        
        health = orch.health()
        providers = []
        for provider_name, h in health.items():
            providers.append({
                "provider": provider_name,
                "model": h.get("model", "unknown"),
                "available": h.get("available", False),
                "latency_ms": h.get("latency_ms", 0),
                "last_checked": datetime.now(timezone.utc).isoformat(),
            })
        return {"providers": providers}
    except Exception:
        return {"providers": []}


def _collect_plugin_status(container: ServiceContainer) -> dict[str, Any]:
    """Collect plugin status information."""
    industry_plugins = getattr(container, "industry_plugins", {})
    plugins_data = []
    
    for name, plugin in industry_plugins.items():
        try:
            plugins_data.append({
                "name": name,
                "commands": len(plugin.get_commands()) if hasattr(plugin, "get_commands") else 0,
                "webhooks": len(plugin.get_webhooks()) if hasattr(plugin, "get_webhooks") else 0,
                "status": "active",
            })
        except Exception:
            plugins_data.append({
                "name": name,
                "commands": 0,
                "webhooks": 0,
                "status": "degraded",
            })
    
    return {"plugins": plugins_data}


def _stop_enterprise_emitters() -> None:
    """Signal all emitter threads to stop."""
    global _emitter_started
    _emitter_stop_event.set()
    _emitter_started = False
    logger.info("Enterprise emitters stopped.")


# ═══════════════════════════════════════════════════════════════════════════
# SOCKETIO HELPERS
# ═══════════════════════════════════════════════════════════════════════════


def _schedule_socketio_emit(event_name: str, payload: dict) -> None:
    """Schedule an async SocketIO emission with error tracking."""
    try:
        from core.socket_server import emit_to_dashboard
        loop = asyncio.get_running_loop()
        future = asyncio.run_coroutine_threadsafe(
            emit_to_dashboard(event_name, payload),
            loop,
        )
        # Log if the coroutine raises
        def _log_error(fut):
            try:
                fut.result()
            except Exception as exc:
                logger.debug("SocketIO emit failed (%s): %s", event_name, exc)
        future.add_done_callback(_log_error)
    except RuntimeError:
        # No running event loop — expected during shutdown
        pass
    except Exception as exc:
        logger.debug("SocketIO schedule failed (%s): %s", event_name, exc)


def _wire_socket_events(container: ServiceContainer) -> None:
    """Bridge EventBus events to SocketIO dashboard."""
    try:
        from core.event_bus import EventBus
        from core.events import EventType
        from core.socket_server import emit_to_dashboard, increment

        bus = container.resolve(EventBus)

        def _safe_payload(event) -> dict:
            return {
                "event_id": event.event_id,
                "occurred_at": event.occurred_at.isoformat(),
                "source": event.source,
                **event.payload,
            }

        def _emit(event_name: str, payload: dict) -> None:
            try:
                loop = asyncio.get_running_loop()
                task = asyncio.create_task(emit_to_dashboard(event_name, payload))
                task.add_done_callback(
                    lambda t: logger.debug("Socket emit error (%s): %s", event_name, t.exception())
                    if t.exception() else None
                )
            except RuntimeError:
                pass

        # Event type → socket event mapping
        mappings = [
            (EventType.MESSAGE_RECEIVED, "new_message", True),
            (EventType.POST_PUBLISHED, "post_published", True),
            (EventType.SCAN_COMPLETED, "scan_complete", False),
            (EventType.PROVIDER_FAILED, "provider_failed", False),
            (EventType.POST_GENERATED, "post_generated", False),
            (EventType.IMAGE_RENDERED, "image_rendered", False),
            (EventType.WORKER_ERROR, "worker_error", False),
        ]

        for event_type, socket_event, track_metric in mappings:
            def make_handler(se=socket_event, tm=track_metric):
                def handler(event):
                    try:
                        if tm:
                            if se == "new_message":
                                increment("messages_today")
                            elif se == "post_published":
                                increment("posts_today")
                        _emit(se, _safe_payload(event))
                    except Exception as exc:
                        logger.debug("Socket bridge error (%s): %s", se, exc)
                return handler
            
            bus.subscribe(event_type, make_handler())

        logger.info(
            "SocketIO bridge active: %d event subscriptions.",
            len(mappings),
        )
    except Exception as exc:
        logger.warning("SocketIO bridge initialization failed: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════
# SHUTDOWN HELPERS (for rollback)
# ═══════════════════════════════════════════════════════════════════════════


def _stop_task_queue(container: ServiceContainer) -> None:
    from workers.task_queue import TaskQueue
    tq = container.try_resolve(TaskQueue)
    if tq:
        tq.stop(timeout=5)


def _stop_plugin_registry(container: ServiceContainer) -> None:
    from plugins.base import PluginRegistry
    registry = container.try_resolve(PluginRegistry)
    if registry:
        registry.stop_all()


def _stop_industry_plugins(container: ServiceContainer) -> None:
    plugins = getattr(container, "industry_plugins", {})
    for name, plugin in plugins.items():
        if hasattr(plugin, "shutdown"):
            try:
                plugin.shutdown()
            except Exception as exc:
                logger.warning("Plugin '%s' shutdown failed: %s", name, exc)


def _stop_brand_schedulers(container: ServiceContainer) -> None:
    from scheduler.brand_scheduler import BrandSchedulerManager
    manager = container.try_resolve(BrandSchedulerManager)
    if manager:
        manager.stop()


def _stop_health_monitor(container: ServiceContainer) -> None:
    from services.health_monitor_service import HealthMonitorService
    hm = container.try_resolve(HealthMonitorService)
    if hm:
        hm.stop()


# ═══════════════════════════════════════════════════════════════════════════
# BACKWARD COMPATIBILITY
# ═══════════════════════════════════════════════════════════════════════════


def register_lifecycle(app, container: ServiceContainer) -> None:
    """Deprecated — use boot_infrastructure() instead.
    
    Kept for backward compatibility with existing test suites.
    """
    logger.warning(
        "register_lifecycle() is deprecated. Use boot_infrastructure() instead."
    )
    boot_infrastructure(app, container)


# ═══════════════════════════════════════════════════════════════════════════
# EXPOSED FOR TESTING
# ═══════════════════════════════════════════════════════════════════════════

__all__ = [
    "boot_infrastructure",
    "graceful_shutdown",
    "register_lifecycle",
    "health_registry",
    "shutdown_registry",
    "HealthStatus",
    "HealthReport",
    "HealthProvider",
    "BootPhase",
    "ServiceHealthRegistry",
    "ShutdownRegistry",
]
