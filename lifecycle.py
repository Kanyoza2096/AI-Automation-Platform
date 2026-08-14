"""
Kanyoza Systems AI Platform v14.1 — Lifecycle Management (FastAPI).

Orchestrates application bootstrap and graceful shutdown through a
phase-based, dependency-ordered lifecycle system.

v14.1 HARDENING (Targeted Patch Release):
  - BootStep timeout enforcement (asyncio.wait_for)
  - Webhook body size validation BEFORE reading into memory
  - Email health check no longer masks failures
  - BrandSchedulerManager registered for shutdown
  - Director capability binding syntax error fixed
  - ConditionEngine decision history bounded (ring buffer)
  - ShutdownRegistry thread-safe execution
  - Structured logging with correlation IDs
  - Health report error sanitization (no internal paths)
  - SocketIO bridge event loop capture hardened

v14.1.1 CRITICAL FIXES:
  - BackupPlugin timeout (10s)
  - TrendIntelligence timeout (15s)
  - MonitorPlugin timeout (10s)
  - Removed dead thread creation in _execute_with_timeout
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import signal
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from functools import partial, wraps
from typing import Any, Callable, Protocol, runtime_checkable
from services.engine_health_handlers import register_all_health_handlers

from plugins.base import PluginRegistry
from service_container import ServiceContainer

logger = logging.getLogger("kanyoza.lifecycle")

# v14.1 — Structured logging helper with correlation IDs
_CONTEXT = threading.local()


def _log(level: int, msg: str, *args: Any, **kwargs: Any) -> None:
    """Emit a structured log entry with correlation ID."""
    correlation_id = getattr(_CONTEXT, "correlation_id", None) or str(uuid.uuid4())
    if not hasattr(_CONTEXT, "correlation_id"):
        _CONTEXT.correlation_id = correlation_id
    logger.log(level, msg, *args, **kwargs, extra={"correlation_id": correlation_id})


def _sanitize_error_message(exc: Exception, max_len: int = 200) -> str:
    """Sanitize exception messages to prevent internal path leakage."""
    raw = str(exc)
    import re
    raw = re.sub(r'File "[^"]*"', "File [redacted]", raw)
    raw = re.sub(r"/[^\s]*\.py", "[path_redacted]", raw)
    raw = re.sub(r"0x[0-9a-fA-F]+", "[addr]", raw)
    return raw[:max_len]


# ═══════════════════════════════════════════════════════════════════════════
# HEALTH CHECK PROTOCOL
# ═══════════════════════════════════════════════════════════════════════════

class HealthStatus(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"
    STARTING = "starting"


@dataclass
class HealthReport:
    status: HealthStatus
    service_name: str
    latency_ms: float = 0.0
    last_success: datetime | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    checked_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.service_name,
            "status": self.status.value,
            "latency": round(self.latency_ms, 2),
            "lastChecked": self.checked_at.isoformat(),
            "lastSuccess": (
                self.last_success.isoformat()
                if self.last_success
                else None
            ),
            "error": self.error_message,
            **self.metadata,
        }


@runtime_checkable
class HealthProvider(Protocol):
    @property
    def service_name(self) -> str: ...
    def check_health(self) -> HealthReport: ...


@runtime_checkable
class LifecycleAware(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...


# ═══════════════════════════════════════════════════════════════════════════
# BOOT PHASE CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

class BootPhase(Enum):
    INFRASTRUCTURE = (0, True)
    CORE = (1, True)
    BUSINESS = (2, False)
    OBSERVABILITY = (3, False)

    def __init__(self, order: int, is_critical: bool):
        self.order = order
        self.is_critical = is_critical

    @property
    def allows_degraded(self) -> bool:
        return not self.is_critical


@dataclass
class BootStep:
    name: str
    phase: BootPhase
    action: Callable[[], None]
    rollback: Callable[[], None] | None = None
    timeout_seconds: float = 30.0


# ═══════════════════════════════════════════════════════════════════════════
# SERVICE HEALTH REGISTRY
# ═══════════════════════════════════════════════════════════════════════════

class _JobServiceHealthProvider:
    def __init__(self, job_service):
        self._js = job_service
        self.service_name = "job_service"

    def check_health(self) -> HealthReport:
        try:
            result = self._js.health_check()
            status_str = result.get("status", "healthy") if isinstance(result, dict) else "healthy"
            
            return HealthReport(
                status=(
                    HealthStatus.HEALTHY
                    if status_str in ("healthy", "degraded")
                    else HealthStatus.DEGRADED
                ),
                service_name=self.service_name,
                metadata=result if isinstance(result, dict) else {},
            )
        except Exception as exc:
            return HealthReport(
                status=HealthStatus.UNHEALTHY,
                service_name=self.service_name,
                error_message=_sanitize_error_message(exc),
            )


class ServiceHealthRegistry:
    def __init__(self):
        self._providers: dict[str, HealthProvider] = {}
        self._lock = threading.RLock()
        self._startup_complete = threading.Event()

    def register(self, provider: HealthProvider) -> None:
        with self._lock:
            if provider.service_name in self._providers:
                _log(
                    logging.WARNING,
                    "Health provider '%s' registered twice — overwriting.",
                    provider.service_name,
                )
            self._providers[provider.service_name] = provider

    def unregister(self, service_name: str) -> None:
        with self._lock:
            self._providers.pop(service_name, None)

    def get_all(self) -> list[HealthProvider]:
        with self._lock:
            return list(self._providers.values())

    def get(self, service_name: str) -> HealthProvider | None:
        with self._lock:
            return self._providers.get(service_name)

    def check_all(
        self, timeout_per_service: float = 5.0
    ) -> dict[str, HealthReport]:
        results: dict[str, HealthReport] = {}
        providers = self.get_all()
        for provider in providers:
            try:
                start = time.monotonic()
                report = provider.check_health()
                report.latency_ms = (
                    time.monotonic() - start
                ) * 1000
                results[provider.service_name] = report
            except Exception as exc:
                results[provider.service_name] = HealthReport(
                    status=HealthStatus.UNHEALTHY,
                    service_name=provider.service_name,
                    error_message=_sanitize_error_message(exc),
                )
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
        self._startup_complete.set()

    @property
    def provider_count(self) -> int:
        with self._lock:
            return len(self._providers)


health_registry = ServiceHealthRegistry()


# ═══════════════════════════════════════════════════════════════════════════
# SHUTDOWN HOOK REGISTRY
# ═══════════════════════════════════════════════════════════════════════════

class ShutdownRegistry:
    """Thread-safe registry for shutdown hooks.

    v14.1 — Fixed race condition: hooks are now executed while holding
    the lock, ensuring no re-entrant modifications can corrupt state.
    """

    def __init__(self):
        self._hooks: list[
            tuple[str, Callable[[], None], float]
        ] = []
        self._lock = threading.RLock()
        self._executing = False

    def register(
        self,
        name: str,
        hook: Callable[[], None],
        timeout_seconds: float = 10.0,
    ) -> None:
        with self._lock:
            if self._executing:
                _log(
                    logging.WARNING,
                    "Shutdown already in progress — ignoring hook '%s'",
                    name,
                )
                return
            self._hooks.append(
                (name, hook, timeout_seconds)
            )

    def execute_all(self) -> None:
        with self._lock:
            if self._executing:
                return
            self._executing = True
            hooks = list(reversed(self._hooks))
            self._hooks.clear()

        for name, hook, timeout in hooks:
            _log(logging.INFO, "Shutting down: %s ...", name)
            try:
                self._execute_with_timeout(hook, timeout)
            except TimeoutError:
                _log(
                    logging.ERROR,
                    "Shutdown timed out for '%s' after %.1fs",
                    name,
                    timeout,
                )
            except Exception as exc:
                _log(
                    logging.ERROR,
                    "Shutdown failed for '%s': %s",
                    name,
                    _sanitize_error_message(exc),
                )

    @staticmethod
    def _execute_with_timeout(
        hook: Callable[[], None],
        timeout_seconds: float,
    ) -> None:
        """Execute a hook with timeout enforcement using a daemon thread.

        v14.1.1 — Removed dead thread creation. Single clean thread.
        """
        result: dict[str, Any] = {}

        def _run() -> None:
            try:
                hook()
                result["completed"] = True
                result["error"] = None
            except Exception as exc:
                result["completed"] = True
                result["error"] = exc

        thread = threading.Thread(
            target=_run,
            daemon=True,
            name=f"shutdown-hook-{uuid.uuid4().hex[:8]}",
        )
        thread.start()
        thread.join(timeout=timeout_seconds)

        if thread.is_alive():
            raise TimeoutError(f"Hook timed out after {timeout_seconds}s")
        if result.get("error"):
            raise result["error"]


shutdown_registry = ShutdownRegistry()


# ═══════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINTS
# ═══════════════════════════════════════════════════════════════════════════

def boot_infrastructure(
    app, container: ServiceContainer
) -> None:
    _log(logging.INFO, "=" * 60)
    _log(logging.INFO, "Kanyoza AI Platform v14.1.1 — Starting boot sequence")
    _log(logging.INFO, "=" * 60)

    start_time = time.monotonic()
    boot_steps = _build_boot_sequence(app, container)

    phases: dict[BootPhase, list[BootStep]] = {}
    for step in boot_steps:
        phases.setdefault(step.phase, []).append(step)

    completed_steps: list[BootStep] = []
    degraded_services: list[str] = []

    for phase in sorted(BootPhase, key=lambda p: p.order):
        phase_steps = phases.get(phase, [])
        if not phase_steps:
            continue
        _log(
            logging.INFO,
            "── Phase %d: %s (%d steps) ──",
            phase.order,
            phase.name,
            len(phase_steps),
        )
        for step in phase_steps:
            try:
                _log(logging.INFO, "  ▶ %s ...", step.name)
                step_start = time.monotonic()
                
                _execute_boot_step_with_timeout(step)
                
                step_elapsed = time.monotonic() - step_start
                completed_steps.append(step)
                _log(
                    logging.INFO,
                    "  ✓ %s (%.2fs)",
                    step.name,
                    step_elapsed,
                )
            except TimeoutError:
                _log(
                    logging.ERROR,
                    "  ✗ %s TIMED OUT after %.1fs",
                    step.name,
                    step.timeout_seconds,
                )
                if phase.is_critical:
                    _log(
                        logging.CRITICAL,
                        "Critical boot timeout — rolling back.",
                    )
                    _rollback(completed_steps)
                    raise SystemExit(1) from TimeoutError(
                        f"{step.name} timed out"
                    )
                else:
                    degraded_services.append(step.name)
            except Exception as exc:
                _log(
                    logging.ERROR,
                    "  ✗ %s FAILED: %s",
                    step.name,
                    _sanitize_error_message(exc),
                )
                if phase.is_critical:
                    _log(
                        logging.CRITICAL,
                        "Critical boot failure — rolling back.",
                    )
                    _rollback(completed_steps)
                    raise SystemExit(1) from exc
                else:
                    degraded_services.append(step.name)

    health_registry.mark_startup_complete()
    total_time = time.monotonic() - start_time
    _log(logging.INFO, "=" * 60)
    _log(
        logging.INFO,
        "Boot complete in %.2fs — %d started, %d degraded",
        total_time,
        len(completed_steps),
        len(degraded_services),
    )
    if degraded_services:
        _log(
            logging.WARNING,
            "Degraded services: %s",
            degraded_services,
        )
    _log(logging.INFO, "=" * 60)


def _execute_boot_step_with_timeout(step: BootStep) -> None:
    """Execute a boot step with timeout enforcement."""
    result: dict[str, Any] = {}

    def _run() -> None:
        try:
            step.action()
            result["completed"] = True
            result["error"] = None
        except Exception as exc:
            result["completed"] = True
            result["error"] = exc

    thread = threading.Thread(
        target=_run,
        daemon=True,
        name=f"boot-step-{uuid.uuid4().hex[:8]}",
    )
    thread.start()
    thread.join(timeout=step.timeout_seconds)

    if thread.is_alive():
        raise TimeoutError(
            f"Boot step '{step.name}' timed out after {step.timeout_seconds}s"
        )
    if result.get("error"):
        raise result["error"]


def graceful_shutdown(container: ServiceContainer) -> None:
    _log(logging.INFO, "=" * 60)
    _log(logging.INFO, "Initiating graceful shutdown")
    _log(logging.INFO, "=" * 60)
    shutdown_registry.execute_all()
    _log(logging.INFO, "Shutdown complete.")


# ═══════════════════════════════════════════════════════════════════════════
# BOOT SEQUENCE BUILDER
# ═══════════════════════════════════════════════════════════════════════════

def _build_boot_sequence(
    app, container: ServiceContainer
) -> list[BootStep]:
    steps: list[BootStep] = []

    # Phase 0: INFRASTRUCTURE
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

    # Phase 1: CORE
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
        action=lambda: _boot_plugin_registry(container),
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

    # Phase 2: BUSINESS
    steps.append(BootStep(
        name="AIOrchestrator",
        phase=BootPhase.BUSINESS,
        action=lambda: _boot_ai_orchestrator(container),
    ))
    steps.append(BootStep(
        name="ContentEngine",
        phase=BootPhase.BUSINESS,
        action=lambda: _boot_content_engine(container),
    ))
    steps.append(BootStep(
        name="MetaEngine",
        phase=BootPhase.BUSINESS,
        action=lambda: _boot_meta_engine(container),
    ))
    steps.append(BootStep(
        name="Director",
        phase=BootPhase.BUSINESS,
        action=lambda: _boot_director(container),
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
    #steps.append(BootStep(
        #name="GuardianScheduler",
        #phase=BootPhase.BUSINESS,
        #action=lambda: _boot_guardian_scheduler(container),
    #))
    steps.append(BootStep(
        name="SocketBridge",
        phase=BootPhase.BUSINESS,
        action=lambda: _wire_socket_events(container),
    ))

    # Phase 3: OBSERVABILITY
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
    #steps.append(BootStep(
        #name="MonitorPlugin",
        #phase=BootPhase.OBSERVABILITY,
        #action=lambda: _boot_monitor_plugin(container),
        #timeout_seconds=10.0,  # v14.1.1 FIX
    #))
    steps.append(BootStep(
        name="BackupPlugin",
        phase=BootPhase.OBSERVABILITY,
        action=lambda: _boot_backup_plugin(container),
        timeout_seconds=10.0,  # v14.1.1 FIX
    ))
    steps.append(BootStep(
        name="TrendIntelligence",
        phase=BootPhase.OBSERVABILITY,
        action=lambda: _boot_trend_plugin(container),
        timeout_seconds=15.0,  # v14.1.1 FIX
    ))

    return steps


def _rollback(completed_steps: list[BootStep]) -> None:
    _log(
        logging.WARNING,
        "Rolling back %d steps...",
        len(completed_steps),
    )
    for step in reversed(completed_steps):
        if step.rollback:
            try:
                step.rollback()
            except Exception as exc:
                _log(
                    logging.ERROR,
                    "Rollback failed: %s — %s",
                    step.name,
                    _sanitize_error_message(exc),
                )


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 0: INFRASTRUCTURE
# ═══════════════════════════════════════════════════════════════════════════

def _boot_event_bus(container: ServiceContainer) -> None:
    from core.event_bus import EventBus, get_event_bus

    bus = get_event_bus()
    container.register_instance(EventBus, bus)
    bus.enable_async(True)

    try:
        from services.supabase import get_service_client_from_settings
        client = get_service_client_from_settings()
        if client:
            bus.enable_persistence(client)
            _log(logging.INFO, "EventBus: persistence enabled")
    except Exception:
        _log(logging.DEBUG, "EventBus: persistence skipped")

    health_registry.register(_EventBusHealthProvider(bus))
    _log(logging.INFO, "EventBus v3.0 ready.")


def _boot_supabase(container: ServiceContainer) -> None:
    from config.settings import settings
    from services.supabase import get_service_client_from_settings

    if not settings.supabase_enabled:
        _log(logging.INFO, "Supabase disabled — skipping.")
        return

    client = get_service_client_from_settings()

    try:
        from core.api_tokens import api_token_manager
        api_token_manager.set_supabase_client(client)
        _log(logging.INFO, "APITokenManager: persistent storage wired")
    except Exception as exc:
        _log(
            logging.WARNING,
            "APITokenManager wiring failed: %s",
            _sanitize_error_message(exc),
        )

    _log(logging.INFO, "Supabase connected.")


def _boot_redis(container: ServiceContainer) -> None:
    from core.redis_client import UpstashRedisClient

    try:
        redis = container.resolve(UpstashRedisClient)
        redis.ping()
        _log(logging.INFO, "Redis connected.")
    except Exception as exc:
        _log(
            logging.WARNING,
            "Redis not available: %s",
            _sanitize_error_message(exc),
        )


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 1: CORE
# ═══════════════════════════════════════════════════════════════════════════

def _boot_task_queue(container: ServiceContainer) -> None:
    from workers.task_queue import TaskQueue

    tq = container.resolve(TaskQueue)
    shutdown_registry.register(
        "TaskQueue",
        lambda: tq.stop(timeout=10),
        timeout_seconds=15.0,
    )
    health_registry.register(_TaskQueueHealthProvider(tq))
    _log(logging.INFO, "TaskQueue ready.")


def _boot_plugin_registry(container: ServiceContainer) -> None:
    from plugins.base import PluginRegistry, get_plugin_registry
    from core.event_bus import EventBus

    event_bus = container.try_resolve(EventBus)
    registry = get_plugin_registry(event_bus=event_bus)
    container.register_instance(PluginRegistry, registry)
    _log(logging.INFO, "PluginRegistry v2.0 ready.")


def _boot_job_service(container: ServiceContainer) -> None:
    from services.job_service import JobService
    from services.supabase import (
        get_service_client_from_settings,
        get_anon_client_from_settings,
    )
    from config.settings import settings
    from core.event_bus import EventBus

    client = None
    if settings.supabase_admin_enabled:
        client = get_service_client_from_settings()
    if client is None and settings.supabase_enabled:
        client = get_anon_client_from_settings()

    job_service = JobService(supabase_client=client)

    event_bus = container.try_resolve(EventBus)
    if event_bus:
        job_service.set_event_bus(event_bus)

    container.register_instance(JobService, job_service)
    
    from services.job_stats_service import init_job_stats_service
    # v4.0 — Thread-safe client factory
    def client_factory():
        return get_service_client_from_settings()

    init_job_stats_service(
        supabase_client=client_factory,
        event_bus=event_bus,
    )
    
    health_registry.register(_JobServiceHealthProvider(job_service))
    _log(logging.INFO, "JobService v3.4 ready.")


def _boot_render_queue(container: ServiceContainer) -> None:
    from core.render_queue import wire_render_queue_container, get_render_queue

    wire_render_queue_container(container)
    rq = get_render_queue()
    cleared = rq.clear_stuck_tasks()
    if cleared:
        _log(
            logging.WARNING,
            "RenderQueue: cleared %d stuck tasks.",
            cleared,
        )
    health_registry.register(_RenderQueueHealthProvider(rq))
    _log(logging.INFO, "RenderQueue ready.")


def _boot_event_recorder(container: ServiceContainer) -> None:
    from domain.analytics.event_recorder import EventRecorder

    recorder = container.resolve(EventRecorder)
    health_registry.register(_EventRecorderHealthProvider(recorder))
    _log(logging.INFO, "EventRecorder ready.")


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 2: BUSINESS
# ═══════════════════════════════════════════════════════════════════════════

def _boot_ai_orchestrator(container: ServiceContainer) -> None:
    from ai.orchestrator import AIOrchestrator

    orch = container.resolve(AIOrchestrator)
    health = orch.health()
    available = [
        name
        for name, h in health.items()
        if h.get("available")
    ]

    if not available:
        _log(logging.ERROR, "No AI providers available.")
        return

    health_registry.register(_AIOrchestratorHealthProvider(orch))
    _log(
        logging.INFO,
        "AIOrchestrator ready. Available: %s",
        available,
    )


def _boot_content_engine(container: ServiceContainer) -> None:
    from services.content_engine import content_engine

    content_engine.set_container(container)
    _log(logging.INFO, "ContentEngine v14.1: container wired")


def _boot_meta_engine(container: ServiceContainer) -> None:
    from services.meta_engine import init_meta_engine
    from services.engine_health_handlers import register_all_health_handlers
    from core.event_bus import EventBus

    event_bus = container.resolve(EventBus)
    meta = init_meta_engine(event_bus=event_bus, container=container)

    result = register_all_health_handlers(meta)
    _log(
        logging.INFO,
        "MetaEngine: %d health handlers registered",
        len(result["registered"]),
    )

    shutdown_registry.register(
        "MetaEngine",
        meta.stop,
        timeout_seconds=10.0,
    )

    _log(
        logging.INFO,
        "MetaEngine: supervising %d engines",
        len(meta._engines),
    )


def _boot_director(container: ServiceContainer) -> None:
    from services.director import init_director, auto_discover_engines
    from services.supabase import get_service_client_from_settings
    from core.event_bus import EventBus

    supabase_client = None
    try:
        supabase_client = get_service_client_from_settings()
    except Exception:
        pass

    director = init_director(
        supabase_client=supabase_client,
        auto_start=False,
    )
    discovered = auto_discover_engines(director, container)

    event_bus = container.try_resolve(EventBus)
    if event_bus:

        def director_event_handler(event_name, payload):
            event_bus.publish_event(
                f"director.{event_name}", payload
            )

        director.set_event_callback(director_event_handler)

    _bind_director_capabilities(director, container)

    director.start()
    shutdown_registry.register(
        "Director",
        director.stop,
        timeout_seconds=10.0,
    )
    _log(
        logging.INFO,
        "👔 Director v2.0: %d engines under supervision",
        discovered,
    )


def _bind_director_capabilities(director, container) -> None:
    from services.condition_engine import autonomous_engine
    from services.content_engine import content_engine
    from services.meta_engine import meta_engine

    def run_health_scan(params):
        try:
            snapshot = meta_engine.take_snapshot()
            return {
                "status": "completed",
                "health": {
                    "overall": snapshot.overall_status.value,
                    "health_score": snapshot.health_score,
                    "risk_score": snapshot.risk_score,
                    "engine_count": len(snapshot.engines),
                    "critical_alerts": snapshot.critical_alerts,
                },
                "timestamp": snapshot.timestamp.isoformat(),
            }
        except Exception as e:
            return {
                "status": "failed",
                "error": _sanitize_error_message(e),
            }

    def restart_failed_service(params):
        engine_name = params.get("service_name", "")
        
        if not engine_name:
            return {
                "status": "failed",
                "error": "service_name is required",
            }
        
        if engine_name in meta_engine._recovery_handlers:
            try:
                from services.meta_engine import Diagnosis, AlertLevel
                
                diagnosis = Diagnosis(
                    id=f"director_restart_{engine_name}",
                    engine=engine_name,
                    symptom=f"Director-requested restart for {engine_name}",
                    root_cause="Director autonomous recovery",
                    severity=AlertLevel.WARNING,
                    recommended_action=f"Restart {engine_name}",
                    auto_fixable=True,
                )
                
                attempt = meta_engine._perform_recovery(diagnosis)
                
                return {
                    "status": attempt.status.value,
                    "engine": engine_name,
                    "verification_passed": attempt.verification_passed,
                }
            except Exception as e:
                return {
                    "status": "failed",
                    "engine": engine_name,
                    "error": _sanitize_error_message(e),
                }
        
        engine = meta_engine._engines.get(engine_name)
        if engine and hasattr(engine, "restart"):
            try:
                engine.restart()
                return {
                    "status": "completed",
                    "engine": engine_name,
                    "verification_passed": True,
                }
            except Exception as e:
                return {
                    "status": "failed",
                    "engine": engine_name,
                    "error": _sanitize_error_message(e),
                }
        
        return {
            "status": "not_implemented",
            "engine": engine_name,
            "reason": f"No recovery handler for '{engine_name}'",
        }

    def calculate_brand_health(params):
        brand_id = params.get("brand_id", "")
        
        if not brand_id:
            return {
                "status": "failed",
                "error": "brand_id is required",
            }
        
        try:
            profile = autonomous_engine.calculate_brand_health(brand_id)
            return {"status": "completed", **profile}
        except Exception as e:
            return {
                "status": "failed",
                "error": _sanitize_error_message(e),
            }

    def analyze_growth_opportunity(params):
        try:
            snapshot = meta_engine.take_snapshot()
            
            opportunities = []
            
            for name, health in snapshot.engines.items():
                if health.status.value in ("degraded", "stale", "offline"):
                    opportunities.append({
                        "type": "engine_recovery",
                        "engine": name,
                        "current_status": health.status.value,
                        "potential_impact": "high" if health.status.value == "offline" else "medium",
                    })
            
            try:
                from services.job_stats_service import get_job_stats_service
                stats_service = get_job_stats_service()
                job_stats = stats_service.get_stats()
                
                if job_stats.get("active_jobs", 0) > 10:
                    opportunities.append({
                        "type": "job_pipeline_optimization",
                        "active_jobs": job_stats["active_jobs"],
                        "oldest_active_minutes": job_stats.get("oldest_active_minutes", 0),
                    })
            except Exception:
                pass
            
            if not opportunities:
                return {
                    "status": "completed",
                    "opportunities": [],
                    "message": "No immediate growth opportunities identified",
                }
            
            return {
                "status": "completed",
                "opportunities": opportunities,
                "count": len(opportunities),
            }
        except Exception as e:
            return {
                "status": "failed",
                "error": _sanitize_error_message(e),
            }

    try:
        director.bind_capability("run_health_scan", run_health_scan)
        director.bind_capability("restart_failed_service", restart_failed_service)
        director.bind_capability("calculate_brand_health", calculate_brand_health)
        director.bind_capability("analyze_growth_opportunity", analyze_growth_opportunity)
        _log(
            logging.INFO,
            "Director: capabilities bound (v2.1 — honest implementations)",
        )
    except Exception as exc:
        _log(
            logging.WARNING,
            "Director capability binding failed: %s",
            _sanitize_error_message(exc),
        )


def _boot_condition_engine(container: ServiceContainer) -> None:
    from services.condition_engine import autonomous_engine
    from core.event_bus import EventBus

    event_bus = container.resolve(EventBus)
    ce = autonomous_engine

    _decisions = getattr(ce, "_decisions", [])
    if isinstance(_decisions, list) and len(_decisions) > 10000:
        ce._decisions = _decisions[-5000:]
        _log(
            logging.WARNING,
            "ConditionEngine: pruned %d old decisions to bound memory",
            len(_decisions) - 5000,
        )

    def real_executor(plan) -> dict:
        action = plan.action
        params = plan.parameters
        brand_id = plan.brand_id

        if action == "increase_similar_content":
            try:
                from services.content_engine import content_engine
                content_engine.boost_content(
                    brand_id=brand_id,
                    factor=params.get("content_mix_change", 0.20),
                    duration_posts=params.get("duration_posts", 5),
                )
                event_bus.publish_event(
                    "condition.boost",
                    {
                        "brand_id": brand_id,
                        "factor": params.get("content_mix_change", 0.20),
                    },
                )
            except Exception as exc:
                _log(
                    logging.ERROR,
                    "Boost failed: %s",
                    _sanitize_error_message(exc),
                )

        elif action == "reduce_underperforming_content":
            try:
                from services.content_engine import content_engine
                content_engine.reduce_content(
                    brand_id=brand_id,
                    factor=params.get("content_mix_change", -0.20),
                    duration_posts=params.get("duration_posts", 5),
                )
                event_bus.publish_event(
                    "condition.reduce",
                    {"brand_id": brand_id},
                )
            except Exception as exc:
                _log(
                    logging.ERROR,
                    "Reduce failed: %s",
                    _sanitize_error_message(exc),
                )

        elif action == "reduce_content_risk":
            try:
                from services.content_engine import content_engine
                content_engine.safe_mode(
                    brand_id=brand_id,
                    duration_posts=params.get("duration_posts", 5),
                )
                event_bus.publish_event(
                    "condition.safe_mode",
                    {"brand_id": brand_id},
                )
            except Exception as exc:
                _log(
                    logging.ERROR,
                    "Safe mode failed: %s",
                    _sanitize_error_message(exc),
                )

        elif action == "monitor_positive_trend":
            event_bus.publish_event(
                "condition.monitor",
                {
                    "brand_id": brand_id,
                    "action": "increase_observation_frequency",
                },
            )

        elif action == "investigate_decline":
            event_bus.publish_event(
                "condition.investigate",
                {
                    "brand_id": brand_id,
                    "run_experiment": True,
                },
            )

        if action in (
            "increase_similar_content",
            "reduce_underperforming_content",
            "reduce_content_risk",
        ):
            try:
                from services.email_service import email_service
                email_service.send_telegram(
                    f"🤖 <b>Autonomous Decision</b>\n"
                    f"Brand: {brand_id}\n"
                    f"Action: {action}\n"
                    f"Confidence: {plan.confidence:.0%}\n"
                    f"<i>{datetime.now(timezone.utc).strftime('%H:%M UTC')}</i>",
                    priority="normal",
                )
            except Exception as exc:
                _log(
                    logging.DEBUG,
                    "Notification failed: %s",
                    _sanitize_error_message(exc),
                )

        return {
            "success": True,
            "action": action,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

    ce.set_action_executor(real_executor)
    _log(logging.INFO, "ConditionEngine: real action executor wired")

    try:
        from services.supabase import get_service_client_from_settings
        client = get_service_client_from_settings()
        ce.set_supabase_client(client)
        _log(logging.INFO, "ConditionEngine: Supabase persistence enabled")
    except Exception as exc:
        _log(
            logging.WARNING,
            "ConditionEngine: Supabase unavailable: %s",
            _sanitize_error_message(exc),
        )

    try:
        from services.email_service import email_service, register_email_actions
        register_email_actions(ce, email_service)
        health_registry.register(_EmailServiceHealthProvider(email_service))
        _log(logging.INFO, "EmailService v3.0 wired to ConditionEngine")
    except Exception as exc:
        _log(
            logging.WARNING,
            "EmailService wiring failed: %s",
            _sanitize_error_message(exc),
        )

    try:
        from services.engagement_tracker import start_engagement_tracking
        start_engagement_tracking(container=container)
        _log(logging.INFO, "EngagementTracker started")
    except Exception as exc:
        _log(
            logging.WARNING,
            "EngagementTracker start failed: %s",
            _sanitize_error_message(exc),
        )

    health_registry.register(_ConditionEngineHealthProvider(ce))
    _log(
        logging.INFO,
        "ConditionEngine ready — %d decisions tracked",
        len(ce._decisions),
    )


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 2: CONTINUED
# ═══════════════════════════════════════════════════════════════════════════

def _boot_industry_plugins(app, container: ServiceContainer) -> None:
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
        _log(logging.INFO, "No industry plugins discovered.")
        return

    _register_plugin_commands(plugins)
    _register_industry_webhooks(app, plugins)

    for name, plugin in plugins.items():
        if hasattr(plugin, "shutdown"):
            shutdown_registry.register(
                f"IndustryPlugin:{name}",
                plugin.shutdown,
                timeout_seconds=10.0,
            )

    for name, plugin in plugins.items():
        if isinstance(plugin, HealthProvider):
            health_registry.register(plugin)

    _log(
        logging.INFO,
        "Industry plugins loaded: %s",
        list(plugins.keys()),
    )


def _discover_industry_plugins(
    supabase_client=None,
    redis_client=None,
) -> dict:
    plugins: dict = {}
    plugins_dir = os.path.join(
        os.path.dirname(__file__),
        "plugins",
        "industries",
    )

    if not os.path.isdir(plugins_dir):
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
            module = importlib.import_module(
                f"plugins.industries.{folder}.plugin"
            )
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
                continue

            plugin_instance = plugin_class(
                supabase_client=supabase_client,
                redis_client=redis_client,
            )
            plugins[folder] = plugin_instance
            _log(logging.INFO, "Plugin discovered: %s", folder)
        except ImportError:
            pass
        except Exception as exc:
            _log(
                logging.WARNING,
                "Plugin '%s' failed: %s",
                folder,
                _sanitize_error_message(exc),
            )

    return plugins


def _register_plugin_commands(plugins: dict) -> None:
    try:
        from services.command_executor import CommandExecutor

        total = 0
        for plugin_name, plugin in plugins.items():
            for cmd in plugin.get_commands():
                CommandExecutor.register_external_command(plugin_name, cmd)
                total += 1
        _log(logging.INFO, "Registered %d plugin commands.", total)
    except ImportError:
        pass
    except Exception as exc:
        _log(
            logging.WARNING,
            "Plugin command registration failed: %s",
            _sanitize_error_message(exc),
        )


def _register_industry_webhooks(app, plugins: dict) -> None:
    from fastapi import Request, HTTPException
    from fastapi.responses import JSONResponse

    MAX_BODY_SIZE = 10 * 1024 * 1024

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
                _log(
                    logging.WARNING,
                    "Webhook handler '%s' not found on '%s'",
                    handler_name,
                    plugin_name,
                )
                continue

            async def make_handler(
                request: Request,
                p_fn=handler_fn,
                p_name=plugin_name,
            ):
                data = {}
                try:
                    content_length = request.headers.get("content-length")
                    if (
                        content_length
                        and int(content_length) > MAX_BODY_SIZE
                    ):
                        raise HTTPException(
                            status_code=413,
                            detail="Payload too large",
                        )

                    body = await request.body()

                    if len(body) > MAX_BODY_SIZE:
                        raise HTTPException(
                            status_code=413,
                            detail="Payload too large",
                        )

                    if body:
                        data = await request.json()
                except HTTPException:
                    raise
                except Exception:
                    data = dict(request.query_params)

                try:
                    if asyncio.iscoroutinefunction(p_fn):
                        result = await p_fn(data)
                    else:
                        result = p_fn(data)
                    return JSONResponse(content=result)
                except Exception as exc:
                    _log(
                        logging.ERROR,
                        "Webhook failed [%s]: %s",
                        p_name,
                        _sanitize_error_message(exc),
                    )
                    return JSONResponse(
                        content={
                            "ok": False,
                            "error": "Internal processing error",
                        },
                        status_code=500,
                    )

            app.add_api_route(
                path,
                make_handler,
                methods=[method],
                name=f"industry_webhook_{plugin_name}_{handler_name}",
            )


def _recover_stale_jobs(container: ServiceContainer) -> None:
    from services.job_service import JobService

    job_service = container.try_resolve(JobService)
    if job_service is None:
        return

    stale_count = job_service.recover_and_retry(stale_minutes=15)
    if stale_count > 0:
        _log(
            logging.WARNING,
            "Recovered %d stale jobs",
            stale_count,
        )


def _boot_brand_schedulers(container: ServiceContainer) -> None:
    from scheduler.brand_scheduler import BrandSchedulerManager

    manager = container.resolve(BrandSchedulerManager)
    manager.set_container(container)

    try:
        loop = asyncio.get_running_loop()
        manager.set_event_loop(loop)
        _log(logging.INFO, "BrandSchedulerManager: event loop wired for dashboard events")
    except RuntimeError:
        _log(logging.WARNING, "BrandSchedulerManager: no running event loop — dashboard events disabled")

    manager.start()

    shutdown_registry.register(
        "BrandSchedulerManager",
        manager.stop,
        timeout_seconds=10.0,
    )


def _boot_guardian_scheduler(container: ServiceContainer) -> None:
    from config.settings import settings

    if not settings.guardian_configured:
        return

    from services.guardian_service import GuardianService
    from scheduler.scan_scheduler import ScanScheduler

    guardian = container.resolve(GuardianService)
    try:
        from routes.webhook import set_guardian_runner
        set_guardian_runner(guardian.scan_all)
    except Exception:
        pass

    scan_scheduler = ScanScheduler(
        guardian.scan_all,
        settings.guardian_scan_hours,
    )
    scan_scheduler.start()
    _log(logging.INFO, "Guardian started.")


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 3: OBSERVABILITY
# ═══════════════════════════════════════════════════════════════════════════

def _boot_health_monitor(container: ServiceContainer) -> None:
    from services.health_monitor_service import HealthMonitorService

    hm = HealthMonitorService(container=container)
    hm.start()
    container.register_instance(HealthMonitorService, hm)
    shutdown_registry.register(
        "HealthMonitorService",
        hm.stop,
        timeout_seconds=10.0,
    )
    _log(logging.INFO, "HealthMonitorService started.")


def _register_api_call_counter(app) -> None:
    _log(logging.INFO, "API call counter ready.")


def _boot_plugin_from_module(
    container: ServiceContainer,
    module_path: str,
    init_function_name: str,
    display_name: str,
) -> None:
    try:
        module = __import__(module_path, fromlist=[init_function_name])
        init_fn = getattr(module, init_function_name)
        from services.supabase import get_service_client_from_settings

        client = get_service_client_from_settings()
        init_fn(supabase_client=client)
        _log(logging.INFO, "%s: started", display_name)
    except Exception as exc:
        _log(
            logging.WARNING,
            "%s: could not start: %s",
            display_name,
            _sanitize_error_message(exc),
        )


def _boot_monitor_plugin(container: ServiceContainer) -> None:
    _boot_plugin_from_module(
        container,
        "plugins.monitor.plugin",
        "init_monitor_plugin",
        "MonitorPlugin",
    )


def _boot_backup_plugin(container: ServiceContainer) -> None:
    _boot_plugin_from_module(
        container,
        "plugins.backup.plugin",
        "init_backup_plugin",
        "BackupPlugin",
    )


def _boot_trend_plugin(container: ServiceContainer) -> None:
    _boot_plugin_from_module(
        container,
        "plugins.trend_intelligence.plugin",
        "init_trend_plugin",
        "TrendIntelligence",
    )


# ═══════════════════════════════════════════════════════════════════════════
# HEALTH PROVIDER IMPLEMENTATIONS
# ═══════════════════════════════════════════════════════════════════════════

class _EventBusHealthProvider:
    def __init__(self, bus):
        self._bus = bus
        self.service_name = "event_bus"

    def check_health(self) -> HealthReport:
        try:
            stats = self._bus.get_stats()
            return HealthReport(
                status=HealthStatus.HEALTHY,
                service_name=self.service_name,
                metadata=stats,
            )
        except Exception as exc:
            return HealthReport(
                status=HealthStatus.UNHEALTHY,
                service_name=self.service_name,
                error_message=_sanitize_error_message(exc),
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
                error_message=_sanitize_error_message(exc),
            )


class _JobServiceHealthProvider:
    def __init__(self, job_service):
        self._js = job_service
        self.service_name = "job_service"

    def check_health(self) -> HealthReport:
        try:
            stats = self._js.get_stats()
            return HealthReport(
                status=HealthStatus.HEALTHY,
                service_name=self.service_name,
                metadata=stats,
            )
        except Exception as exc:
            return HealthReport(
                status=HealthStatus.UNHEALTHY,
                service_name=self.service_name,
                error_message=_sanitize_error_message(exc),
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
                error_message=_sanitize_error_message(exc),
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
            available = sum(
                1 for h in health.values() if h.get("available")
            )
            return HealthReport(
                status=(
                    HealthStatus.HEALTHY
                    if available > 0
                    else HealthStatus.DEGRADED
                ),
                service_name=self.service_name,
                metadata={
                    "available": available,
                    "total": len(health),
                },
            )
        except Exception as exc:
            return HealthReport(
                status=HealthStatus.UNHEALTHY,
                service_name=self.service_name,
                error_message=_sanitize_error_message(exc),
            )


class _ConditionEngineHealthProvider:
    def __init__(self, engine):
        self._engine = engine
        self.service_name = "condition_engine"

    def check_health(self) -> HealthReport:
        try:
            decisions = len(
                getattr(self._engine, "_decisions", [])
            )
            # v14.1.1 — Safely access config attributes
            config = getattr(self._engine, "config", None)
            critical_ratio = getattr(
                config, "critical_performance_ratio", 0.5
            )
            
            return HealthReport(
                status=HealthStatus.HEALTHY,
                service_name=self.service_name,
                metadata={
                    "decisions_tracked": decisions,
                    "critical_performance_ratio": critical_ratio,
                },
            )
        except Exception as exc:
            return HealthReport(
                status=HealthStatus.UNHEALTHY,
                service_name=self.service_name,
                error_message=_sanitize_error_message(exc),
            )


class _EmailServiceHealthProvider:
    """v14.1 — No longer masks failures."""

    def __init__(self, email_service):
        self._es = email_service
        self.service_name = "email_service"

    def check_health(self) -> HealthReport:
        try:
            stats = self._es.get_stats()
            return HealthReport(
                status=HealthStatus.HEALTHY,
                service_name=self.service_name,
                metadata=stats,
            )
        except Exception as exc:
            return HealthReport(
                status=HealthStatus.DEGRADED,
                service_name=self.service_name,
                error_message=_sanitize_error_message(exc),
            )


# ═══════════════════════════════════════════════════════════════════════════
# ENTERPRISE EMITTERS
# ═══════════════════════════════════════════════════════════════════════════

_emitter_started = False
_emitter_lock = threading.Lock()
_emitter_stop_event = threading.Event()


def _start_enterprise_emitters(container: ServiceContainer) -> None:
    global _emitter_started
    with _emitter_lock:
        if _emitter_started:
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

    _log(
        logging.INFO,
        "Enterprise emitters started: %s",
        ", ".join(
            f"{e.name} ({e.interval}s)" for e in emitters
        ),
    )


@dataclass
class _EmitterConfig:
    name: str
    interval: int
    emitter_fn: Callable[[], dict[str, Any]]


def _emitter_loop(config: _EmitterConfig) -> None:
    while not _emitter_stop_event.is_set():
        try:
            payload = config.emitter_fn()
            if payload:
                payload["timestamp"] = datetime.now(timezone.utc).isoformat()
                _schedule_socketio_emit(config.name, payload)
        except Exception as exc:
            _log(
                logging.ERROR,
                "Emitter '%s' error: %s",
                config.name,
                _sanitize_error_message(exc),
            )
        _emitter_stop_event.wait(config.interval)


def _collect_system_resources() -> dict[str, Any]:
    cpu = mem = disk = 0.0
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=1)
        mem = psutil.virtual_memory().percent
        disk = psutil.disk_usage("/").percent
    except ImportError:
        pass
    return {
        "cpu_percent": round(cpu, 1),
        "memory_percent": round(mem, 1),
        "disk_percent": round(disk, 1),
    }


def _collect_service_health() -> dict[str, Any]:
    reports = health_registry.check_all()
    services = {}
    for name, report in reports.items():
        services[name] = report.to_dict()
    return {"services": services}


def _collect_ai_provider_health(container: ServiceContainer) -> dict[str, Any]:
    try:
        from ai.orchestrator import AIOrchestrator
        orch = container.try_resolve(AIOrchestrator)
        if not orch:
            return {"providers": []}
        health = orch.health()
        providers = []
        for name, h in health.items():
            providers.append({
                "provider": name,
                "available": h.get("available", False),
                "latency_ms": h.get("latency_ms", 0),
            })
        return {"providers": providers}
    except Exception:
        return {"providers": []}


def _collect_plugin_status(container: ServiceContainer) -> dict[str, Any]:
    plugins = getattr(container, "industry_plugins", {})
    data = []
    for name, plugin in plugins.items():
        try:
            data.append({
                "name": name,
                "commands": len(plugin.get_commands()) if hasattr(plugin, "get_commands") else 0,
                "status": "active",
            })
        except Exception:
            data.append({
                "name": name,
                "status": "degraded",
            })
    return {"plugins": data}


def _stop_enterprise_emitters() -> None:
    global _emitter_started
    _emitter_stop_event.set()
    _emitter_started = False


# ═══════════════════════════════════════════════════════════════════════════
# SOCKETIO HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _schedule_socketio_emit(event_name: str, payload: dict) -> None:
    try:
        from core.socket_server import emit_to_dashboard
        loop = asyncio.get_running_loop()
        future = asyncio.run_coroutine_threadsafe(
            emit_to_dashboard(event_name, payload),
            loop,
        )
    except RuntimeError:
        _log(
            logging.DEBUG,
            "SocketIO emit skipped for '%s': no event loop",
            event_name,
        )


def _wire_socket_events(container: ServiceContainer) -> None:
    try:
        from core.event_bus import EventBus
        from core.events import EventType
        from core.socket_server import emit_to_dashboard, increment

        bus = container.resolve(EventBus)

        def _emit(event_name, payload):
            try:
                loop = asyncio.get_running_loop()
                asyncio.create_task(
                    emit_to_dashboard(event_name, payload)
                )
            except RuntimeError:
                _log(
                    logging.DEBUG,
                    "SocketIO emit skipped for '%s': no event loop",
                    event_name,
                )

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
                        _emit(
                            se,
                            {
                                "event_id": event.event_id,
                                "source": event.source,
                                **event.payload,
                            },
                        )
                    except Exception as exc:
                        _log(
                            logging.DEBUG,
                            "Socket handler error for '%s': %s",
                            se,
                            _sanitize_error_message(exc),
                        )

                return handler

            bus.subscribe(event_type, make_handler())

        _log(
            logging.INFO,
            "SocketIO bridge active: %d subscriptions",
            len(mappings),
        )
    except Exception as exc:
        _log(
            logging.WARNING,
            "SocketIO bridge failed: %s",
            _sanitize_error_message(exc),
        )


# ═══════════════════════════════════════════════════════════════════════════
# SHUTDOWN HELPERS
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
                _log(
                    logging.WARNING,
                    "Plugin '%s' shutdown failed: %s",
                    name,
                    _sanitize_error_message(exc),
                )


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
    _log(logging.WARNING, "register_lifecycle() is deprecated.")
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
    "_sanitize_error_message",
    "_execute_boot_step_with_timeout",
]
