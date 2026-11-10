"""
Kanyoza Systems AI Platform v16.9 — Lifecycle Management (Async-Native).

v16.9 (content-intelligence boot step):
  - Added ContentIntelligence BootStep. Registers ContentIntelligenceAdapter
    under the ContentIntelligencePort (falling back to the concrete adapter
    type) so that content_engine.generate_post can resolve it and ground
    content generation in external evidence. Non-critical: content
    generation still works (ungrounded) if this step fails, but the gap is
    logged as a boot warning so it is visible.
  - The step runs in the BUSINESS phase, immediately before LLMGateway, so
    the engine it wraps can see the LLM gateway when it is first used.

v16.8 (hardening, backward compatible):
  - Thread-safe, loop-aware health registry with an optional snapshot cache
    (read_snapshot / publish_snapshot / check_all_cached).
  - BootStep.critical override; AutonomousLoop marked critical.
  - _rollback with per-step timeout; BootFailure.failed_rollbacks surfaced.
  - ShutdownRegistry with a global budget; hooks past budget are skipped.
  - Health probes sanitize exception logging (no traceback.format_exc() in
    production paths that could carry credentials).
  - Meta-engine registration & supervisor start modeled as BootSteps.
  - _sanitize_error_message re-exported from core.redaction.
  - All public signatures preserved.

v16.7 (unchanged):
  - Auto-registration of ALL services with Meta Engine.
  - Centralized service registry integration.

v16.6 (unchanged):
  - probe failures now return DEGRADED (not UNHEALTHY).
  - async check exceptions return DEGRADED (not UNHEALTHY).

V71.16.1 (hardening, unchanged):
  - AutonomousLoop boot step installs the workforce bridge AFTER the
    loop is created. Fails loud on install error.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from functools import partial
from typing import Any, Callable, Optional, Protocol, runtime_checkable

from plugins.base import PluginRegistry
from service_container import ServiceContainer

# PATCH: centralized redaction. Falls back to a local implementation if
# core.redaction is not yet deployed, so this file is safe to ship either way.
try:
    from core.redaction import sanitize_error_message as _sanitize_error_message_impl
except ImportError:  # pragma: no cover
    import re as _re

    def _sanitize_error_message_impl(exc, max_len: int = 200) -> str:  # type: ignore
        raw = str(exc)
        raw = _re.sub(r'File "[^"]*"', "File [redacted]", raw)
        raw = _re.sub(r"/[^\s]*\.py", "[path_redacted]", raw)
        raw = _re.sub(r"0x[0-9a-fA-F]+", "[addr]", raw)
        return raw[:max_len]


logger = logging.getLogger("kanyoza.lifecycle")


def _sanitize_error_message(exc: BaseException, max_len: int = 200) -> str:
    """Public re-export. Same signature as v16.7."""
    return _sanitize_error_message_impl(exc, max_len)


# ─────────────────────────────────────────────────────────────────────────────
# Exceptions
# ─────────────────────────────────────────────────────────────────────────────


class BootFailure(Exception):
    """Raised when a critical boot phase fails and the process should exit.

    failed_rollbacks: names of steps whose rollback also failed. Non-empty
    means the process is in an indeterminate state (leaked threads, open
    sockets). Downstream supervisors should be told before exit.
    """

    def __init__(
        self,
        message: str,
        *,
        failed_rollbacks: Optional[list[str]] = None,
    ) -> None:
        super().__init__(message)
        self.failed_rollbacks: list[str] = failed_rollbacks or []


# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────


async def _run_in_thread(func: Callable, *args, **kwargs) -> Any:
    """Run a blocking callable in a thread using the current event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        partial(func, *args, **kwargs) if args or kwargs else func,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Health Model
# ─────────────────────────────────────────────────────────────────────────────


class HealthStatus(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"     # PATCH: explicit "we don't know" state
    STARTING = "starting"


@dataclass
class HealthReport:
    status: HealthStatus
    service_name: str
    latency_ms: float = 0.0
    last_success: Optional[datetime] = None
    error_message: Optional[str] = None
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
    rollback: Optional[Callable[[], Any]] = None
    timeout_seconds: float = 30.0
    # PATCH: per-step criticality override. If None, uses phase.is_critical.
    # Set to True for steps that must fail-loud even in a non-critical phase.
    # Set to False (rare) to allow a critical-phase step to degrade.
    critical: Optional[bool] = None


# ─────────────────────────────────────────────────────────────────────────────
# Service Health Registry
# ─────────────────────────────────────────────────────────────────────────────


class ServiceHealthRegistry:
    """Health provider registry with an optional snapshot cache.

    Snapshot cache lets HealthMonitorService publish a fresh aggregate that
    HTTP readiness endpoints can read without re-probing. If no snapshot has
    been published yet, read_snapshot() returns {} and callers should fall
    back to check_all().
    """

    def __init__(self, max_concurrency: int = 16) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self._providers: dict[str, Any] = {}
        self._max_concurrency = max_concurrency
        self._lock = asyncio.Lock()
        self._startup_complete = asyncio.Event()

        # Snapshot cache (v16.8)
        self._snapshot: dict[str, HealthReport] = {}
        self._snapshot_at: float = 0.0
        self._snapshot_lock = asyncio.Lock()

    async def register(self, provider: Any) -> None:
        async with self._lock:
            name = getattr(provider, "service_name", "unknown")
            existing = self._providers.get(name)
            if existing is not None and existing is not provider:
                logger.warning(
                    "Health provider name collision: '%s' (%s replaces %s)",
                    name,
                    type(provider).__name__,
                    type(existing).__name__,
                )
            self._providers[name] = provider

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

    def is_startup_complete(self) -> bool:
        return self._startup_complete.is_set()

    # ── Snapshot cache (v16.8) ─────────────────────────────────────────

    async def publish_snapshot(self, reports: dict[str, HealthReport]) -> None:
        """Store the latest aggregate. Called by the health monitor."""
        async with self._snapshot_lock:
            self._snapshot = dict(reports)
            self._snapshot_at = time.monotonic()

    def read_snapshot(self) -> dict[str, HealthReport]:
        """Read the last published snapshot. Never blocks on probes."""
        return dict(self._snapshot)

    def read_snapshot_age_seconds(self) -> Optional[float]:
        if self._snapshot_at <= 0:
            return None
        return time.monotonic() - self._snapshot_at

    async def check_all_cached(
        self, max_age_seconds: float = 60.0
    ) -> dict[str, HealthReport]:
        """Return the cached snapshot if fresh; otherwise probe once and cache."""
        age = self.read_snapshot_age_seconds()
        if age is not None and age <= max_age_seconds and self._snapshot:
            return self.read_snapshot()
        reports = await self.check_all()
        await self.publish_snapshot(reports)
        return reports

    # ── Probing ────────────────────────────────────────────────────────

    async def check_all(
        self, timeout_per_service: float = 5.0
    ) -> dict[str, HealthReport]:
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
                        status=HealthStatus.UNKNOWN,
                        service_name=service_name,
                        error_message=f"Timeout after {timeout_per_service}s",
                        metadata={"note": "Health probe timed out", "confidence": "low"},
                    )
                except Exception as exc:
                    logger.debug(
                        "Unexpected error while probing %s: %s",
                        service_name,
                        _sanitize_error_message(exc),
                    )
                    return service_name, HealthReport(
                        status=HealthStatus.UNKNOWN,
                        service_name=service_name,
                        error_message=_sanitize_error_message(exc),
                        metadata={"note": "Health probe errored", "confidence": "low"},
                    )

        checked = await asyncio.gather(*(check_provider(p) for p in providers))
        return dict(checked)

    async def _check_single_provider(
        self, provider: Any, timeout: float
    ) -> HealthReport:
        service_name = getattr(provider, "service_name", "unknown")

        if hasattr(provider, "check_health_async"):
            return await self._run_health_check(
                provider.check_health_async, service_name, timeout, is_async=True
            )

        if hasattr(provider, "health_check"):
            return await self._run_health_check(
                provider.health_check, service_name, timeout, is_async=False
            )

        if hasattr(provider, "check_health"):
            return await self._run_health_check(
                provider.check_health, service_name, timeout, is_async=False
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
                raw = await asyncio.wait_for(
                    _run_in_thread(method),
                    timeout=timeout,
                )
                if inspect.isawaitable(raw):
                    raw = await asyncio.wait_for(raw, timeout=timeout)

            return self._normalize_health_result(raw, service_name)

        except asyncio.TimeoutError:
            return HealthReport(
                HealthStatus.UNKNOWN,
                service_name,
                error_message=f"Timeout after {timeout}s",
                metadata={"note": "probe_timeout", "confidence": "low"},
            )
        except Exception as exc:
            # PATCH: no traceback.format_exc() — it can carry credentials from
            # the underlying exception chain.
            logger.debug(
                "Health probe failed for %s: %s",
                service_name,
                _sanitize_error_message(exc),
            )
            return HealthReport(
                HealthStatus.UNKNOWN,
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
            if status_str in ("unknown", "starting"):
                return HealthReport(HealthStatus.UNKNOWN, service_name, metadata=raw)
            return HealthReport(HealthStatus.DEGRADED, service_name, metadata=raw)

        if raw is None:
            return HealthReport(
                HealthStatus.UNKNOWN,
                service_name,
                metadata={"note": "health check returned None", "confidence": "low"},
            )

        return HealthReport(
            HealthStatus.UNKNOWN,
            service_name,
            metadata={"note": "unrecognized health payload",
                      "type": type(raw).__name__},
        )


health_registry = ServiceHealthRegistry()


# ─────────────────────────────────────────────────────────────────────────────
# Shutdown Registry
# ─────────────────────────────────────────────────────────────────────────────


class ShutdownRegistry:
    """LIFO shutdown hook executor with a global budget.

    Hooks past the global budget are skipped (and logged). This prevents a
    hung hook from delaying process exit beyond the orchestrator's SIGKILL
    window.
    """

    def __init__(self, global_budget_seconds: float = 25.0) -> None:
        self._hooks: list[tuple[str, Callable, float]] = []
        self._lock = asyncio.Lock()
        self._executing = False
        self._global_budget = global_budget_seconds

    async def register(
        self, name: str, hook: Callable, timeout_seconds: float = 10.0
    ) -> None:
        async with self._lock:
            if self._executing:
                logger.warning(
                    "Late shutdown registration ignored for '%s' — shutdown "
                    "already started. This may leak a resource.",
                    name,
                )
                return
            self._hooks.append((name, hook, timeout_seconds))

    async def execute_all(self) -> dict[str, Any]:
        """Run all registered hooks (LIFO) within the global budget.

        Returns a summary dict: total, executed, failed, timed_out,
        budget_exhausted, abandoned.
        """
        async with self._lock:
            if self._executing:
                return {
                    "total": 0, "executed": 0, "failed": 0, "timed_out": 0,
                    "budget_exhausted": False, "abandoned": 0,
                }
            self._executing = True
            hooks = list(reversed(self._hooks))
            self._hooks.clear()

        deadline = time.monotonic() + self._global_budget
        summary = {
            "total": len(hooks),
            "executed": 0,
            "failed": 0,
            "timed_out": 0,
            "budget_exhausted": False,
            "abandoned": 0,
        }

        for idx, (name, hook, timeout) in enumerate(hooks):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                summary["budget_exhausted"] = True
                summary["abandoned"] = len(hooks) - idx
                logger.error(
                    "Shutdown budget exhausted; abandoning %d remaining hook(s). "
                    "First abandoned: %s",
                    summary["abandoned"],
                    name,
                )
                break

            effective_timeout = min(timeout, remaining)
            logger.info("Shutting down: %s ...", name)
            try:
                result = hook()
                if asyncio.iscoroutine(result) or asyncio.isfuture(result):
                    await asyncio.wait_for(result, timeout=effective_timeout)
                summary["executed"] += 1
            except asyncio.TimeoutError:
                summary["timed_out"] += 1
                logger.error(
                    "Shutdown timed out for '%s' after %.1fs",
                    name, effective_timeout,
                )
            except Exception as exc:
                summary["failed"] += 1
                logger.error(
                    "Shutdown failed for '%s': %s",
                    name,
                    _sanitize_error_message(exc),
                )

        return summary


shutdown_registry = ShutdownRegistry()


# ─────────────────────────────────────────────────────────────────────────────
# Meta Engine Auto-Registration (v16.7)
# ─────────────────────────────────────────────────────────────────────────────


async def _register_all_services_with_meta(container: ServiceContainer) -> None:
    """Register all services with Meta Engine using the centralized registry."""
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
    logger.info("Kanyoza AI Platform v16.9 — Starting boot sequence")
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

        logger.info(
            "── Phase %d: %s (%d steps) ──",
            phase.order, phase.name, len(phase_steps),
        )

        for step in phase_steps:
            is_critical = (
                step.critical if step.critical is not None else phase.is_critical
            )
            try:
                logger.info("  ▶ %s ...", step.name)
                step_start = time.monotonic()
                await _execute_boot_step_with_timeout(step)
                elapsed = time.monotonic() - step_start
                completed_steps.append(step)
                logger.info("  ✓ %s (%.2fs)", step.name, elapsed)
            except asyncio.TimeoutError:
                logger.error(
                    "  ✗ %s TIMED OUT after %.1fs",
                    step.name, step.timeout_seconds,
                )
                if is_critical:
                    failed = await _rollback(completed_steps)
                    raise BootFailure(
                        f"Critical boot step timed out: {step.name}",
                        failed_rollbacks=failed,
                    )
                degraded_services.append(step.name)
            except Exception as exc:
                logger.error(
                    "  ✗ %s FAILED: %s",
                    step.name, _sanitize_error_message(exc),
                )
                if is_critical:
                    failed = await _rollback(completed_steps)
                    raise BootFailure(
                        f"Critical boot step failed: {step.name}",
                        failed_rollbacks=failed,
                    ) from exc
                degraded_services.append(step.name)

    # v16.7: Register everything with Meta Engine after all services have booted.
    try:
        await _register_all_services_with_meta(container)
    except Exception as exc:
        logger.warning(
            "Service registration with Meta Engine failed: %s",
            _sanitize_error_message(exc),
        )
        degraded_services.append("MetaEngineServiceRegistration")

    # Start supervision after the final service map and health handlers exist.
    try:
        from services.meta_engine import meta_engine
        await meta_engine.start(interval_seconds=60)
    except Exception as exc:
        logger.warning(
            "Meta Engine monitor start deferred/failed: %s",
            _sanitize_error_message(exc),
        )
        degraded_services.append("MetaEngineMonitorStart")

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


async def graceful_shutdown(container: ServiceContainer) -> dict[str, Any]:
    """Execute all shutdown hooks within the global budget.

    v16.8: returns a summary dict. Callers that only awaited and ignored
    the return value continue to work unchanged.
    """
    logger.info("Initiating graceful shutdown")
    summary = await shutdown_registry.execute_all()
    logger.info(
        "Shutdown complete: %d/%d executed, %d failed, %d timed out, "
        "%d abandoned, budget_exhausted=%s",
        summary.get("executed", 0),
        summary.get("total", 0),
        summary.get("failed", 0),
        summary.get("timed_out", 0),
        summary.get("abandoned", 0),
        summary.get("budget_exhausted", False),
    )
    return summary


def _build_boot_sequence(app, container: ServiceContainer) -> list[BootStep]:
    from config.settings import settings

    steps: list[BootStep] = []
    mode = (
        os.getenv("SERVICE_MODE", "").strip().lower()
        or getattr(settings, "service_mode", "full")
    )
    logger.info("Boot sequence mode: %s", mode)

    # ── Infrastructure ──────────────────────────────────────────────────
    steps.append(BootStep(
        name="SupabaseClient",
        phase=BootPhase.INFRASTRUCTURE,
        action=partial(_boot_supabase, container),
    ))
    steps.append(BootStep(
        name="EventBus",
        phase=BootPhase.INFRASTRUCTURE,
        action=partial(_boot_event_bus, container),
    ))
    if mode in ("workers", "director", "meta", "condition", "monitor", "github"):
        steps.append(BootStep(
            name="SharedEventBus",
            phase=BootPhase.INFRASTRUCTURE,
            action=partial(_boot_shared_event_bus, container),
        ))
    steps.append(BootStep(
        name="KCPGEventBridge",
        phase=BootPhase.INFRASTRUCTURE,
        action=partial(_boot_kcpg_event_bridge, container),
    ))
    steps.append(BootStep(
        name="RedisClient",
        phase=BootPhase.INFRASTRUCTURE,
        action=partial(_boot_redis, container),
    ))

    # ── Core ────────────────────────────────────────────────────────────
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
        steps.append(BootStep(
            name="JobService",
            phase=BootPhase.CORE,
            action=partial(_boot_job_service, container),
        ))
        steps.append(BootStep(
            name="JobStatsService",
            phase=BootPhase.CORE,
            action=partial(_boot_job_stats_service, container),
        ))
        steps.append(BootStep(
            name="RenderQueue",
            phase=BootPhase.CORE,
            action=partial(_boot_render_queue, container),
        ))

    # ── Business ────────────────────────────────────────────────────────
    if mode in ("full", "core", "meta"):
        steps.append(BootStep(
            name="MetaEngine",
            phase=BootPhase.BUSINESS,
            action=partial(_boot_meta_engine, container),
        ))
        steps.append(BootStep(
            name="MetaHealthHandlers",
            phase=BootPhase.BUSINESS,
            action=partial(_boot_meta_health_handlers, container),
        ))

    if mode in ("full", "core", "director"):
        steps.append(BootStep(
            name="Director",
            phase=BootPhase.BUSINESS,
            action=partial(_boot_director, container),
        ))

    if mode in ("full", "core", "condition"):
        steps.append(BootStep(
            name="ConditionEngine",
            phase=BootPhase.BUSINESS,
            action=partial(_boot_condition_engine, container),
        ))

    if mode in ("full", "core"):
        # v16.9: ContentIntelligence must be registered BEFORE ContentEngine
        # so the content engine can resolve it at first use. It also runs
        # BEFORE LLMGateway so the engine it wraps can see the gateway when
        # analyze() is first called. Non-critical: generation degrades to
        # ungrounded prose if the port is unavailable, it does not crash —
        # but the absence is logged so it does not go unnoticed (which is
        # exactly how the 2026-10-20 ungrounded listicle happened).
        steps.append(BootStep(
            name="ContentIntelligence",
            phase=BootPhase.BUSINESS,
            action=partial(_boot_content_intelligence, container),
            timeout_seconds=10.0,
            critical=False,
        ))
        steps.append(BootStep(
            name="LLMGateway",
            phase=BootPhase.BUSINESS,
            action=partial(_boot_llm_gateway, container),
        ))
        steps.append(BootStep(
            name="AIOrchestrator",
            phase=BootPhase.BUSINESS,
            action=partial(_boot_ai_orchestrator, container),
        ))
        steps.append(BootStep(
            name="ContentEngine",
            phase=BootPhase.BUSINESS,
            action=partial(_boot_content_engine, container),
        ))
        steps.append(BootStep(
            name="TrendPublishBridge",
            phase=BootPhase.BUSINESS,
            action=partial(_boot_trend_publish_bridge, container),
        ))
        steps.append(BootStep(
            name="AutonomousContent",
            phase=BootPhase.BUSINESS,
            action=partial(_boot_autonomous_content, container),
            timeout_seconds=20.0,
        ))
        steps.append(BootStep(
            name="AutonomousLoop",
            phase=BootPhase.BUSINESS,
            # V71.16.1 — pass app so the workforce bridge can be installed
            # inside _boot_autonomous_loop AFTER the loop exists.
            action=partial(_boot_autonomous_loop, app, container),
            timeout_seconds=20.0,
            # PATCH v16.8: this step has always re-raised on failure, but the
            # BUSINESS phase is non-critical, so the raise was silently
            # downgraded to "degraded". The critical override makes the
            # fail-loud intent of V71.16.1 actually take effect.
            critical=True,
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
        steps.append(BootStep(
            name="SocketBridge",
            phase=BootPhase.BUSINESS,
            action=partial(_wire_socket_events, container),
        ))

    if mode in ("full", "workers"):
        steps.append(BootStep(
            name="ITDeploymentReconciliationScheduler",
            phase=BootPhase.BUSINESS,
            action=partial(_boot_it_reconciliation_scheduler, container),
            timeout_seconds=15.0,
        ))
        steps.append(BootStep(
            name="ITServiceRemediationReconciliationScheduler",
            phase=BootPhase.BUSINESS,
            action=partial(_boot_it_service_remediation_reconciliation_scheduler, container),
            timeout_seconds=15.0,
        ))
        steps.append(BootStep(
            name="ManufacturingMaintenanceReconciliationScheduler",
            phase=BootPhase.BUSINESS,
            action=partial(_boot_manufacturing_maintenance_reconciliation_scheduler, container),
            timeout_seconds=15.0,
        ))
        steps.append(BootStep(
            name="FinancePaymentReconciliationScheduler",
            phase=BootPhase.BUSINESS,
            action=partial(_boot_finance_reconciliation_scheduler, container),
            timeout_seconds=15.0,
        ))
        steps.append(BootStep(
            name="CRMMessageReconciliationScheduler",
            phase=BootPhase.BUSINESS,
            action=partial(_boot_crm_message_reconciliation_scheduler, container),
            timeout_seconds=15.0,
        ))
        steps.append(BootStep(
            name="EducationParentMessageReconciliationScheduler",
            phase=BootPhase.BUSINESS,
            action=partial(_boot_education_parent_message_reconciliation_scheduler, container),
            timeout_seconds=15.0,
        ))
        steps.append(BootStep(
            name="HealthcarePatientMessageReconciliationScheduler",
            phase=BootPhase.BUSINESS,
            action=partial(_boot_healthcare_patient_message_reconciliation_scheduler, container),
            timeout_seconds=15.0,
        ))
        steps.append(BootStep(
            name="CommerceOrderNotificationReconciliationScheduler",
            phase=BootPhase.BUSINESS,
            action=partial(_boot_commerce_order_notification_reconciliation_scheduler, container),
            timeout_seconds=15.0,
        ))
        steps.append(BootStep(
            name="BrandSchedulers",
            phase=BootPhase.BUSINESS,
            action=partial(_boot_brand_schedulers, container),
            rollback=partial(_stop_brand_schedulers, container),
        ))

    # ── Observability ───────────────────────────────────────────────────
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
            action=partial(
                _boot_plugin_from_module, container,
                "plugins.monitor.plugin", "init_monitor_plugin", "MonitorPlugin",
            ),
            timeout_seconds=10.0,
        ))
        steps.append(BootStep(
            name="BackupPlugin",
            phase=BootPhase.OBSERVABILITY,
            action=partial(
                _boot_plugin_from_module, container,
                "plugins.backup.plugin", "init_backup_plugin", "BackupPlugin",
            ),
            timeout_seconds=10.0,
        ))
        steps.append(BootStep(
            name="TrendIntelligence",
            phase=BootPhase.OBSERVABILITY,
            action=partial(
                _boot_plugin_from_module, container,
                "plugins.trend_intelligence.plugin",
                "init_trend_plugin", "TrendIntelligence",
            ),
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


async def _rollback(
    completed_steps: list[BootStep],
    timeout_per_step: float = 10.0,
) -> list[str]:
    """Best-effort rollback with per-step timeout. Returns names whose
    rollback failed or timed out."""
    logger.warning("Rolling back %d steps...", len(completed_steps))
    failed: list[str] = []
    for step in reversed(completed_steps):
        if not step.rollback:
            continue
        try:
            result = step.rollback()
            if asyncio.iscoroutine(result) or asyncio.isfuture(result):
                await asyncio.wait_for(result, timeout=timeout_per_step)
        except asyncio.TimeoutError:
            failed.append(step.name)
            logger.error(
                "Rollback timed out for %s after %.1fs",
                step.name, timeout_per_step,
            )
        except Exception as exc:
            failed.append(step.name)
            logger.error(
                "Rollback failed for %s: %s",
                step.name, _sanitize_error_message(exc),
            )
    return failed


# ─────────────────────────────────────────────────────────────────────────────
# Boot Helpers (behavior preserved from v16.8; only ContentIntelligence added)
# ─────────────────────────────────────────────────────────────────────────────


async def _boot_content_intelligence(container: ServiceContainer) -> None:
    """Register the content-intelligence port with the container.

    The adapter wraps ContentIntelligenceEngine (web search + fetch + LLM
    claim extraction). It is consumed by content_engine.generate_post to
    ground content in external evidence before generation.

    Registration strategy:
      1. If a domain-level ContentIntelligencePort protocol exists, register
         the adapter under that port. This is what consumers should resolve.
      2. Otherwise register under the concrete adapter type.
      3. As a last resort, register under the engine type so a direct
         try_resolve(ContentIntelligenceEngine) works.

    Non-critical by design. Content generation must remain functional if
    the intelligence layer is unavailable (web down, provider down); it
    simply produces ungrounded prose. But the absence is recorded so it
    shows up in boot diagnostics — silently running without grounding is
    how the 2026-10-20 ungrounded listicle reached production.
    """
    try:
        from services.content_intelligence import ContentIntelligenceEngine
    except ImportError as exc:
        logger.warning(
            "ContentIntelligence engine unavailable — content will be "
            "ungrounded: %s",
            _sanitize_error_message(exc),
        )
        raise

    try:
        from services.content_intelligence_adapter import ContentIntelligenceAdapter
    except ImportError as exc:
        logger.warning(
            "ContentIntelligence adapter unavailable — content will be "
            "ungrounded: %s",
            _sanitize_error_message(exc),
        )
        raise

    engine = ContentIntelligenceEngine()
    adapter = ContentIntelligenceAdapter(engine)

    registered_as = "engine"
    # Try the port first. Path is flexible: the consumer resolves the same
    # symbol, so as long as one of these succeeds, the wiring works.
    for module_path, attr in (
        ("domain.content.ports", "ContentIntelligencePort"),
        ("application.ports.content_intelligence", "ContentIntelligencePort"),
        ("services.content_intelligence_port", "ContentIntelligencePort"),
    ):
        try:
            module = importlib.import_module(module_path)
            port = getattr(module, attr, None)
            if port is not None:
                container.register_instance(port, adapter)
                registered_as = f"port:{module_path}.{attr}"
                break
        except ImportError:
            continue
        except Exception as exc:
            logger.debug(
                "ContentIntelligence port registration attempt %s.%s failed: %s",
                module_path, attr, _sanitize_error_message(exc),
            )

    if registered_as == "engine":
        # No port found. Register the adapter under its concrete type so
        # try_resolve(ContentIntelligenceAdapter) works, and also under
        # the engine type as a compatibility fallback for any caller that
        # resolves the engine directly.
        try:
            container.register_instance(ContentIntelligenceAdapter, adapter)
        except Exception:
            pass
        try:
            container.register_instance(ContentIntelligenceEngine, engine)
        except Exception:
            pass
        registered_as = "adapter"

    # Non-critical but observable.
    logger.info(
        "ContentIntelligence ready (registered as %s). Content generation "
        "will be evidence-grounded.",
        registered_as,
    )


async def _boot_event_bus(container: ServiceContainer) -> None:
    from core.unified_bus import UnifiedEventBus, get_unified_bus, set_bus_instance
    from core.event_bus import EventBus, get_event_bus_bridge, EventBusBridge

    # Resolve the composition-root singleton first so boot and legacy callers
    # cannot silently construct separate event-bus subscriber registries.
    bus = container.try_resolve(UnifiedEventBus)
    if bus is None:
        bus = await get_unified_bus()
    else:
        set_bus_instance(bus)

    await bus.start()
    container.register_instance(UnifiedEventBus, bus)

    bridge = get_event_bus_bridge(unified=bus)
    container.register_instance(EventBus, bridge)
    container.register_instance(EventBusBridge, bridge)
    await health_registry.register(_EventBusHealthProvider(bus))
    logger.info("UnifiedEventBus ready (legacy EventBus bridged).")

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
        container.register_instance(
            type(business_automation_event_bridge),
            business_automation_event_bridge,
        )
        logger.info("BusinessAutomationEventBridge wired (event-driven automation).")
    except Exception as exc:
        logger.warning("BusinessAutomationEventBridge wire failed: %s", str(exc)[:300])

    try:
        from core.organism import OrganismRegistry, organism_registry
        organism_registry.set_container(container)
        organism_registry.set_event_bus(bus)
        container.register_instance(OrganismRegistry, organism_registry)

        # The process is not a tenant. Never create or start a workspace
        # organism from a process-wide default during boot. Authenticated
        # request middleware establishes the authoritative workspace first.
        logger.info(
            "OrganismRegistry ready (tenant organisms start after authenticated workspace binding)."
        )

        from organism.event_bridge import OrganismEventBridge
        organism_event_bridge = OrganismEventBridge(organism_registry)
        await organism_event_bridge.start(bus)
        container.register_instance(OrganismEventBridge, organism_event_bridge)
        await shutdown_registry.register(
            "OrganismEventBridge",
            organism_event_bridge.stop,
            timeout_seconds=5.0,
        )
        logger.info("OrganismEventBridge wired (workspace-scoped perception only).")
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
    from services.supabase import (
        get_anon_client_from_settings,
        get_service_client_from_settings,
    )

    if not settings.supabase_enabled:
        logger.info("Supabase disabled — skipping.")
        return

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
        logger.warning(
            "Supabase Python client type unavailable; client not registered in DI container"
        )

    logger.info(
        "Supabase connected (client=%s).",
        "service-role" if settings.supabase_admin_enabled else "anon",
    )

    # PATCH v16.8: wire the Supabase close hook if the module exposes it.
    try:
        from services.supabase import aclose_all as _supabase_aclose
        await shutdown_registry.register(
            "SupabaseClients", _supabase_aclose, timeout_seconds=5.0
        )
    except ImportError:
        pass
    except Exception as exc:
        logger.debug(
            "Supabase shutdown hook registration skipped: %s",
            _sanitize_error_message(exc),
        )


async def _boot_shared_event_bus(container: ServiceContainer) -> None:
    from core.shared_event_bus import init_shared_event_bus, SharedEventBus
    from core.event_provenance_runtime import build_durable_event_provenance_verifier
    from config.settings import settings

    try:
        from supabase import Client as SupabaseClient
        supabase_client = container.try_resolve(SupabaseClient)
    except ImportError:
        supabase_client = None

    source_service = (
        os.getenv("SERVICE_MODE", "")
        or getattr(settings, "service_mode", "full")
    ).strip().lower()
    provenance_verifier = None
    try:
        provenance_verifier = build_durable_event_provenance_verifier()
    except Exception as exc:
        logger.warning(
            "SharedEventBus durable provenance verification disabled: %s",
            _sanitize_error_message(exc),
        )
    bus = init_shared_event_bus(
        supabase_client=supabase_client,
        source_service=source_service,
        provenance_verifier=provenance_verifier,
    )
    container.register_instance(SharedEventBus, bus)
    # Attach the organism bridge to the durable transport when its runtime
    # has already been composed. Invalid/legacy envelopes remain untrusted.
    try:
        from organism.event_bridge import OrganismEventBridge
        organism_event_bridge = container.try_resolve(OrganismEventBridge)
        if organism_event_bridge is not None:
            await organism_event_bridge.start_shared(bus)
    except Exception as exc:
        logger.warning(
            "Organism durable event bridge unavailable: %s",
            _sanitize_error_message(exc),
        )
    await health_registry.register(bus)
    await shutdown_registry.register(
        "SharedEventBus", bus.stop, timeout_seconds=5.0
    )
    logger.info(
        "SharedEventBus ready (transport=%s, source=%s).",
        "supabase" if supabase_client else "disabled",
        source_service,
    )


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

    await shutdown_registry.register(
        "TaskQueue", lambda: tq.stop(timeout=10), timeout_seconds=15.0
    )
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
        await shutdown_registry.register(
            "PublishOutbox", publish_outbox.stop_drainer, timeout_seconds=10.0
        )
        logger.info("PublishOutbox drainer ready")
    except Exception as exc:
        logger.warning("PublishOutbox boot: %s", exc)

    try:
        from core.slo_alerts import slo_alerter
        await slo_alerter.start_monitor(interval_seconds=60.0)
        await shutdown_registry.register(
            "SloAlerter", slo_alerter.stop, timeout_seconds=5.0
        )
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
        await shutdown_registry.register(
            "JobStatsService", service.shutdown, timeout_seconds=10.0
        )
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
        # Health adapters must be attached to the exact module-level singleton
        # supervised by lifecycle.py and used by service_definitions. Resolving
        # MetaEngineV7 from DI here can create a second, unowned instance whose
        # handlers never reach the live supervisor.
        from services.meta_engine import meta_engine

        if meta_engine is None:
            logger.warning("MetaEngine health handlers skipped: MetaEngine unavailable")
            return

        summary = register_all_health_handlers(meta_engine)
        logger.info("MetaEngine health handlers registered on canonical singleton: %s", summary)
    except Exception as exc:
        logger.warning(
            "MetaEngine health handler registration failed: %s",
            _sanitize_error_message(exc),
        )
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
    from services.meta_engine import MetaEngineV7, init_meta_engine
    from core.unified_bus import UnifiedEventBus

    event_bus = container.try_resolve(UnifiedEventBus)
    meta = init_meta_engine(event_bus=event_bus, container=container)
    if inspect.isawaitable(meta):
        meta = await meta

    # Publish the lifecycle-owned singleton into DI so every subsystem that
    # resolves MetaEngineV7 observes the same supervisor instance.
    container.register_instance(MetaEngineV7, meta)

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
        await shutdown_registry.register(
            "Director", director.stop, timeout_seconds=10.0
        )
    except Exception as exc:
        logger.warning("Director shutdown register: %s", exc)

    try:
        await health_registry.register(director)
    except Exception as exc:
        logger.warning("Director health registry: %s", exc)

    logger.info("👔 Director: %d engines under supervision", discovered)

    try:
        from services.director_bindings import (
            bind_director_capabilities,
            assert_no_unbound_capabilities,
        )

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

    try:
        from services.supabase import supabase_service
        client = (
            getattr(supabase_service, "service_client", None)
            or getattr(supabase_service, "client", None)
        )
        if client is not None and hasattr(ce, "set_supabase_client"):
            ce.set_supabase_client(client)

        from council.integration.condition_engine_integration import (
            bind_condition_engine_with_council,
        )
        bind_condition_engine_with_council(ce)
        logger.info("ConditionEngine governed executor wired through Council")
    except Exception as exc:
        try:
            from services.condition_engine import default_action_executor
            ce.set_action_executor(default_action_executor)
        except Exception:
            pass
        logger.error(
            "ConditionEngine governed executor unavailable; execution fenced: %s",
            exc,
        )


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
            logger.warning(
                "Supabase client unavailable for Autonomous Content: %s", exc
            )

        engine = await init_autonomous_content(
            supabase_client=supabase_client,
            shared_bus=event_bus,
            container=container,
        )

        await shutdown_registry.register(
            "AutonomousContentEngine", engine.stop, timeout_seconds=10.0
        )
        await health_registry.register(engine)
        logger.info("🚀 Autonomous Content Engine started")
    except ImportError as exc:
        logger.warning("Autonomous Content Engine not available: %s", exc)
    except Exception as exc:
        logger.error("Autonomous Content Engine boot failed: %s", exc)
        raise


async def _boot_autonomous_loop(app, container: ServiceContainer) -> None:
    """Create the AutonomousLoop and install the V71.16 workforce bridge.

    V71.16.1 hardening — the bridge must be installed AFTER the loop exists.
    """
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
            from services.v71_16_autonomous_loop_workforce_bridge import (
                install_autonomous_loop_workforce_bridge,
            )
            install_autonomous_loop_workforce_bridge(app, container)
            logger.info("V71.16 AutonomousLoop workforce bridge installed.")
        except Exception as bridge_exc:
            logger.exception(
                "V71.16 workforce bridge install failed: %s", bridge_exc
            )
            raise

        # The canonical DreamSystem receives its platform council in main.py.
        # Do not manufacture a tenant organism here just to obtain a council:
        # tenant organisms are created only after authenticated workspace binding.

        await shutdown_registry.register(
            "AutonomousLoop", loop.stop, timeout_seconds=10.0
        )
        await health_registry.register(loop)
        logger.info("🔄 Autonomous Loop started — system is self-driving")
    except ImportError as exc:
        logger.warning("Autonomous Loop not available: %s", exc)
    except Exception as exc:
        logger.error("Autonomous Loop boot failed: %s", exc)
        raise


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
            await shutdown_registry.register(
                "GrowthbridgePlugin", plugin.shutdown, timeout_seconds=10.0
            )

        if any(
            hasattr(plugin, attr)
            for attr in ("check_health", "check_health_async", "health_check")
        ):
            await health_registry.register(plugin)

        logger.info("Growthbridge plugin loaded (standalone)")
    except ImportError:
        logger.debug("Growthbridge plugin not available")
    except Exception as exc:
        logger.warning(
            "Growthbridge boot failed: %s", _sanitize_error_message(exc)
        )


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
        if (
            not os.path.isdir(folder_path)
            or folder.startswith("__")
            or folder in ("base", "specs", "addons")
        ):
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
                continue

            plugin_instance = plugin_class(
                supabase_client=supabase_client, redis_client=redis_client
            )
            plugins[folder] = plugin_instance
        except ImportError:
            pass
        except Exception as exc:
            logger.warning(
                "Plugin '%s' failed: %s", folder, _sanitize_error_message(exc)
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
        logger.info("Registered %d plugin commands.", total)
    except ImportError:
        pass
    except Exception as exc:
        logger.warning(
            "Plugin command registration failed: %s", _sanitize_error_message(exc)
        )


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
                event_name = (
                    event_data.get("event_name", "unknown")
                    if isinstance(event_data, dict)
                    else "event"
                )
                payload = (
                    event_data.get("payload", event_data)
                    if isinstance(event_data, dict)
                    else {"data": event_data}
                )
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
        logger.warning(
            "SocketIO bridge failed: %s", _sanitize_error_message(exc)
        )


async def _boot_it_reconciliation_scheduler(container: ServiceContainer) -> None:
    """Start durable IT deployment reconciliation on the existing worker lifecycle."""
    try:
        from plugins.industries.it_operations.repository import ITOperationsRepository
        from scheduler.it_reconciliation_scheduler import build_it_reconciliation_scheduler
        from services.supabase import get_service_client_from_settings

        supabase_client = await _run_in_thread(get_service_client_from_settings)
        redis_client = None
        try:
            from core.redis_client import UpstashRedisClient
            redis_client = container.try_resolve(UpstashRedisClient)
        except Exception:
            pass

        scheduler = build_it_reconciliation_scheduler(
            repository=ITOperationsRepository(supabase_client),
            redis_client=redis_client,
        )
        if scheduler is None:
            return
        scheduler.start()
        container.register_instance(type(scheduler), scheduler)
        await shutdown_registry.register(
            "ITDeploymentReconciliationScheduler", scheduler.stop, timeout_seconds=10.0
        )
        logger.info("IT deployment reconciliation scheduler started")
    except Exception as exc:
        logger.warning("IT deployment reconciliation scheduler unavailable: %s", _sanitize_error_message(exc))


async def _boot_it_service_remediation_reconciliation_scheduler(container: ServiceContainer) -> None:
    """Start durable IT service-remediation reconciliation on the existing worker lifecycle."""
    try:
        from plugins.industries.it_operations.repository import ITOperationsRepository
        from scheduler.it_service_remediation_reconciliation_scheduler import build_it_service_remediation_reconciliation_scheduler
        from services.supabase import get_service_client_from_settings
        supabase_client = await _run_in_thread(get_service_client_from_settings)
        redis_client = None
        try:
            from core.redis_client import UpstashRedisClient
            redis_client = container.try_resolve(UpstashRedisClient)
        except Exception:
            pass
        scheduler = build_it_service_remediation_reconciliation_scheduler(repository=ITOperationsRepository(supabase_client), redis_client=redis_client)
        if scheduler is None:
            return
        scheduler.start(); container.register_instance(type(scheduler), scheduler)
        await shutdown_registry.register("ITServiceRemediationReconciliationScheduler", scheduler.stop, timeout_seconds=10.0)
        logger.info("IT service-remediation reconciliation scheduler started")
    except Exception as exc:
        logger.warning("IT service-remediation reconciliation scheduler unavailable: %s", _sanitize_error_message(exc))


async def _boot_manufacturing_maintenance_reconciliation_scheduler(container: ServiceContainer) -> None:
    """Start durable Manufacturing maintenance reconciliation on the existing worker lifecycle."""
    try:
        from plugins.industries.manufacturing.repository import ManufacturingRepository
        from scheduler.manufacturing_maintenance_reconciliation_scheduler import build_manufacturing_maintenance_reconciliation_scheduler
        from services.supabase import get_service_client_from_settings
        supabase_client = await _run_in_thread(get_service_client_from_settings)
        redis_client = None
        try:
            from core.redis_client import UpstashRedisClient
            redis_client = container.try_resolve(UpstashRedisClient)
        except Exception:
            pass
        scheduler = build_manufacturing_maintenance_reconciliation_scheduler(repository=ManufacturingRepository(supabase_client), redis_client=redis_client)
        if scheduler is None: return
        scheduler.start(); container.register_instance(type(scheduler), scheduler)
        await shutdown_registry.register("ManufacturingMaintenanceReconciliationScheduler", scheduler.stop, timeout_seconds=10.0)
        logger.info("Manufacturing maintenance reconciliation scheduler started")
    except Exception as exc:
        logger.warning("Manufacturing maintenance reconciliation scheduler unavailable: %s", _sanitize_error_message(exc))


async def _boot_finance_reconciliation_scheduler(container: ServiceContainer) -> None:
    """Start durable Finance payment reconciliation on the existing worker lifecycle."""
    try:
        from plugins.industries.finance.repository import FinanceRepository
        from scheduler.finance_reconciliation_scheduler import build_finance_reconciliation_scheduler
        from services.supabase import get_service_client_from_settings

        supabase_client = await _run_in_thread(get_service_client_from_settings)
        redis_client = None
        try:
            from core.redis_client import UpstashRedisClient
            redis_client = container.try_resolve(UpstashRedisClient)
        except Exception:
            pass
        scheduler = build_finance_reconciliation_scheduler(
            repository=FinanceRepository(supabase_client), redis_client=redis_client
        )
        if scheduler is None:
            return
        scheduler.start()
        container.register_instance(type(scheduler), scheduler)
        await shutdown_registry.register(
            "FinancePaymentReconciliationScheduler", scheduler.stop, timeout_seconds=10.0
        )
        logger.info("Finance payment reconciliation scheduler started")
    except Exception as exc:
        logger.warning("Finance reconciliation scheduler unavailable: %s", _sanitize_error_message(exc))


async def _boot_crm_message_reconciliation_scheduler(container: ServiceContainer) -> None:
    """Start durable CRM message delivery reconciliation on the existing worker lifecycle."""
    try:
        from plugins.industries.crm.repository import CRMRepository
        from scheduler.crm_message_reconciliation_scheduler import build_crm_message_reconciliation_scheduler
        from services.supabase import get_service_client_from_settings

        supabase_client = await _run_in_thread(get_service_client_from_settings)
        redis_client = None
        try:
            from core.redis_client import UpstashRedisClient
            redis_client = container.try_resolve(UpstashRedisClient)
        except Exception:
            pass
        scheduler = build_crm_message_reconciliation_scheduler(
            repository=CRMRepository(supabase_client), redis_client=redis_client
        )
        if scheduler is None:
            return
        scheduler.start()
        container.register_instance(type(scheduler), scheduler)
        await shutdown_registry.register(
            "CRMMessageReconciliationScheduler", scheduler.stop, timeout_seconds=10.0
        )
        logger.info("CRM message reconciliation scheduler started")
    except Exception as exc:
        logger.warning("CRM message reconciliation scheduler unavailable: %s", _sanitize_error_message(exc))


async def _boot_education_parent_message_reconciliation_scheduler(container: ServiceContainer) -> None:
    """Start durable Education parent-message reconciliation on the existing worker lifecycle."""
    try:
        from plugins.industries.education.repository import EducationRepository
        from scheduler.education_parent_message_reconciliation_scheduler import build_education_parent_message_reconciliation_scheduler
        from services.supabase import get_service_client_from_settings
        supabase_client = await _run_in_thread(get_service_client_from_settings)
        redis_client = None
        try:
            from core.redis_client import UpstashRedisClient
            redis_client = container.try_resolve(UpstashRedisClient)
        except Exception:
            pass
        scheduler = build_education_parent_message_reconciliation_scheduler(repository=EducationRepository(supabase_client), redis_client=redis_client)
        if scheduler is None: return
        scheduler.start(); container.register_instance(type(scheduler), scheduler)
        await shutdown_registry.register("EducationParentMessageReconciliationScheduler", scheduler.stop, timeout_seconds=10.0)
        logger.info("Education parent-message reconciliation scheduler started")
    except Exception as exc:
        logger.warning("Education parent-message reconciliation scheduler unavailable: %s", _sanitize_error_message(exc))


async def _boot_healthcare_patient_message_reconciliation_scheduler(container: ServiceContainer) -> None:
    """Start durable Healthcare patient-message reconciliation on the existing worker lifecycle."""
    try:
        from plugins.industries.healthcare.repository import HealthcareRepository
        from scheduler.healthcare_patient_message_reconciliation_scheduler import build_healthcare_patient_message_reconciliation_scheduler
        from services.supabase import get_service_client_from_settings
        supabase_client = await _run_in_thread(get_service_client_from_settings)
        redis_client = None
        try:
            from core.redis_client import UpstashRedisClient
            redis_client = container.try_resolve(UpstashRedisClient)
        except Exception:
            pass
        scheduler = build_healthcare_patient_message_reconciliation_scheduler(repository=HealthcareRepository(supabase_client), redis_client=redis_client)
        if scheduler is None: return
        scheduler.start(); container.register_instance(type(scheduler), scheduler)
        await shutdown_registry.register("HealthcarePatientMessageReconciliationScheduler", scheduler.stop, timeout_seconds=10.0)
        logger.info("Healthcare patient-message reconciliation scheduler started")
    except Exception as exc:
        logger.warning("Healthcare patient-message reconciliation scheduler unavailable: %s", _sanitize_error_message(exc))


async def _boot_commerce_order_notification_reconciliation_scheduler(container: ServiceContainer) -> None:
    """Start durable Commerce order-notification reconciliation on the existing worker lifecycle."""
    try:
        from plugins.industries.commerce.repository import CommerceRepository
        from scheduler.commerce_order_notification_reconciliation_scheduler import build_commerce_order_notification_reconciliation_scheduler
        from services.supabase import get_service_client_from_settings
        supabase_client = await _run_in_thread(get_service_client_from_settings)
        redis_client = None
        try:
            from core.redis_client import UpstashRedisClient
            redis_client = container.try_resolve(UpstashRedisClient)
        except Exception:
            pass
        scheduler = build_commerce_order_notification_reconciliation_scheduler(repository=CommerceRepository(supabase_client), redis_client=redis_client)
        if scheduler is None: return
        scheduler.start(); container.register_instance(type(scheduler), scheduler)
        await shutdown_registry.register("CommerceOrderNotificationReconciliationScheduler", scheduler.stop, timeout_seconds=10.0)
        logger.info("Commerce order-notification reconciliation scheduler started")
    except Exception as exc:
        logger.warning("Commerce order-notification reconciliation scheduler unavailable: %s", _sanitize_error_message(exc))


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

    await shutdown_registry.register(
        "BrandSchedulerManager", manager.stop, timeout_seconds=10.0
    )
    logger.info("BrandSchedulers started")


async def _boot_health_monitor(container: ServiceContainer) -> None:
    from services.health_monitor_service import HealthMonitorService

    hm = HealthMonitorService(container=container)
    result = hm.start()
    if asyncio.iscoroutine(result) or asyncio.isfuture(result):
        await result

    container.register_instance(HealthMonitorService, hm)
    await shutdown_registry.register(
        "HealthMonitorService", hm.stop, timeout_seconds=10.0
    )
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
        logger.warning(
            "%s: could not start: %s", display_name, _sanitize_error_message(exc)
        )
        raise


async def _boot_github_connector(container: ServiceContainer) -> None:
    try:
        from plugins.github_connector.plugin import init_github_connector_plugin
        from services.supabase import get_service_client_from_settings

        client = await _run_in_thread(get_service_client_from_settings)
        plugin = init_github_connector_plugin(supabase_client=client)

        await health_registry.register(_GitHubConnectorHealthProvider(plugin))
        await shutdown_registry.register(
            "GitHubConnector", plugin.shutdown, timeout_seconds=10.0
        )
        logger.info("GitHubConnector: started")
    except ImportError:
        logger.debug("GitHubConnector: not available.")
    except Exception as exc:
        logger.warning(
            "GitHubConnector: could not start: %s", _sanitize_error_message(exc)
        )


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
                        HealthStatus.HEALTHY
                        if status in ("healthy", "ok")
                        else HealthStatus.DEGRADED,
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
