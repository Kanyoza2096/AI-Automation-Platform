"""
Kanyoza Systems AI Platform v16.7 — Lifecycle Management (Async-Native).

v16.7 CHANGES:
  - ADDED: Auto-registration of ALL services with Meta Engine
  - ADDED: Centralized service registry integration
  - FIXED: No more manual service registration
  - All services now auto-discover and register

v16.6 CHANGES:
  - FIXED: probe failures now return DEGRADED (not UNHEALTHY)
  - FIXED: async check exceptions return DEGRADED (not UNHEALTHY)
  - All health check probe failures = DEGRADED with confidence=low
  - Only explicit offline/failed/stopped status = UNHEALTHY
  - Reduced false positive alerts to near zero
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
import os
import re
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from functools import partial
from typing import Any, Callable, Optional, Protocol, runtime_checkable

from plugins.base import PluginRegistry
from service_container import ServiceContainer

logger = logging.getLogger("kanyoza.lifecycle")


# ─────────────────────────────────────────────────────────────────────────────
# Exceptions
# ─────────────────────────────────────────────────────────────────────────────

class BootFailure(Exception):
    """Raised when a critical boot phase fails and the process should exit."""


# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────

def _sanitize_error_message(exc: Exception, max_len: int = 200) -> str:
    raw = str(exc)
    raw = re.sub(r'File "[^"]*"', "File [redacted]", raw)
    raw = re.sub(r"/[^\s]*\.py", "[path_redacted]", raw)
    raw = re.sub(r"0x[0-9a-fA-F]+", "[addr]", raw)
    return raw[:max_len]


async def _run_in_thread(func: Callable, *args, **kwargs) -> Any:
    """Run a blocking callable in a thread using the current event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(func, *args, **kwargs) if args or kwargs else func)


# ─────────────────────────────────────────────────────────────────────────────
# Health Model
# ─────────────────────────────────────────────────────────────────────────────

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
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    correlation_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.service_name,
            "status": self.status.value,
            "latency": round(self.latency_ms, 2),
            "lastChecked": self.checked_at.isoformat(),
            "lastSuccess": self.last_success.isoformat() if self.last_success else None,
            "error": self.error_message,
            "correlationId": self.correlation_id,
            **self.metadata,
        }


@runtime_checkable
class HealthProvider(Protocol):
    @property
    def service_name(self) -> str: ...
    def check_health(self) -> HealthReport: ...


@runtime_checkable
class AsyncHealthProvider(Protocol):
    @property
    def service_name(self) -> str: ...
    async def check_health_async(self) -> HealthReport: ...


# ─────────────────────────────────────────────────────────────────────────────
# Boot Model
# ─────────────────────────────────────────────────────────────────────────────

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
    action: Callable[[], Any]
    rollback: Callable[[], Any] | None = None
    timeout_seconds: float = 30.0


# ─────────────────────────────────────────────────────────────────────────────
# Service Health Registry
# ─────────────────────────────────────────────────────────────────────────────

class ServiceHealthRegistry:
    def __init__(self, max_concurrency: int = 16) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self._providers: dict[str, Any] = {}
        self._max_concurrency = max_concurrency
        self._lock = asyncio.Lock()
        self._startup_complete = asyncio.Event()

    async def register(self, provider: Any) -> None:
        async with self._lock:
            self._providers[provider.service_name] = provider

    async def unregister(self, service_name: str) -> None:
        async with self._lock:
            self._providers.pop(service_name, None)

    async def get_all(self) -> list[Any]:
        async with self._lock:
            return list(self._providers.values())

    async def get(self, service_name: str) -> Any:
        async with self._lock:
            return self._providers.get(service_name)

    async def get_provider_count(self) -> int:
        async with self._lock:
            return len(self._providers)

    def mark_startup_complete(self) -> None:
        self._startup_complete.set()

    async def check_all(self, timeout_per_service: float = 5.0) -> dict[str, HealthReport]:
        providers = await self.get_all()
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def check_provider(provider: Any) -> tuple[str, HealthReport]:
            service_name = getattr(provider, "service_name", "unknown")
            start = time.monotonic()
            async with semaphore:
                try:
                    report = await self._check_single_provider(provider, timeout_per_service)
                    report.latency_ms = (time.monotonic() - start) * 1000
                    return service_name, report
                except asyncio.TimeoutError:
                    return service_name, HealthReport(
                        status=HealthStatus.DEGRADED,
                        service_name=service_name,
                        error_message=f"Timeout after {timeout_per_service}s",
                        metadata={"note": "Health probe timed out", "confidence": "low"},
                    )
                except Exception as exc:
                    logger.debug(
                        "Unexpected error while probing %s:\n%s",
                        service_name,
                        traceback.format_exc(),
                    )
                    return service_name, HealthReport(
                        status=HealthStatus.DEGRADED,
                        service_name=service_name,
                        error_message=_sanitize_error_message(exc),
                        metadata={"note": "Health probe errored", "confidence": "low"},
                    )

        checked = await asyncio.gather(*(check_provider(p) for p in providers))
        return dict(checked)

    async def _check_single_provider(self, provider: Any, timeout: float) -> HealthReport:
        service_name = getattr(provider, "service_name", "unknown")

        # 1. Prefer modern async check
        if hasattr(provider, "check_health_async"):
            return await self._run_health_check(
                provider.check_health_async,
                service_name,
                timeout,
                is_async=True,
            )

        # 2. Prefer sync health_check (common in many services)
        if hasattr(provider, "health_check"):
            return await self._run_health_check(
                provider.health_check,
                service_name,
                timeout,
                is_async=False,
            )

        # 3. Legacy check_health
        if hasattr(provider, "check_health"):
            return await self._run_health_check(
                provider.check_health,
                service_name,
                timeout,
                is_async=False,
            )

        return HealthReport(
            HealthStatus.UNKNOWN,
            service_name,
            error_message="No health check method found",
        )

    async def _run_health_check(
        self,
        method: Callable,
        service_name: str,
        timeout: float,
        *,
        is_async: bool,
    ) -> HealthReport:
        try:
            if is_async:
                raw = await asyncio.wait_for(method(), timeout=timeout)
            else:
                # Never invoke synchronous health probes on the event loop.
                # Run the initial call in a worker thread; some legacy sync
                # adapters return an awaitable, which is then awaited here.
                raw = await asyncio.wait_for(
                    _run_in_thread(method),
                    timeout=timeout,
                )
                if inspect.isawaitable(raw):
                    raw = await asyncio.wait_for(raw, timeout=timeout)

            return self._normalize_health_result(raw, service_name)

        except asyncio.TimeoutError:
            return HealthReport(
                HealthStatus.DEGRADED,
                service_name,
                error_message=f"Timeout after {timeout}s",
                metadata={"note": "probe_timeout", "confidence": "low"},
            )
        except Exception as exc:
            logger.debug(
                "Health probe failed for %s:\n%s",
                service_name,
                traceback.format_exc(),
            )
            return HealthReport(
                HealthStatus.DEGRADED,
                service_name,
                error_message=_sanitize_error_message(exc),
                metadata={"note": "probe_failed", "confidence": "low"},
            )

    def _normalize_health_result(self, raw: Any, service_name: str) -> HealthReport:
        if isinstance(raw, HealthReport):
            return raw

        if isinstance(raw, dict):
            status_str = str(raw.get("status", "healthy")).lower()
            if status_str in ("healthy", "ok", "nominal", "ready", "running", "idle"):
                return HealthReport(HealthStatus.HEALTHY, service_name, metadata=raw)
            if status_str in ("unhealthy", "offline", "failed", "critical", "stopped"):
                return HealthReport(HealthStatus.UNHEALTHY, service_name, metadata=raw)
            return HealthReport(HealthStatus.DEGRADED, service_name, metadata=raw)

        if raw is None:
            return HealthReport(
                HealthStatus.DEGRADED,
                service_name,
                metadata={"note": "health check returned None", "confidence": "low"},
            )

        # Unknown return type – treat as degraded with the raw value for debugging
        return HealthReport(
            HealthStatus.DEGRADED,
            service_name,
            metadata={"result": str(raw)},
        )


health_registry = ServiceHealthRegistry()


# ─────────────────────────────────────────────────────────────────────────────
# Shutdown Registry
# ─────────────────────────────────────────────────────────────────────────────

class ShutdownRegistry:
    def __init__(self) -> None:
        self._hooks: list[tuple[str, Callable, float]] = []
        self._lock = asyncio.Lock()
        self._executing = False

    async def register(self, name: str, hook: Callable, timeout_seconds: float = 10.0) -> None:
        async with self._lock:
            if self._executing:
                logger.debug("Ignoring late shutdown registration for '%s'", name)
                return
            self._hooks.append((name, hook, timeout_seconds))

    async def execute_all(self) -> None:
        async with self._lock:
            if self._executing:
                return
            self._executing = True
            hooks = list(reversed(self._hooks))
            self._hooks.clear()

        for name, hook, timeout in hooks:
            logger.info("Shutting down: %s ...", name)
            try:
                result = hook()
                if asyncio.iscoroutine(result) or asyncio.isfuture(result):
                    await asyncio.wait_for(result, timeout=timeout)
            except asyncio.TimeoutError:
                logger.error("Shutdown timed out for '%s' after %.1fs", name, timeout)
            except Exception as exc:
                logger.error(
                    "Shutdown failed for '%s': %s",
                    name,
                    _sanitize_error_message(exc),
                )


shutdown_registry = ShutdownRegistry()


# ─────────────────────────────────────────────────────────────────────────────
# Meta Engine Auto-Registration (v16.7)
# ─────────────────────────────────────────────────────────────────────────────

async def _register_all_services_with_meta(container: ServiceContainer) -> None:
    """
    Register ALL services with Meta Engine using the centralized service registry.
    This is the permanent solution introduced in v16.7.
    """
    from core.service_registry import get_service_registry
    from core.service_definitions import register_all_services
    from services.meta_engine import meta_engine

    if meta_engine is None:
        logger.warning("Meta Engine not available — skipping service registration")
        return

    registry = get_service_registry()
    registry.set_container(container)
    registry.set_meta_engine(meta_engine)

    registered = register_all_services(container, meta_engine)
    logger.info("✅ Meta Engine now supervising %d services", registered)


# ─────────────────────────────────────────────────────────────────────────────
# Boot Sequence
# ─────────────────────────────────────────────────────────────────────────────

async def boot_infrastructure(app, container: ServiceContainer) -> None:
    logger.info("=" * 60)
    logger.info("Kanyoza AI Platform v16.7 — Starting boot sequence")
    logger.info("=" * 60)

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

        logger.info("── Phase %d: %s (%d steps) ──", phase.order, phase.name, len(phase_steps))

        for step in phase_steps:
            try:
                logger.info("  ▶ %s ...", step.name)
                step_start = time.monotonic()
                await _execute_boot_step_with_timeout(step)
                elapsed = time.monotonic() - step_start
                completed_steps.append(step)
                logger.info("  ✓ %s (%.2fs)", step.name, elapsed)
            except asyncio.TimeoutError:
                logger.error("  ✗ %s TIMED OUT after %.1fs", step.name, step.timeout_seconds)
                if phase.is_critical:
                    await _rollback(completed_steps)
                    raise BootFailure(f"Critical boot step timed out: {step.name}")
                degraded_services.append(step.name)
            except Exception as exc:
                logger.error("  ✗ %s FAILED: %s", step.name, _sanitize_error_message(exc))
                if phase.is_critical:
                    await _rollback(completed_steps)
                    raise BootFailure(f"Critical boot step failed: {step.name}") from exc
                degraded_services.append(step.name)

    # v16.7: Register everything with Meta Engine after all services have booted
    try:
        await _register_all_services_with_meta(container)
    except Exception as exc:
        logger.warning("Service registration with Meta Engine failed: %s", exc)

    # Start supervision only after the final service map and health handlers
    # exist. This closes the boot-time false-negative race.
    try:
        from services.meta_engine import meta_engine
        await meta_engine.start(interval_seconds=60)
    except Exception as exc:
        logger.warning("Meta Engine monitor start deferred/failed: %s", _sanitize_error_message(exc))

    health_registry.mark_startup_complete()
    total_time = time.monotonic() - start_time

    logger.info("=" * 60)
    logger.info(
        "Boot complete in %.2fs — %d started, %d degraded",
        total_time,
        len(completed_steps),
        len(degraded_services),
    )
    if degraded_services:
        logger.warning("Degraded services: %s", ", ".join(degraded_services))
    logger.info("=" * 60)


async def _execute_boot_step_with_timeout(step: BootStep) -> None:
    result = step.action()
    if asyncio.iscoroutine(result) or asyncio.isfuture(result):
        await asyncio.wait_for(result, timeout=step.timeout_seconds)


async def graceful_shutdown(container: ServiceContainer) -> None:
    logger.info("Initiating graceful shutdown")
    await shutdown_registry.execute_all()
    logger.info("Shutdown complete.")


def _build_boot_sequence(app, container: ServiceContainer) -> list[BootStep]:
    from config.settings import settings

    steps: list[BootStep] = []
    mode = os.getenv("SERVICE_MODE", "").strip().lower() or getattr(settings, "service_mode", "full")
    logger.info("Boot sequence mode: %s", mode)

    # ── Infrastructure ──────────────────────────────────────────────────────
    # Infrastructure dependencies are booted before consumers.  In particular,
    # the UnifiedEventBus must receive the already-created Supabase client so
    # durable event persistence is enabled when the database is configured.
    steps.append(BootStep(name="SupabaseClient", phase=BootPhase.INFRASTRUCTURE, action=partial(_boot_supabase, container)))
    steps.append(BootStep(name="EventBus", phase=BootPhase.INFRASTRUCTURE, action=partial(_boot_event_bus, container)))
    # SharedEventBus is the cross-instance transport, not the in-process
    # FastAPI event bus. Only split/background deployments should run it.
    if mode in ("workers", "director", "meta", "condition", "monitor", "github"):
        steps.append(BootStep(name="SharedEventBus", phase=BootPhase.INFRASTRUCTURE, action=partial(_boot_shared_event_bus, container)))
    steps.append(BootStep(name="KCPGEventBridge", phase=BootPhase.INFRASTRUCTURE, action=partial(_boot_kcpg_event_bridge, container)))
    steps.append(BootStep(name="RedisClient", phase=BootPhase.INFRASTRUCTURE, action=partial(_boot_redis, container)))

    # ── Core ────────────────────────────────────────────────────────────────
    if mode in ("full", "api", "core", "workers", "director", "meta", "condition"):
        steps.append(BootStep(
            name="TaskQueue",
            phase=BootPhase.CORE,
            action=partial(_boot_task_queue, container),
            rollback=partial(_stop_task_queue, container),
            timeout_seconds=15.0,
        ))
        steps.append(BootStep(
            name="PluginRegistry",
            phase=BootPhase.CORE,
            action=partial(_boot_plugin_registry, container),
            rollback=partial(_stop_plugin_registry, container),
        ))
        steps.append(BootStep(name="JobService", phase=BootPhase.CORE, action=partial(_boot_job_service, container)))
        steps.append(BootStep(name="JobStatsService", phase=BootPhase.CORE, action=partial(_boot_job_stats_service, container)))
        steps.append(BootStep(name="RenderQueue", phase=BootPhase.CORE, action=partial(_boot_render_queue, container)))

    # ── Business ────────────────────────────────────────────────────────────
    # Split-engine modes are first-class deployments.  They must boot the
    # service they advertise rather than merely mounting routes for it.
    if mode in ("full", "core", "meta"):
        steps.append(BootStep(name="MetaEngine", phase=BootPhase.BUSINESS, action=partial(_boot_meta_engine, container)))
        steps.append(BootStep(name="MetaHealthHandlers", phase=BootPhase.BUSINESS, action=partial(_boot_meta_health_handlers, container)))

    if mode in ("full", "core", "director"):
        steps.append(BootStep(name="Director", phase=BootPhase.BUSINESS, action=partial(_boot_director, container)))

    if mode in ("full", "core", "condition"):
        steps.append(BootStep(name="ConditionEngine", phase=BootPhase.BUSINESS, action=partial(_boot_condition_engine, container)))

    if mode in ("full", "core"):
        steps.append(BootStep(name="LLMGateway", phase=BootPhase.BUSINESS, action=partial(_boot_llm_gateway, container)))
        steps.append(BootStep(name="AIOrchestrator", phase=BootPhase.BUSINESS, action=partial(_boot_ai_orchestrator, container)))
        steps.append(BootStep(name="ContentEngine", phase=BootPhase.BUSINESS, action=partial(_boot_content_engine, container)))
        steps.append(BootStep(name="TrendPublishBridge", phase=BootPhase.BUSINESS, action=partial(_boot_trend_publish_bridge, container)))
        steps.append(BootStep(
            name="AutonomousContent",
            phase=BootPhase.BUSINESS,
            action=partial(_boot_autonomous_content, container),
            timeout_seconds=20.0,
        ))
        steps.append(BootStep(
            name="AutonomousLoop",
            phase=BootPhase.BUSINESS,
            action=partial(_boot_autonomous_loop, container),
            timeout_seconds=20.0,
        ))

    if mode in ("full", "api"):
        steps.append(BootStep(
            name="IndustryPlugins",
            phase=BootPhase.BUSINESS,
            action=partial(_boot_industry_plugins, app, container),
            rollback=partial(_stop_industry_plugins, container),
        ))
        steps.append(BootStep(
            name="GrowthbridgePlugin",
            phase=BootPhase.BUSINESS,
            action=partial(_boot_growthbridge, container),
            rollback=partial(_stop_growthbridge, container),
            timeout_seconds=10.0,
        ))
        steps.append(BootStep(name="SocketBridge", phase=BootPhase.BUSINESS, action=partial(_wire_socket_events, container)))

    if mode in ("full", "workers"):
        steps.append(BootStep(
            name="BrandSchedulers",
            phase=BootPhase.BUSINESS,
            action=partial(_boot_brand_schedulers, container),
            rollback=partial(_stop_brand_schedulers, container),
        ))

    # ── Observability ───────────────────────────────────────────────────────
    if mode in ("full", "api"):
        steps.append(BootStep(
            name="HealthMonitor",
            phase=BootPhase.OBSERVABILITY,
            action=partial(_boot_health_monitor, container),
            rollback=partial(_stop_health_monitor, container),
        ))
        steps.append(BootStep(
            name="EnterpriseEmitters",
            phase=BootPhase.OBSERVABILITY,
            action=partial(_start_enterprise_emitters, container),
        ))

    if mode in ("full", "monitor"):
        steps.append(BootStep(
            name="MonitorPlugin",
            phase=BootPhase.OBSERVABILITY,
            action=partial(_boot_plugin_from_module, container, "plugins.monitor.plugin", "init_monitor_plugin", "MonitorPlugin"),
            timeout_seconds=10.0,
        ))
        steps.append(BootStep(
            name="BackupPlugin",
            phase=BootPhase.OBSERVABILITY,
            action=partial(_boot_plugin_from_module, container, "plugins.backup.plugin", "init_backup_plugin", "BackupPlugin"),
            timeout_seconds=10.0,
        ))
        steps.append(BootStep(
            name="TrendIntelligence",
            phase=BootPhase.OBSERVABILITY,
            action=partial(_boot_plugin_from_module, container, "plugins.trend_intelligence.plugin", "init_trend_plugin", "TrendIntelligence"),
            timeout_seconds=15.0,
        ))

    if mode in ("full", "github"):
        steps.append(BootStep(
            name="GitHubConnector",
            phase=BootPhase.OBSERVABILITY,
            action=partial(_boot_github_connector, container),
            timeout_seconds=10.0,
        ))

    return steps


async def _rollback(completed_steps: list[BootStep]) -> None:
    logger.warning("Rolling back %d steps...", len(completed_steps))
    for step in reversed(completed_steps):
        if not step.rollback:
            continue
        try:
            result = step.rollback()
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            logger.error("Rollback failed for %s: %s", step.name, _sanitize_error_message(exc)  )


# ─────────────────────────────────────────────────────────────────────────────
# Boot Helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _boot_event_bus(container: ServiceContainer) -> None:
    from core.unified_bus import UnifiedEventBus, get_unified_bus
    from core.event_bus import EventBus, get_event_bus_bridge, EventBusBridge

    bus = await get_unified_bus()
    container.register_instance(UnifiedEventBus, bus)

    try:
        from core.unified_bus import set_bus_instance
        set_bus_instance(bus)
    except Exception:
        pass

    bridge = get_event_bus_bridge(unified=bus)
    container.register_instance(EventBus, bridge)
    container.register_instance(EventBusBridge, bridge)
    await health_registry.register(_EventBusHealthProvider(bus))
    logger.info("UnifiedEventBus ready (legacy EventBus bridged).")

    # Optional subsystems – soft-fail
    try:
        from core.synapses import wire_synapses
        matrix = await wire_synapses(container, bus)
        container.register_instance(type(matrix), matrix)
        logger.info("SynapseMatrix wired (Phase B closed loop).")
    except Exception as exc:
        logger.warning("SynapseMatrix wire failed: %s", str(exc)[:300])

    try:
        from services.business_automation_event_bridge import business_automation_event_bridge
        await business_automation_event_bridge.wire(bus)
        container.register_instance(type(business_automation_event_bridge), business_automation_event_bridge)
        logger.info("BusinessAutomationEventBridge wired (event-driven automation).")
    except Exception as exc:
        logger.warning("BusinessAutomationEventBridge wire failed: %s", str(exc)[:300])

    try:
        from core.organism import OrganismRegistry, organism_registry
        organism_registry.set_container(container)
        organism_registry.set_event_bus(bus)
        container.register_instance(OrganismRegistry, organism_registry)

        default_ws = "default"
        try:
            from config.settings import settings
            default_ws = getattr(settings, "default_workspace_id", None) or "default"
        except Exception:
            pass

        org = organism_registry.get_or_create(default_ws, plan="free")
        await org.start()
        logger.info("OrganismRegistry ready (default workspace=%s).", default_ws)
    except Exception as exc:
        logger.warning("OrganismRegistry boot failed: %s", str(exc)[:300])

    try:
        from core.metabolism import usage_meter, metabolism, PLAN_CATALOG
        try:
            from services.supabase import supabase_service
            if supabase_service and supabase_service.client:
                usage_meter.set_supabase(supabase_service.client)
        except Exception:
            pass
        container.register_instance(type(metabolism), metabolism)
        logger.info("Metabolism ready (plans=%s).", list(PLAN_CATALOG.keys()))
    except Exception as exc:
        logger.warning("Metabolism boot failed: %s", str(exc)[:200])

    try:
        from core.runtime_config import runtime_config
        try:
            from services.supabase import supabase_service
            if supabase_service and supabase_service.client:
                runtime_config.set_supabase(supabase_service.client)
                runtime_config.refresh()
        except Exception:
            pass
        logger.info("RuntimeConfig ready (plans=%s)", list(runtime_config.list_plans().keys()))
    except Exception as exc:
        logger.warning("RuntimeConfig boot: %s", str(exc)[:200])

    try:
        from core.species_scale import boot_species_scale
        scale_info = await boot_species_scale(container, bus)
        logger.info("Species scale: %s", scale_info)
    except Exception as exc:
        logger.warning("Species scale boot failed: %s", str(exc)[:200])


async def _boot_supabase(container: ServiceContainer) -> None:
    from config.settings import settings
    from services.supabase import get_anon_client_from_settings, get_service_client_from_settings

    if not settings.supabase_enabled:
        logger.info("Supabase disabled — skipping.")
        return

    # Prefer the service-role client for server-side infrastructure such as
    # durable event persistence. Fall back to the anon client only when the
    # deployment intentionally has no service-role credential.
    client = None
    if settings.supabase_admin_enabled:
        client = await _run_in_thread(get_service_client_from_settings)
    else:
        client = await _run_in_thread(get_anon_client_from_settings)

    if client is None:
        raise RuntimeError("Supabase is configured but no client could be created")

    try:
        from supabase import Client as SupabaseClient
        container.register_instance(SupabaseClient, client)
    except ImportError:
        logger.warning("Supabase Python client type unavailable; client not registered in DI container")

    logger.info("Supabase connected (client=%s).", "service-role" if settings.supabase_admin_enabled else "anon")


async def _boot_shared_event_bus(container: ServiceContainer) -> None:
    """Initialize the cross-instance event transport after Supabase is ready."""
    from core.shared_event_bus import init_shared_event_bus, SharedEventBus
    from config.settings import settings

    try:
        from supabase import Client as SupabaseClient
        supabase_client = container.try_resolve(SupabaseClient)
    except ImportError:
        supabase_client = None

    source_service = (os.getenv("SERVICE_MODE", "") or getattr(settings, "service_mode", "full")).strip().lower()
    bus = init_shared_event_bus(supabase_client=supabase_client, source_service=source_service)
    container.register_instance(SharedEventBus, bus)
    await health_registry.register(bus)
    await shutdown_registry.register("SharedEventBus", bus.stop, timeout_seconds=5.0)
    logger.info("SharedEventBus ready (transport=%s, source=%s).", "supabase" if supabase_client else "disabled", source_service)


async def _boot_redis(container: ServiceContainer) -> None:
    from core.redis_client import UpstashRedisClient

    try:
        redis = container.resolve(UpstashRedisClient)
        ping = getattr(redis, "ping", None)
        if ping:
            result = ping()
            if asyncio.iscoroutine(result):
                await result
        logger.info("Redis connected.")
    except Exception as exc:
        logger.warning("Redis not available: %s", _sanitize_error_message(exc))


async def _boot_task_queue(container: ServiceContainer) -> None:
    from workers.task_queue import TaskQueue

    tq = container.resolve(TaskQueue)
    result = tq.start()
    if asyncio.iscoroutine(result) or asyncio.isfuture(result):
        await result

    await shutdown_registry.register("TaskQueue", lambda: tq.stop(timeout=10), timeout_seconds=15.0)
    await health_registry.register(_TaskQueueHealthProvider(tq))
    logger.info("TaskQueue ready.")

    try:
        from core.publish_outbox import publish_outbox
        publish_outbox.set_container(container)
        try:
            from services.supabase import supabase_service
            if supabase_service and getattr(supabase_service, "client", None):
                publish_outbox.set_supabase(supabase_service.client)
        except Exception:
            pass
        await publish_outbox.start_drainer(interval_seconds=5.0)
        await shutdown_registry.register("PublishOutbox", publish_outbox.stop_drainer, timeout_seconds=10.0)
        logger.info("PublishOutbox drainer ready")
    except Exception as exc:
        logger.warning("PublishOutbox boot: %s", exc)

    try:
        from core.slo_alerts import slo_alerter
        await slo_alerter.start_monitor(interval_seconds=60.0)
        await shutdown_registry.register("SloAlerter", slo_alerter.stop, timeout_seconds=5.0)
        logger.info("SloAlerter ready (enabled=%s)", slo_alerter.enabled)
    except Exception as exc:
        logger.warning("SloAlerter boot: %s", exc)


async def _boot_kcpg_event_bridge(container: ServiceContainer) -> None:
    from core.unified_bus import UnifiedEventBus
    from services.kcpg_event_bridge import KCPGEventBridge

    bus = container.try_resolve(UnifiedEventBus)
    if not bus:
        raise RuntimeError("UnifiedEventBus unavailable for KCPG event bridge")

    bridge = KCPGEventBridge(bus)
    started = await bridge.start()
    container.register_instance(KCPGEventBridge, bridge)
    await health_registry.register(bridge)
    await shutdown_registry.register("KCPGEventBridge", bridge.stop, timeout_seconds=10.0)
    logger.info("KCPG event bridge: %s", "enabled" if started else "disabled")


async def _boot_plugin_registry(container: ServiceContainer) -> None:
    from plugins.base import PluginRegistry
    from core.unified_bus import UnifiedEventBus

    event_bus = container.try_resolve(UnifiedEventBus)
    registry = container.try_resolve(PluginRegistry)

    if registry is None:
        from plugins.base import get_plugin_registry
        registry = get_plugin_registry(event_bus=event_bus)
        container.register_instance(PluginRegistry, registry)
        logger.info("PluginRegistry created via get_plugin_registry fallback.")
    else:
        if event_bus and hasattr(registry, "set_event_bus"):
            registry.set_event_bus(event_bus)
        logger.info("PluginRegistry resolved from container.")

    if hasattr(registry, "start_all"):
        registry.start_all()
        logger.info("PluginRegistry.start_all() completed.")

    try:
        from services.publish_orchestration import ensure_social_plugins
        report = await ensure_social_plugins(container)
        logger.info("Social plugins ensure: %s", report)
    except Exception as exc:
        logger.warning("Social plugin ensure failed: %s", exc)

    logger.info("PluginRegistry ready.")


async def _boot_job_service(container: ServiceContainer) -> None:
    from services.job_service import JobService
    from services.supabase import get_service_client_from_settings
    from core.unified_bus import UnifiedEventBus

    client = await _run_in_thread(get_service_client_from_settings)
    job_service = JobService(supabase_client=client)

    event_bus = container.try_resolve(UnifiedEventBus)
    if event_bus:
        job_service.set_event_bus(event_bus)

    container.register_instance(JobService, job_service)
    await health_registry.register(_JobServiceHealthProvider(job_service))
    logger.info("JobService ready.")


async def _boot_job_stats_service(container: ServiceContainer) -> None:
    from services.job_stats_service import init_job_stats_service
    from services.supabase import get_service_client_from_settings
    from core.unified_bus import UnifiedEventBus

    client = await _run_in_thread(get_service_client_from_settings)
    event_bus = container.try_resolve(UnifiedEventBus)
    service = init_job_stats_service(supabase_client=client, event_bus=event_bus)
    if hasattr(service, "shutdown"):
        await shutdown_registry.register("JobStatsService", service.shutdown, timeout_seconds=10.0)
    await health_registry.register(service)
    container.register_instance(type(service), service)
    logger.info("JobStatsService ready and database-wired.")


async def _boot_render_queue(container: ServiceContainer) -> None:
    from core.render_queue import wire_render_queue_container, get_render_queue

    await _run_in_thread(wire_render_queue_container, container)
    rq = get_render_queue()
    await health_registry.register(_RenderQueueHealthProvider(rq))
    logger.info("RenderQueue ready.")


async def _boot_llm_gateway(container: ServiceContainer) -> None:
    try:
        from ai.gateway import get_llm_gateway, LLMGateway
        gw = get_llm_gateway()
        health = gw.health()
        try:
            container.register_instance(LLMGateway, gw)
        except Exception:
            pass
        logger.info(
            "LLMGateway ready mode=%s providers_configured=%s",
            health.get("mode"),
            health.get("providers_configured"),
        )
    except Exception as exc:
        logger.warning("LLMGateway boot degraded: %s", exc)


async def _boot_ai_orchestrator(container: ServiceContainer) -> None:
    from ai.orchestrator import AIOrchestrator

    orch = container.resolve(AIOrchestrator)
    await health_registry.register(_AIOrchestratorHealthProvider(orch))
    logger.info("AIOrchestrator ready.")


async def _boot_content_engine(container: ServiceContainer) -> None:
    from services.content_engine import content_engine

    content_engine.set_container(container)
    await health_registry.register(content_engine)
    logger.info("ContentEngine: container wired")


async def _boot_meta_health_handlers(container: ServiceContainer) -> None:
    try:
        from services.engine_health_handlers import register_all_health_handlers
        from services.meta_engine import MetaEngineV7 as MetaEngine

        meta = container.try_resolve(MetaEngine)
        if meta is None:
            logger.warning("MetaEngine health handlers skipped: MetaEngine unavailable")
            return

        summary = register_all_health_handlers(meta)
        logger.info("MetaEngine health handlers registered: %s", summary)
    except Exception as exc:
        logger.warning("MetaEngine health handler registration failed: %s", _sanitize_error_message(exc))
        raise


async def _boot_trend_publish_bridge(container: ServiceContainer) -> None:
    from core.unified_bus import UnifiedEventBus
    from services.trend_publish_bridge import wire_trend_publish_bridge

    bus = container.try_resolve(UnifiedEventBus)
    if not bus:
        raise RuntimeError("UnifiedEventBus unavailable for trend publish bridge")

    if not await wire_trend_publish_bridge(bus):
        raise RuntimeError("Failed to wire trend.publish.requested")

    logger.info("Trend publish bridge wired")


async def _boot_meta_engine(container: ServiceContainer) -> None:
    from services.meta_engine import init_meta_engine
    from core.unified_bus import UnifiedEventBus

    event_bus = container.try_resolve(UnifiedEventBus)
    meta = init_meta_engine(event_bus=event_bus, container=container)
    if inspect.isawaitable(meta):
        meta = await meta

    await shutdown_registry.register("MetaEngine", meta.stop, timeout_seconds=10.0)

    try:
        await health_registry.register(meta)
    except Exception as exc:
        logger.warning("MetaEngine health registry: %s", exc)

    logger.info("MetaEngine initialized (registration will happen after all services boot)")


async def _boot_director(container: ServiceContainer) -> None:
    from services.director import init_director, auto_discover_engines
    from services.supabase import get_service_client_from_settings

    try:
        supabase_client = await _run_in_thread(get_service_client_from_settings)
    except Exception:
        supabase_client = None

    director = await init_director(supabase_client=supabase_client, auto_start=False)

    try:
        discovered = auto_discover_engines(director, container)
    except Exception as exc:
        logger.warning("Director auto_discover soft-fail: %s", exc)
        discovered = 0

    try:
        await director.start()
    except Exception as exc:
        logger.warning("Director start soft-fail: %s", exc)

    try:
        await shutdown_registry.register("Director", director.stop, timeout_seconds=10.0)
    except Exception as exc:
        logger.warning("Director shutdown register: %s", exc)

    try:
        await health_registry.register(director)
    except Exception as exc:
        logger.warning("Director health registry: %s", exc)

    logger.info("👔 Director: %d engines under supervision", discovered)

    try:
        from services.director_bindings import bind_director_capabilities, assert_no_unbound_capabilities

        binding_status = bind_director_capabilities(director, container)
        bound = sum(1 for v in binding_status.values() if v == "bound")
        logger.info("Director hands bound: %d capabilities", bound)

        try:
            unbound = assert_no_unbound_capabilities(director)
            if unbound:
                logger.error("Director still has unbound capabilities: %s", unbound)
        except Exception as exc:
            logger.warning("Director unbound check: %s", exc)
    except Exception as exc:
        logger.error("Director capability binding failed: %s", exc)


async def _boot_condition_engine(container: ServiceContainer) -> None:
    from services.condition_engine import autonomous_engine
    from core.unified_bus import UnifiedEventBus

    event_bus = container.try_resolve(UnifiedEventBus)
    ce = autonomous_engine
    ce.set_event_bus(event_bus)
    await health_registry.register(ce)
    logger.info("ConditionEngine ready — %d decisions tracked", len(ce._decisions))

    # Condition Engine is intelligence/proposal infrastructure. Its consequential
    # actions must never fall back to a direct content/scheduler mutator. The
    # canonical Council integration supplies the approval + durable execution
    # boundary; if that integration cannot be installed we fail closed rather
    # than silently creating a second execution plane.
    try:
        from services.supabase import supabase_service
        client = getattr(supabase_service, "service_client", None) or getattr(supabase_service, "client", None)
        if client is not None and hasattr(ce, "set_supabase_client"):
            ce.set_supabase_client(client)

        from council.integration.condition_engine_integration import bind_condition_engine_with_council
        bind_condition_engine_with_council(ce)
        logger.info("ConditionEngine governed executor wired through Council")
    except Exception as exc:
        # Keep the engine usable for observation/proposal, but make execution
        # unavailable. Never install a direct content-engine fallback here.
        try:
            from services.condition_engine import default_action_executor
            ce.set_action_executor(default_action_executor)
        except Exception:
            pass
        logger.error("ConditionEngine governed executor unavailable; execution fenced: %s", exc)


async def _boot_autonomous_content(container: ServiceContainer) -> None:
    try:
        from core.autonomous_content import init_autonomous_content
        from core.unified_bus import UnifiedEventBus
        from services.supabase import get_service_client_from_settings

        event_bus = container.try_resolve(UnifiedEventBus)
        supabase_client = None
        try:
            supabase_client = await _run_in_thread(get_service_client_from_settings)
        except Exception as exc:
            logger.warning("Supabase client unavailable for Autonomous Content: %s", exc)

        engine = await init_autonomous_content(
            supabase_client=supabase_client,
            shared_bus=event_bus,
            container=container,
        )

        await shutdown_registry.register("AutonomousContentEngine", engine.stop, timeout_seconds=10.0)
        await health_registry.register(engine)
        logger.info("🚀 Autonomous Content Engine started")
    except ImportError as exc:
        logger.warning("Autonomous Content Engine not available: %s", exc)
    except Exception as exc:
        logger.error("Autonomous Content Engine boot failed: %s", exc)
        raise


async def _boot_autonomous_loop(container: ServiceContainer) -> None:
    try:
        from core.autonomous_loop import init_autonomous_loop
        from core.unified_bus import UnifiedEventBus
        from services.meta_engine import meta_engine
        from services.director import director
        from services.condition_engine import autonomous_engine as condition_engine

        event_bus = container.try_resolve(UnifiedEventBus)

        loop = await init_autonomous_loop(
            shared_bus=event_bus,
            meta_engine=meta_engine,
            director=director,
            condition_engine=condition_engine,
            container=container,
        )

        container.register_instance(type(loop), loop)

        try:
            from core.organism import organism_registry
            org = organism_registry.get_or_create("default")
            ds = org.dreams
            if ds is not None and org.council is not None:
                ds._council = org.council
            logger.info("DreamSystem linked to organism council (ws=default)")
        except Exception as exc:
            logger.debug("Dream council link: %s", exc)

        await shutdown_registry.register("AutonomousLoop", loop.stop, timeout_seconds=10.0)
        await health_registry.register(loop)
        logger.info("🔄 Autonomous Loop started — system is self-driving")
    except ImportError as exc:
        logger.warning("Autonomous Loop not available: %s", exc)
    except Exception as exc:
        logger.error("Autonomous Loop boot failed: %s", exc)

async def _boot_industry_plugins(app, container: ServiceContainer) -> None:
    from supabase import Client as SupabaseClient

    supabase_client = container.try_resolve(SupabaseClient)
    plugins = await _run_in_thread(_discover_industry_plugins, supabase_client, None)
    container.industry_plugins = plugins

    if not plugins:
        logger.info("No industry plugins discovered.")
        return

    _register_plugin_commands(plugins)

    for name, plugin in plugins.items():
        if hasattr(plugin, "shutdown"):
            await shutdown_registry.register(
                f"IndustryPlugin:{name}",
                plugin.shutdown,
                timeout_seconds=10.0,
            )

    logger.info("Industry plugins loaded: %s", list(plugins.keys()))


async def _boot_growthbridge(container: ServiceContainer) -> None:
    try:
        from plugins.growthbridge.plugin import GrowthbridgePlugin
        from services.supabase import get_service_client_from_settings

        client = await _run_in_thread(get_service_client_from_settings)
        plugin = GrowthbridgePlugin(supabase_client=client)

        if not hasattr(container, "industry_plugins"):
            container.industry_plugins = {}
        container.industry_plugins["growthbridge"] = plugin

        try:
            from services.command_executor import CommandExecutor
            for cmd in plugin.get_commands():
                CommandExecutor.register_external_command("growthbridge", cmd)
        except ImportError:
            pass

        if hasattr(plugin, "shutdown"):
            await shutdown_registry.register("GrowthbridgePlugin", plugin.shutdown, timeout_seconds=10.0)

        if any(hasattr(plugin, attr) for attr in ("check_health", "check_health_async", "health_check")):
            await health_registry.register(plugin)

        logger.info("Growthbridge plugin loaded (standalone)")
    except ImportError:
        logger.debug("Growthbridge plugin not available")
    except Exception as exc:
        logger.warning("Growthbridge boot failed: %s", _sanitize_error_message(exc))


async def _stop_growthbridge(container: ServiceContainer) -> None:
    plugins = getattr(container, "industry_plugins", {})
    plugin = plugins.get("growthbridge")
    if plugin and hasattr(plugin, "shutdown"):
        await _run_in_thread(plugin.shutdown)


def _discover_industry_plugins(supabase_client=None, redis_client=None) -> dict:
    plugins = {}
    plugins_dir = os.path.join(os.path.dirname(__file__), "plugins", "industries")

    if not os.path.isdir(plugins_dir):
        return plugins

    for folder in sorted(os.listdir(plugins_dir)):
        folder_path = os.path.join(plugins_dir, folder)
        if not os.path.isdir(folder_path) or folder.startswith("__") or folder in ("base", "specs", "addons"):
            continue

        plugin_file = os.path.join(folder_path, "plugin.py")
        if not os.path.isfile(plugin_file):
            continue

        try:
            module = importlib.import_module(f"plugins.industries.{folder}.plugin")
            plugin_class = None
            for name in dir(module):
                obj = getattr(module, name)
                if isinstance(obj, type) and name.endswith("Plugin") and name != "IndustryPlugin":
                    plugin_class = obj
                    break

            if plugin_class is None:
                continue

            plugin_instance = plugin_class(supabase_client=supabase_client, redis_client=redis_client)
            plugins[folder] = plugin_instance
        except ImportError:
            pass
        except Exception as exc:
            logger.warning("Plugin '%s' failed: %s", folder, _sanitize_error_message(exc))

    return plugins


def _register_plugin_commands(plugins: dict) -> None:
    try:
        from services.command_executor import CommandExecutor
        total = 0
        for plugin_name, plugin in plugins.items():
            for cmd in plugin.get_commands():
                CommandExecutor.register_external_command(plugin_name, cmd)
                total += 1
        logger.info("Registered %d plugin commands.", total)
    except ImportError:
        pass
    except Exception as exc:
        logger.warning("Plugin command registration failed: %s", _sanitize_error_message(exc))


async def _wire_socket_events(container: ServiceContainer) -> None:
    try:
        from core.unified_bus import UnifiedEventBus, get_unified_bus
        from core.socket_server import get_socket_server

        bus = container.try_resolve(UnifiedEventBus)
        if bus is None:
            bus = await get_unified_bus()

        socket_server = get_socket_server()
        if not socket_server:
            logger.debug("SocketIO server not available")
            return

        async def forward_to_socket(event_data: Any) -> None:
            try:
                event_name = event_data.get("event_name", "unknown") if isinstance(event_data, dict) else "event"
                payload = event_data.get("payload", event_data) if isinstance(event_data, dict) else {"data": event_data}
                await socket_server.emit(event_name, payload)
            except Exception as e:
                logger.debug("Socket forward failed: %s", e)

        await bus.subscribe("service_heartbeat", forward_to_socket)
        await bus.subscribe("engine_alert", forward_to_socket)
        await bus.subscribe("incident_updated", forward_to_socket)
        await bus.subscribe("diagnosis_created", forward_to_socket)

        logger.info("✅ SocketIO bridge active — forwarding events to clients")
    except ImportError as exc:
        logger.debug("SocketIO bridge unavailable: %s", exc)
    except Exception as exc:
        logger.warning("SocketIO bridge failed: %s", _sanitize_error_message(exc))


async def _boot_brand_schedulers(container: ServiceContainer) -> None:
    from scheduler.brand_scheduler import BrandSchedulerManager

    manager = container.resolve(BrandSchedulerManager)
    manager.set_container(container)

    try:
        manager.set_event_loop(asyncio.get_running_loop())
    except Exception:
        pass

    result = manager.start()
    if asyncio.iscoroutine(result) or asyncio.isfuture(result):
        await result

    await shutdown_registry.register("BrandSchedulerManager", manager.stop, timeout_seconds=10.0)
    logger.info("BrandSchedulers started")


async def _boot_health_monitor(container: ServiceContainer) -> None:
    from services.health_monitor_service import HealthMonitorService

    hm = HealthMonitorService(container=container)
    result = hm.start()
    if asyncio.iscoroutine(result) or asyncio.isfuture(result):
        await result

    container.register_instance(HealthMonitorService, hm)
    await shutdown_registry.register("HealthMonitorService", hm.stop, timeout_seconds=10.0)
    await health_registry.register(hm)
    logger.info("HealthMonitorService started.")


async def _start_enterprise_emitters(container: ServiceContainer) -> None:
    logger.info("Enterprise emitters initialized")


async def _boot_plugin_from_module(
    container: ServiceContainer,
    module_path: str,
    init_function_name: str,
    display_name: str,
) -> None:
    try:
        module = __import__(module_path, fromlist=[init_function_name])
        init_fn = getattr(module, init_function_name)

        from services.supabase import get_service_client_from_settings
        client = await _run_in_thread(get_service_client_from_settings)

        result = init_fn(supabase_client=client)
        if asyncio.iscoroutine(result):
            await result

        logger.info("%s: started", display_name)
    except Exception as exc:
        logger.warning("%s: could not start: %s", display_name, _sanitize_error_message(exc))
        raise


async def _boot_github_connector(container: ServiceContainer) -> None:
    try:
        from plugins.github_connector.plugin import init_github_connector_plugin
        from services.supabase import get_service_client_from_settings

        client = await _run_in_thread(get_service_client_from_settings)
        plugin = init_github_connector_plugin(supabase_client=client)

        await health_registry.register(_GitHubConnectorHealthProvider(plugin))
        await shutdown_registry.register("GitHubConnector", plugin.shutdown, timeout_seconds=10.0)
        logger.info("GitHubConnector: started")
    except ImportError:
        logger.debug("GitHubConnector: not available.")
    except Exception as exc:
        logger.warning("GitHubConnector: could not start: %s", _sanitize_error_message(exc)  )


# ─────────────────────────────────────────────────────────────────────────────
# Shutdown Helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _stop_task_queue(container: ServiceContainer) -> None:
    from workers.task_queue import TaskQueue
    tq = container.try_resolve(TaskQueue)
    if tq:
        result = tq.stop(5)
        if asyncio.iscoroutine(result):
            await result


async def _stop_plugin_registry(container: ServiceContainer) -> None:
    from plugins.base import PluginRegistry
    registry = container.try_resolve(PluginRegistry)
    if registry:
        result = registry.stop_all()
        if asyncio.iscoroutine(result):
            await result


async def _stop_industry_plugins(container: ServiceContainer) -> None:
    plugins = getattr(container, "industry_plugins", {})
    for name, plugin in plugins.items():
        if hasattr(plugin, "shutdown"):
            try:
                result = plugin.shutdown()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                pass


async def _stop_brand_schedulers(container: ServiceContainer) -> None:
    from scheduler.brand_scheduler import BrandSchedulerManager
    manager = container.try_resolve(BrandSchedulerManager)
    if manager:
        result = manager.stop()
        if asyncio.iscoroutine(result):
            await result


async def _stop_health_monitor(container: ServiceContainer) -> None:
    from services.health_monitor_service import HealthMonitorService
    hm = container.try_resolve(HealthMonitorService)
    if hm:
        result = hm.stop()
        if asyncio.iscoroutine(result):
            await result


# ─────────────────────────────────────────────────────────────────────────────
# Health Providers
# ─────────────────────────────────────────────────────────────────────────────

class _EventBusHealthProvider:
    def __init__(self, bus):
        self._bus = bus
        self.service_name = "event_bus"

    async def check_health_async(self) -> HealthReport:
        try:
            return HealthReport(
                HealthStatus.HEALTHY,
                self.service_name,
                metadata=self._bus.get_stats(),
            )
        except Exception as exc:
            return HealthReport(
                HealthStatus.DEGRADED,
                self.service_name,
                error_message=_sanitize_error_message(exc),
            )


class _TaskQueueHealthProvider:
    def __init__(self, tq):
        self._tq = tq
        self.service_name = "task_queue"

    async def check_health_async(self) -> HealthReport:
        try:
            q = getattr(self._tq, "queue", None)
            return HealthReport(
                HealthStatus.HEALTHY,
                self.service_name,
                metadata={"queue_size": q.qsize() if q else 0},
            )
        except Exception as exc:
            return HealthReport(
                HealthStatus.DEGRADED,
                self.service_name,
                error_message=_sanitize_error_message(exc),
            )


class _JobServiceHealthProvider:
    def __init__(self, js):
        self._js = js
        self.service_name = "job_service"

    async def check_health_async(self) -> HealthReport:
        try:
            if hasattr(self._js, "health_check"):
                result = self._js.health_check()
                if asyncio.iscoroutine(result):
                    result = await result
                if isinstance(result, dict):
                    status = str(result.get("status", "healthy")).lower()
                    return HealthReport(
                        HealthStatus.HEALTHY if status in ("healthy", "ok") else HealthStatus.DEGRADED,
                        self.service_name,
                        metadata=result,
                    )
            return HealthReport(
                HealthStatus.HEALTHY,
                self.service_name,
                metadata={"status": "running"},
            )
        except Exception as exc:
            return HealthReport(
                HealthStatus.DEGRADED,
                self.service_name,
                error_message=_sanitize_error_message(exc),
            )


class _RenderQueueHealthProvider:
    def __init__(self, rq):
        self._rq = rq
        self.service_name = "render_queue"

    async def check_health_async(self) -> HealthReport:
        try:
            return HealthReport(
                HealthStatus.HEALTHY,
                self.service_name,
                metadata={"pending_tasks": len(getattr(self._rq, "_pending", []))},
            )
        except Exception as exc:
            return HealthReport(
                HealthStatus.DEGRADED,
                self.service_name,
                error_message=_sanitize_error_message(exc),
            )


class _AIOrchestratorHealthProvider:
    def __init__(self, orch):
        self._orch = orch
        self.service_name = "ai_orchestrator"

    async def check_health_async(self) -> HealthReport:
        try:
            health = self._orch.health()
            available = sum(1 for h in health.values() if h.get("available"))
            return HealthReport(
                HealthStatus.HEALTHY if available else HealthStatus.DEGRADED,
                self.service_name,
                metadata={"available": available, "total": len(health)},
            )
        except Exception as exc:
            return HealthReport(
                HealthStatus.DEGRADED,
                self.service_name,
                error_message=_sanitize_error_message(exc),
            )


class _GitHubConnectorHealthProvider:
    def __init__(self, plugin):
        self._plugin = plugin
        self.service_name = "github_connector"

    async def check_health_async(self) -> HealthReport:
        try:
            return HealthReport(HealthStatus.HEALTHY, self.service_name)
        except Exception as exc:
            return HealthReport(
                HealthStatus.DEGRADED,
                self.service_name,
                error_message=_sanitize_error_message(exc),
            )


__all__ = [
    "boot_infrastructure",
    "graceful_shutdown",
    "health_registry",
    "shutdown_registry",
    "HealthStatus",
    "HealthReport",
    "HealthProvider",
    "AsyncHealthProvider",
    "BootPhase",
    "BootStep",
    "ServiceHealthRegistry",
    "ShutdownRegistry",
    "BootFailure",
    "_sanitize_error_message",
    "_execute_boot_step_with_timeout",
]
