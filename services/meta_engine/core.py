"""
KANYOZA META ENGINE v7.1 — FULLY POLISHED (COMPLETE + FIXED + INTELLIGENT)
============================================================================

RESOURCE-SAFE INTELLIGENT SUPERVISOR — ASYNC-NATIVE

V7.1.1 CHANGES (Additive — nothing removed):
  - ADDED: _reported_engine_status helper — reads what an engine says about
    itself without treating reachability as health. A reachable engine that
    reports "degraded" is alive but not nominal.
  - ADDED: New status branch in check_engine_health that downgrades to
    DEGRADED when an engine self-reports degraded/unhealthy/failed/unavailable/
    error/critical. Previously such engines were marked NOMINAL because they
    responded to the ping.

V7.1 CHANGES (Additive — nothing removed):
  - ADDED: Context-aware health learning (engine + time + resource-mode)
  - ADDED: Seasonal pattern detection (learns which engines fail when)
  - ADDED: Causal chain reasoning (root dependencies with confidence)
  - ADDED: Decay-weighted confidence (recent failures weigh more)
  - ADDED: Adaptive recovery (learns which fixes work)
  - ADDED: Recovery effectiveness tracking
  - ADDED: 7 new introspection endpoints
  - ADDED: Enhanced get_status with learning insights

V7.0.3 CHANGES:
  - FIXED: Topology drift false positives for external services (supabase, redis, etc.)
  - FIXED: Deferred topology validation waits 15s for all services to register
  - ADDED: External service filtering in topology validation
  - ADDED: Filtered topology drift in get_impact_assessment
  - FIXED: Optional plugin services no longer trigger false drift warnings
  - FIXED: Self-services (meta_engine, director, guardian) excluded from drift

V7.0.2 CHANGES:
  - FIXED: _ping_engine_inner returns False for unknown engines (not True)
  - FIXED: check_health_async returns "unknown" on exception (not "degraded")
  - FIXED: WARNING alerts now sent to dashboard
  - FIXED: OFFLINE infra services alert even with 0 errors
  - FIXED: Recovery handler handles async stop/start properly
  - FIXED: Added missing _monitoring_loop_sync method
  - FIXED: _send_telegram_alert indentation and placement
  - FIXED: asyncio.create_task called outside event loop - now uses run_coroutine_threadsafe
  - FIXED: StatePersistence._serialize references _topology_drift - now uses meta_engine_ref
  - ADDED: Synchronous health check methods for MetaEngineV7 self-monitoring
  - FIXED: Self-referential monitoring excluded from registration
  - FIXED: Improved ping logic for services without standard health methods
  - ADDED: _is_service_alive method for better health detection
"""

from __future__ import annotations

import asyncio
import dataclasses
import atexit
import hashlib
import inspect
import json
import logging
import math
import os
import random
import statistics
import threading
import time
import traceback
import uuid

from collections import defaultdict, deque
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from enum import Enum
from statistics import mean, median, pstdev
from typing import Any, Callable, Deque, Dict, Iterable, List, Optional, Set, Tuple

logger = logging.getLogger("kanyoza.meta_engine")

# ═══════════════════════════════════════════════════════════════════════════
# EVENT BUS INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════

try:
    from core.unified_bus import get_unified_bus
    HAS_EVENT_BUS = True
except ImportError:
    HAS_EVENT_BUS = False


async def _publish_event_async(event_name: str, payload: Dict[str, Any]) -> None:
    """Async publish on UnifiedEventBus."""
    if not HAS_EVENT_BUS:
        return
    pl = dict(payload or {})
    pl.setdefault("workspace_id", "platform")
    try:
        bus = await get_unified_bus()
        try:
            await bus.publish(event_name, pl, source="meta_engine", workspace_id="platform")
        except TypeError:
            await bus.publish(event_name, pl)
    except Exception as exc:
        logger.debug("Meta Engine publish failed for %s: %s", event_name, exc)


from .models import (
    AlertLevel,
    Anomaly,
    AnomalyType,
    CachedHealth,
    CircuitState,
    Diagnosis,
    EngineHealth,
    EngineStatus,
    EscalationTier,
    FailurePattern,
    Incident,
    IncidentStatus,
    MetricEvent,
    Prediction,
    RecoveryAttempt,
    RecoveryStatus,
    ResourceMode,
    SystemResources,
    SystemSnapshot,
)
from .utils import clamp, iso, percentile, safe_float, utcnow, _publish_event
from .learning import HealthPatternLearner
from .components import (
    BoundedProbeExecutor,
    BoundedRecoveryExecutor,
    DependencyGraph,
    GlobalAlertRateLimiter,
    IncidentManager,
    ResourceMonitor,
    StatePersistence,
)

class MetaEngineV7:
    """Intelligent, bounded, resource-aware supervisory engine — v7.1 Intelligent."""

    VERSION = "7.1.0"

    # Health thresholds
    ERROR_DEGRADED_THRESHOLD = 0.10
    ERROR_CRITICAL_THRESHOLD = 0.50
    STALE_AFTER_SECONDS = 3600
    LATENCY_Z_THRESHOLD = 3.0
    ERROR_SPIKE_MULTIPLIER = 3.0
    MIN_SAMPLES_FOR_ANOMALY = 5

    # Recovery
    RECOVERY_COOLDOWN_SECONDS = 300
    MAX_RECOVERY_ATTEMPTS_PER_HOUR = 3
    RECOVERY_MIN_CONFIDENCE = 0.75
    RECOVERY_MAX_RISK_SCORE = 30.0

    # Incidents
    INCIDENT_DEDUP_SECONDS = 300
    INCIDENT_ESCALATION_SECONDS = 1800
    RESOLVED_INCIDENT_RETENTION_SECONDS = 3600

    # Prediction
    PREDICTION_MIN_SAMPLES = 10
    PREDICTION_DECLINE_SLOPE = -0.5
    PREDICTION_CRITICAL_SCORE = 20.0
    PREDICTION_MAX_CYCLES = 20

    # Resource safety
    NORMAL_INTERVAL_SECONDS = 60
    CAUTIOUS_INTERVAL_SECONDS = 90
    PRESSURED_INTERVAL_SECONDS = 180
    EMERGENCY_INTERVAL_SECONDS = 300
    HEALTH_CACHE_SECONDS = 45

    # Bounded buffers
    MAX_HEALTH_HISTORY = 30
    MAX_SNAPSHOTS = 12
    MAX_ERROR_COUNTS = 80
    MAX_SUCCESS_COUNTS = 80
    MAX_LATENCY_SAMPLES = 40
    MAX_METRICS_PER_NAME = 40
    MAX_DIAGNOSES = 60
    MAX_ANOMALIES = 60
    MAX_RECOVERY_HISTORY = 30
    MAX_FAILURE_PATTERNS = 30
    MAX_PREDICTIONS = 20
    MAX_INCIDENTS = 30

    # External safety
    HEALTH_CHECK_TIMEOUT_SECONDS = 3.0
    DB_FAILURE_THRESHOLD = 3
    DB_COOLDOWN_SECONDS = 300

    # False positive prevention
    INFRASTRUCTURE_SERVICES = {"redis", "event_bus", "supabase", "task_queue"}
    INFRA_CONFIRMATION_CYCLES = 3
    INFRA_MIN_ERROR_COUNT = 5
    ALERT_DEDUP_SECONDS = 600

    # External services that should NOT trigger topology drift
    EXTERNAL_SERVICES = {
        "supabase", "redis", "github", "gemini",
        "meta_engine", "director", "guardian", "guardian_scheduler",
        "enterprise_emitters", "event_recorder",
        "backup_plugin", "brand_scheduler", "engagement_tracker",
        "growthbridge_plugin", "monitor_plugin", "trend_plugin",
    }

    # v7.1 — Intelligence toggles
    ENABLE_HEALTH_PATTERN_LEARNING = True
    ENABLE_ADAPTIVE_RECOVERY = True
    ENABLE_TIME_AWARE_RISK = True
    MIN_FAILURES_FOR_PATTERN = 3

    def __init__(self, event_bus: Any = None, container: Any = None, supabase_client: Any = None):
        self.event_bus = event_bus
        self.container = container
        self.supabase = supabase_client

        self._engines: Dict[str, Any] = {}
        self._engine_versions: Dict[str, str] = {}
        self._registration_report: Dict[str, Any] = {"registered": [], "skipped": []}
        self._deps = DependencyGraph()

        self._health_history: Dict[str, Deque[EngineHealth]] = defaultdict(lambda: deque(maxlen=self.MAX_HEALTH_HISTORY))
        self._snapshots: Deque[SystemSnapshot] = deque(maxlen=self.MAX_SNAPSHOTS)
        self._diagnoses: Deque[Diagnosis] = deque(maxlen=self.MAX_DIAGNOSES)
        self._anomalies: Deque[Anomaly] = deque(maxlen=self.MAX_ANOMALIES)
        self._recovery_history: Deque[RecoveryAttempt] = deque(maxlen=self.MAX_RECOVERY_HISTORY)
        self._failure_patterns: Deque[FailurePattern] = deque(maxlen=self.MAX_FAILURE_PATTERNS)
        self._predictions: Deque[Prediction] = deque(maxlen=self.MAX_PREDICTIONS)
        self._incidents: Dict[str, Incident] = {}
        self._error_counts: Dict[str, Deque[datetime]] = defaultdict(lambda: deque(maxlen=self.MAX_ERROR_COUNTS))
        self._success_counts: Dict[str, Deque[datetime]] = defaultdict(lambda: deque(maxlen=self.MAX_SUCCESS_COUNTS))
        self._latency_samples: Dict[str, Deque[Tuple[datetime, float]]] = defaultdict(lambda: deque(maxlen=self.MAX_LATENCY_SAMPLES))
        self._metrics: Dict[str, Dict[str, Deque[MetricEvent]]] = defaultdict(lambda: defaultdict(lambda: deque(maxlen=self.MAX_METRICS_PER_NAME)))
        self._cached_health: Dict[str, CachedHealth] = {}
        self._recovery_handlers: Dict[str, Callable] = {}
        self._health_handlers: Dict[str, Callable] = {}
        self._recovery_timestamps: Dict[str, Deque[datetime]] = defaultdict(lambda: deque(maxlen=10))
        self._circuits: Dict[str, CircuitState] = {}
        self._alert_callback: Optional[Callable] = None
        self._last_alert: Dict[str, float] = {}
        self._unhealthy_cycles: Dict[str, int] = defaultdict(int)

        self._running = False
        self._async_task: Optional[asyncio.Task] = None
        self._lock = threading.RLock()
        self._start_time = utcnow()
        self._last_monitor_cycle: Optional[datetime] = None
        self._cycle_count = 0
        self._current_snapshot: Optional[SystemSnapshot] = None
        self._current_resources = SystemResources()
        self._monitor_interval_seconds = self.NORMAL_INTERVAL_SECONDS
        self._jitter = random.uniform(0, 10)
        self._db_failure_count = 0
        self._db_circuit_open = False
        self._db_circuit_reset_time = 0.0
        self._cycle_running = False
        self._last_full_diagnostic = 0.0
        self._resource_monitor = ResourceMonitor()
        self._probe_executor = BoundedProbeExecutor(timeout_seconds=self.HEALTH_CHECK_TIMEOUT_SECONDS)
        self._recovery_executor = BoundedRecoveryExecutor()
        self._topology_drift = None
        self._persistence = StatePersistence(supabase_client=supabase_client, meta_engine_ref=self)
        self._incident_manager = IncidentManager(self)
        self._alert_limiter = GlobalAlertRateLimiter()
        
        # v7.1 — Intelligence
        self._pattern_learner = HealthPatternLearner()

        self._build_dependency_graph()
        logger.info("KANYOZA META ENGINE v%s initialized — intelligent async-native supervisor", self.VERSION)

    # ═══════════════════════════════════════════════════════════════════
    # HEALTH CHECK METHODS (Self-monitoring)
    # ═══════════════════════════════════════════════════════════════════

    def _director_signals_for_snapshot(self, snapshot: Optional[SystemSnapshot]) -> list:
        """Create advisory signals only from explicit, current supervisor evidence.

        A nominal Meta snapshot can still contain engines marked STALE. In that
        case, request a health scan rather than claiming the stale engine is
        broken or attempting recovery directly. Degraded/unknown snapshots do
        not publish actionable recommendations because Director requires a
        healthy source observation.
        """
        if snapshot is None or snapshot.overall_status != EngineStatus.NOMINAL:
            return []
        engines = getattr(snapshot, "engines", None)
        if not isinstance(engines, dict):
            return []
        stale_names = sorted(
            str(name) for name, health in engines.items()
            if getattr(health, "status", None) == EngineStatus.STALE
        )
        if not stale_names:
            return []
        from core.director_signal_contract import director_signal_metrics
        # Use the common contract helper for envelope validation; this is a
        # recommendation to inspect, not a claim that stale means failed.
        return [{
            "capability": "run_health_scan",
            "description": "Review stale engine observations",
            "params": {},
            "confidence": 0.72,
            "impact_score": 55,
            "mission_priority": 60,
            "rationale": "Meta Engine reports nominal overall status but explicitly marks these engine observations stale: " + ", ".join(stale_names[:10]),
            "success_criteria": [{"observer": "meta_engine", "metric_path": "stale_engines", "operator": "equals", "expected": 0}],
        }]

    def _engine_readiness_report(self) -> Dict[str, Any]:
        """Expose per-engine capability readiness without conflating it with liveness."""
        reports: Dict[str, Any] = {}
        for name, engine in self._engines.items():
            if engine is None:
                continue
            for method_name in ("check_health", "health_check"):
                method = getattr(engine, method_name, None)
                if not callable(method):
                    continue
                try:
                    result = method()
                    if inspect.isawaitable(result):
                        try:
                            result = asyncio.run(result)
                        except RuntimeError:
                            continue
                    if isinstance(result, dict) and isinstance(result.get("readiness"), dict):
                        reports[name] = dict(result["readiness"])
                    break
                except Exception as exc:
                    reports[name] = {"status": "unknown", "error": str(exc)[:160]}
                    break
        return reports

    def check_health(self) -> Dict[str, Any]:
        snapshot = self._current_snapshot
        from core.director_signal_contract import director_signal_metrics
        if snapshot is None:
            return {"status": "starting" if self._running else "stopped", "available": True, "version": self.VERSION, "engine_readiness": self._engine_readiness_report(), "metrics": director_signal_metrics()}
        status = "healthy" if snapshot.overall_status == EngineStatus.NOMINAL else "unknown" if snapshot.overall_status == EngineStatus.UNKNOWN else "degraded"
        engine_states = {
            str(name): str(getattr(getattr(health, "status", None), "value", getattr(health, "status", "unknown")))
            for name, health in getattr(snapshot, "engines", {}).items()
        }
        unknown_engines = sorted(
            name for name, state in engine_states.items()
            if state in {EngineStatus.UNKNOWN.value, EngineStatus.STALE.value}
        )
        degraded_engines = sorted(
            name for name, state in engine_states.items()
            if state == EngineStatus.DEGRADED.value
        )
        offline_engines = sorted(
            name for name, state in engine_states.items()
            if state == EngineStatus.OFFLINE.value
        )
        signals = self._director_signals_for_snapshot(snapshot)
        return {
            "status": status,
            "available": True,
            "version": self.VERSION,
            "health_score": snapshot.health_score,
            "risk_score": snapshot.risk_score,
            "snapshot_status": getattr(snapshot.overall_status, "value", str(snapshot.overall_status)),
            "engine_states": dict(sorted(engine_states.items())),
            "unknown_engines": unknown_engines,
            "degraded_engines": degraded_engines,
            "offline_engines": offline_engines,
            "engine_readiness": self._engine_readiness_report(),
            "metrics": director_signal_metrics(signals),
        }

    def get_status(self) -> Dict[str, Any]:
        snapshot = self._current_snapshot
        if snapshot is None:
            return {"status": "starting", "available": True}
        status = "healthy" if snapshot.overall_status == EngineStatus.NOMINAL else "unknown" if snapshot.overall_status == EngineStatus.UNKNOWN else "degraded"
        return {"status": status, "available": True}

    def get_stats(self) -> Dict[str, Any]:
        snapshot = self._current_snapshot
        if snapshot is None:
            return {"status": "starting", "available": True}
        status = "healthy" if snapshot.overall_status == EngineStatus.NOMINAL else "unknown" if snapshot.overall_status == EngineStatus.UNKNOWN else "degraded"
        return {"status": status, "available": True}

    # ═══════════════════════════════════════════════════════════════════
    # DEPENDENCY GRAPH (Retained)
    # ═══════════════════════════════════════════════════════════════════

    def _build_dependency_graph(self) -> None:
        from core.engine_topology import dependency_pairs
        for engine, deps in dependency_pairs():
            self._deps.add(engine, deps)

    # ═══════════════════════════════════════════════════════════════════
    # LIFECYCLE (Async-Native)
    # ═══════════════════════════════════════════════════════════════════

    @property
    def service_name(self) -> str:
        return "meta_engine"

    async def start(self, interval_seconds: int = None) -> None:
        if interval_seconds is None:
            interval_seconds = self.NORMAL_INTERVAL_SECONDS

        with self._lock:
            if self._running:
                return
            self._running = True
            self._monitor_interval_seconds = max(30, int(interval_seconds))

            loop = asyncio.get_event_loop()
            previous = await loop.run_in_executor(None, self._persistence.load_recent_snapshot)
            if previous:
                logger.info("Meta Engine restored previous state from %s", previous.get("timestamp"))

            self._async_task = asyncio.create_task(self._async_monitoring_loop(), name="meta-engine-supervisor")
            asyncio.create_task(self.validate_topology_deferred())
            _publish_event._main_loop = asyncio.get_running_loop()

            logger.info("KANYOZA META ENGINE v%s started — async-native supervisor", self.VERSION)

    async def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._running = False

        if self._async_task and not self._async_task.done():
            self._async_task.cancel()
            try:
                await self._async_task
            except asyncio.CancelledError:
                pass

        self._async_task = None

        try:
            self._probe_executor.shutdown()
        except Exception:
            pass
        try:
            self._recovery_executor.shutdown()
        except Exception:
            pass

        logger.info("Meta Engine v%s stopped", self.VERSION)

    async def check_health_async(self) -> Dict[str, Any]:
        """Expose the same detailed snapshot through the async health-provider path.

        The health registry prefers this method over check_health(). Keep both
        contracts aligned so engine-level diagnostics are not lost during
        normalization into HealthReport.metadata.
        """
        try:
            return self.check_health()
        except Exception as exc:
            from core.director_signal_contract import director_signal_metrics
            return {
                "status": "unknown",
                "error": str(exc)[:200],
                "message": "Health check could not execute",
                "metrics": director_signal_metrics(),
            }

    async def _async_monitoring_loop(self) -> None:
        while self._running:
            cycle_started = time.monotonic()

            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._run_cycle)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Meta Engine async supervisor loop error")

            interval = self._monitor_interval_seconds
            if self._current_resources.mode == ResourceMode.CAUTIOUS:
                interval = max(interval, self.CAUTIOUS_INTERVAL_SECONDS)
            elif self._current_resources.mode == ResourceMode.PRESSURED:
                interval = max(interval, self.PRESSURED_INTERVAL_SECONDS)
            elif self._current_resources.mode == ResourceMode.EMERGENCY:
                interval = max(interval, self.EMERGENCY_INTERVAL_SECONDS)

            elapsed = time.monotonic() - cycle_started
            sleep_for = max(1.0, interval - elapsed)
            sleep_for += random.uniform(0, 5)

            try:
                await asyncio.sleep(sleep_for)
            except asyncio.CancelledError:
                break

    # ═══════════════════════════════════════════════════════════════════
    # REGISTRATION (Retained)
    # ═══════════════════════════════════════════════════════════════════

    @staticmethod
    def _resolve_engine_instance(engine: Any) -> Any:
        """Accept live instances only; factories must be resolved explicitly by the owner.

        Discovery must never instantiate arbitrary classes or execute request handlers.
        """
        if engine is None:
            return None
        if isinstance(engine, type) or callable(engine):
            if inspect.iscoroutinefunction(engine):
                raise TypeError("Engine factory is async/awaitable; initialize it in the owning lifecycle before registration")
            raise TypeError("Engine export is a class/callable, not a live instance; resolve through an explicit lifecycle adapter")
        return engine

    def register(self, name: str, engine: Any, version: str = "") -> None:
        if not name:
            raise ValueError("Engine name cannot be empty")
        if name == "meta_engine":
            logger.debug("Skipping self-registration of meta_engine")
            return
        resolved = self._resolve_engine_instance(engine)
        if inspect.isawaitable(resolved):
            try:
                resolved.close()
            except Exception:
                pass
            raise TypeError(f"Engine {name!r} resolved to an awaitable; initialize it before registration")
        if resolved is None:
            raise TypeError(f"Engine {name!r} resolved to no live instance")
        with self._lock:
            self._engines[name] = resolved
            self._engine_versions[name] = version or getattr(resolved, "__version__", "") or ""
            self._circuits.setdefault(name, CircuitState(engine=name))

    def register_all_engines(self) -> None:
        report = {"registered": [], "skipped": [], "normalized_aliases": []}
        # The Director observer label is intentionally distinct inside Director,
        # but Meta Engine must use the canonical runtime topology name.
        canonical_aliases = {"meta_director": "director"}
        # Only these named getters are approved to resolve singleton services.
        approved_getters = {
            ("services.job_stats_service", "get_job_stats_service"),
            ("core.render_queue", "get_render_queue"),
            ("core.event_bus", "get_event_bus"),
        }
        try:
            from services.director import ENGINE_REGISTRY
            for module_path, attr_name, display_name, sector in ENGINE_REGISTRY:
                engine_name = canonical_aliases.get(display_name, display_name)
                if engine_name != display_name:
                    report["normalized_aliases"].append({"source_name": display_name, "canonical_name": engine_name})
                if engine_name == "meta_engine":
                    logger.debug("Skipping meta_engine self-registration")
                    continue
                try:
                    module = __import__(module_path, fromlist=[attr_name])
                    engine = getattr(module, attr_name, None)
                    if engine is None:
                        report["skipped"].append({"engine": engine_name, "reason": "missing_export"})
                        continue
                    if (module_path, attr_name) in approved_getters:
                        if not callable(engine):
                            report["skipped"].append({"engine": engine_name, "reason": "approved_getter_not_callable"})
                            continue
                        engine = engine()
                    elif isinstance(engine, type) or callable(engine):
                        report["skipped"].append({"engine": engine_name, "reason": "live_instance_or_explicit_adapter_required"})
                        continue
                    self.register(engine_name, engine)
                    report["registered"].append(engine_name)
                except Exception as exc:
                    report["skipped"].append({"engine": engine_name, "reason": type(exc).__name__})
                    logger.debug("Engine registration skipped %s: %s", engine_name, exc)
        except ImportError:
            self._manual_register()
        if self.container:
            try:
                plugins = getattr(self.container, "industry_plugins", {})
                for name, plugin in plugins.items():
                    if name == "meta_engine":
                        continue
                    try:
                        self.register(name, plugin)
                        report["registered"].append(str(name))
                    except Exception as exc:
                        report["skipped"].append({"engine": str(name), "reason": type(exc).__name__})
            except Exception:
                pass
        self._registration_report = report
        self._topology_drift = None
        self._topology_check_deferred = True
        logger.info("Meta Engine registered %d live engine instances; skipped %d exports (topology validation deferred)", len(self._engines), len(report["skipped"]))

    async def validate_topology_deferred(self) -> None:
        try:
            await asyncio.sleep(15)
            from core.engine_topology import validate_topology
            raw_drift = validate_topology(self._engines.keys())
            filtered_missing = tuple(s for s in raw_drift.missing_runtime_nodes if s not in self.EXTERNAL_SERVICES)
            filtered_invalid = tuple((source, dep) for source, dep in raw_drift.invalid_dependencies if dep not in self.EXTERNAL_SERVICES and source not in self.EXTERNAL_SERVICES)
            self._topology_drift = raw_drift
            has_real_issues = len(filtered_missing) > 0 or len(filtered_invalid) > 0
            if has_real_issues:
                logger.warning("Meta Engine topology drift (filtered): missing=%s invalid=%s", filtered_missing, filtered_invalid)
            else:
                logger.info("Meta Engine topology healthy: %d engines registered (%d external deps filtered)", len(self._engines), len(raw_drift.missing_runtime_nodes) - len(filtered_missing))
        except Exception as exc:
            logger.debug("Deferred topology validation failed: %s", exc)

    def _manual_register(self) -> None:
        registry = [
            ("content_engine", "services.content_engine", "content_engine"),
            ("condition_engine", "services.condition_engine", "autonomous_engine"),
            ("guardian", "services.guardian_service", "guardian_service"),
            ("email_service", "services.email_service", "email_service"),
            ("engagement_tracker", "services.engagement_tracker", "engagement_tracker"),
            ("ai_orchestrator", "ai.orchestrator", "AIOrchestrator"),
            ("job_service", "services.job_service", "JobService"),
            ("job_stats_service", "services.job_stats_service", "get_job_stats_service"),
            ("plugin_registry", "plugins.base", "PluginRegistry"),
            ("event_bus", "core.event_bus", "get_event_bus"),
            ("render_queue", "core.render_queue", "get_render_queue"),
        ]
        for name, module_name, attr_name in registry:
            if name == "meta_engine":
                continue
            try:
                module = __import__(module_name, fromlist=[attr_name])
                engine = getattr(module, attr_name, None)
                if engine is not None:
                    self.register(name, engine)
            except Exception:
                continue

    def set_alert_callback(self, callback: Optional[Callable]) -> None:
        self._alert_callback = callback

    def register_recovery_handler(self, engine_name: str, handler: Callable) -> None:
        if callable(handler):
            self._recovery_handlers[engine_name] = handler

    def register_health_handler(self, engine_name: str, handler: Callable) -> None:
        if callable(handler):
            self._health_handlers[engine_name] = handler

    # ═══════════════════════════════════════════════════════════════════
    # METRICS (Retained)
    # ═══════════════════════════════════════════════════════════════════

    def record_success(self, engine_name: str) -> None:
        self._success_counts[engine_name].append(utcnow())
        if engine_name in self._unhealthy_cycles:
            self._unhealthy_cycles[engine_name] = 0

    def record_error(self, engine_name: str, error: str = "") -> None:
        self._error_counts[engine_name].append(utcnow())

    def record_latency(self, engine_name: str, milliseconds: float) -> None:
        self._latency_samples[engine_name].append((utcnow(), max(0.0, safe_float(milliseconds))))

    def record_metric(self, engine_name: str, metric_name: str, value: float, metadata: Optional[Dict[str, Any]] = None) -> None:
        self._metrics[engine_name][metric_name].append(MetricEvent(timestamp=utcnow(), kind=metric_name, engine=engine_name, value=safe_float(value), metadata=metadata or {}))

    # ═══════════════════════════════════════════════════════════════════
    # HEALTH CHECK (Retained + Intelligent)
    # ═══════════════════════════════════════════════════════════════════

    def check_engine_health(self, name: str, force: bool = False, record_history: bool = True) -> EngineHealth:
        if not force and self._cached_health_valid(name):
            return self._cached_health[name].health

        engine = self._engines.get(name)
        health = EngineHealth(name=name, version=self._engine_versions.get(name, ""))
        health.dependencies = self._deps.get_dependencies(name)
        health.dependents = self._deps.get_dependents(name)

        OFFLINE_AFTER_CYCLES = 3

        if engine is None:
            self._unhealthy_cycles[name] = self._unhealthy_cycles.get(name, 0) + 1
            if self._unhealthy_cycles[name] >= OFFLINE_AFTER_CYCLES and self._cycle_count >= 2:
                health.status = EngineStatus.OFFLINE
                health.health_score = 0.0
                health.risk_score = 100.0
            else:
                health.status = EngineStatus.STALE
                health.health_score = 40.0
                health.risk_score = 40.0
            self._cache_health(name, health)
            if record_history:
                self._health_history[name].append(health)
            # v7.1: Record pattern
            if self.ENABLE_HEALTH_PATTERN_LEARNING:
                self._pattern_learner.record_check(name, health.status, self._current_resources.mode)
            return health

        if not self._ping_engine(engine, name):
            self._unhealthy_cycles[name] = self._unhealthy_cycles.get(name, 0) + 1
            if self._unhealthy_cycles[name] >= OFFLINE_AFTER_CYCLES and self._cycle_count >= 2:
                health.status = EngineStatus.OFFLINE
                health.health_score = 0.0
                health.risk_score = 100.0
            else:
                health.status = EngineStatus.DEGRADED
                health.health_score = 35.0
                health.risk_score = 55.0
            self._cache_health(name, health)
            if record_history:
                self._health_history[name].append(health)
            # v7.1: Record pattern
            if self.ENABLE_HEALTH_PATTERN_LEARNING:
                self._pattern_learner.record_check(name, health.status, self._current_resources.mode)
            return health

        self._unhealthy_cycles[name] = 0
        now = utcnow()

        # v7.1.1: capture what the engine says about itself.
        # Used below in the status decision — a reachable engine that
        # reports "degraded" is alive, but it is not nominal.
        _reported_status = self._reported_engine_status(engine, name)

        five_minutes_ago = now - timedelta(minutes=5)
        one_hour_ago = now - timedelta(hours=1)

        errors = self._error_counts[name]
        successes = self._success_counts[name]
        health.error_count_5m = sum(ts >= five_minutes_ago for ts in errors)
        health.error_count_1h = sum(ts >= one_hour_ago for ts in errors)
        health.success_count_5m = sum(ts >= five_minutes_ago for ts in successes)
        health.success_count_1h = sum(ts >= one_hour_ago for ts in successes)
        health.throughput_5m = health.success_count_5m / 300.0
        health.throughput_1h = health.success_count_1h / 3600.0

        latency_values = [value for timestamp, value in self._latency_samples[name] if timestamp >= one_hour_ago]
        if latency_values:
            health.avg_latency_ms = mean(latency_values)
            health.median_latency_ms = median(latency_values)
            health.p95_latency_ms = percentile(latency_values, 95)

        metrics = self._metrics[name]
        cpu_events = metrics.get("cpu_percent", ())
        if cpu_events:
            recent = list(cpu_events)[-5:]
            if recent:
                health.cpu_percent = mean(event.value for event in recent)

        memory_events = metrics.get("memory_percent", ())
        if memory_events:
            recent = list(memory_events)[-5:]
            if recent:
                health.memory_mb = mean(event.value for event in recent)

        queue_events = metrics.get("queue_depth", ())
        if queue_events:
            recent = list(queue_events)[-1]
            health.queue_depth = int(max(0, recent.value))

        health.anomaly_flags = [anomaly.anomaly_type.value for anomaly in self._anomalies if anomaly.engine == name and not anomaly.resolved][-5:]

        total_operations = health.error_count_1h + health.success_count_1h
        error_rate = health.error_count_1h / max(1, total_operations)

        _DEGRADED_REPORTS = {"degraded", "unhealthy", "failed", "unavailable", "error", "critical", "offline", "stopped"}

        if error_rate >= self.ERROR_CRITICAL_THRESHOLD:
            health.status = EngineStatus.DEGRADED
        elif error_rate >= self.ERROR_DEGRADED_THRESHOLD:
            health.status = EngineStatus.DEGRADED
        elif _reported_status in _DEGRADED_REPORTS:
            # The engine is reachable but reports itself as unhealthy.
            health.status = EngineStatus.DEGRADED
        elif total_operations == 0:
            last_success = successes[-1] if successes else None
            if last_success and (now - last_success).total_seconds() > self.STALE_AFTER_SECONDS:
                health.status = EngineStatus.STALE
            elif _reported_status in {"healthy", "ok", "nominal", "ready", "idle"}:
                # An explicit, successful health contract is evidence of readiness;
                # mere reachability or `available: true` is not.
                health.status = EngineStatus.NOMINAL
            else:
                # No successful operations and no explicit healthy status means
                # health is unknown, not nominal. Do not manufacture green status.
                health.status = EngineStatus.UNKNOWN
        else:
            health.status = EngineStatus.NOMINAL

        health.health_score = self._calculate_health_score(health)
        health.risk_score = self._calculate_risk_score(health)
        health.last_active = successes[-1] if successes else None

        # v7.1 — Boost risk score using time-aware risk
        if self.ENABLE_TIME_AWARE_RISK:
            time_risk = self._pattern_learner.get_time_risk(name)
            if time_risk > 0.3:
                health.risk_score = round(clamp(health.risk_score + time_risk * 20), 2)

        self._cache_health(name, health)
        if record_history:
            self._health_history[name].append(health)

        # v7.1: Record pattern
        if self.ENABLE_HEALTH_PATTERN_LEARNING:
            self._pattern_learner.record_check(name, health.status, self._current_resources.mode)

        return health

    def _engine_has_health_contract(self, engine_name: str) -> bool:
        engine = self._engines.get(engine_name)
        if engine is None:
            return False
        if engine_name in self._health_handlers:
            return True
        return any(callable(getattr(engine, method, None)) for method in ("check_health_async", "health_check", "check_health", "get_status", "get_stats"))

    def _reported_engine_status(self, engine: Any, engine_name: str) -> Optional[str]:
        """Read an engine's own self-reported status without treating it as truth.

        Returns the raw status string (e.g. "healthy", "degraded", "offline"),
        or None if the engine exposes no status contract or fails to respond.

        This is used alongside the liveness ping: a reachable engine that
        reports "degraded" is alive, but it is not nominal.
        """
        if engine is None:
            return None
        try:
            # Registered adapters are the canonical contract for components
            # whose native APIs expose metrics or typed reports rather than a
            # normalized status. Use the same adapter for liveness and status.
            custom_handler = self._health_handlers.get(engine_name)
            if custom_handler is not None:
                try:
                    result = custom_handler(engine)
                    if inspect.isawaitable(result):
                        result = asyncio.run(result)
                    if isinstance(result, dict):
                        status = result.get("status") or result.get("overall")
                    else:
                        status = getattr(result, "status", result)
                    status = getattr(status, "value", status)
                    if isinstance(status, str) and status.strip():
                        return status.strip().lower()
                except Exception as exc:
                    logger.debug("Health adapter failed for %s: %s", engine_name, exc)
            for method_name in ("check_health_async", "check_health", "health_check", "get_status", "get_stats"):
                method = getattr(engine, method_name, None)
                if not callable(method):
                    continue
                try:
                    result = method()
                except Exception:
                    continue
                if inspect.isawaitable(result):
                    try:
                        result = asyncio.run(result)
                    except RuntimeError:
                        continue
                if isinstance(result, dict):
                    status = result.get("status")
                    if isinstance(status, str) and status.strip():
                        return status.strip().lower()
                    overall = result.get("overall")
                    if isinstance(overall, str) and overall.strip():
                        return overall.strip().lower()
                if isinstance(result, str) and result.strip():
                    return result.strip().lower()
                # Some subsystems return typed HealthReport objects rather
                # than dictionaries. Normalize enum-backed status values too.
                typed_status = getattr(result, "status", None)
                typed_status = getattr(typed_status, "value", typed_status)
                if isinstance(typed_status, str) and typed_status.strip():
                    return typed_status.strip().lower()
        except Exception as exc:
            logger.debug("Reported status read failed for %s: %s", engine_name, exc)
        return None

    def _ping_engine(self, engine: Any, engine_name: str) -> bool:
        if engine is None:
            return False
        if isinstance(engine, type):
            return True
        success, result = self._probe_executor.run(self._ping_engine_inner, engine, engine_name)
        return success and bool(result)

    def _ping_engine_inner(self, engine: Any, engine_name: str) -> bool:
        try:
            custom_handler = self._health_handlers.get(engine_name)
            def evaluate(result: Any) -> bool:
                if inspect.isawaitable(result):
                    try:
                        result = asyncio.run(result)
                    except RuntimeError:
                        return True
                if isinstance(result, dict):
                    status = str(result.get("status", "")).lower()
                    if status in {"healthy", "ok", "nominal", "ready", "degraded"}:
                        return True
                    if result.get("running") is True:
                        return True
                    if result.get("available") is True:
                        return True
                    if status in {"stopped", "offline", "failed"}:
                        return False
                return bool(result)

            if custom_handler:
                return evaluate(custom_handler(engine))
            for method_name in ("check_health", "health_check", "get_status", "get_stats", "check_health_async"):
                method = getattr(engine, method_name, None)
                if callable(method):
                    return evaluate(method())
            
            return self._is_service_alive(engine_name, engine)
        except Exception as exc:
            logger.debug("Health probe failed for %s: %s", engine_name, exc)
            return False

    def _is_service_alive(self, name: str, engine: Any) -> bool:
        if inspect.ismodule(engine):
            return True
        if isinstance(engine, type) or callable(engine):
            return True
        
        alive_indicators = ['is_running', 'running', 'alive', 'connected', 'initialized', 'started', 'ready', 'available', 'active']
        
        for indicator in alive_indicators:
            value = getattr(engine, indicator, None)
            if value is not None:
                if callable(value):
                    try:
                        result = value()
                        if isinstance(result, bool):
                            return result
                        if isinstance(result, dict):
                            return result.get('status', '') not in ('stopped', 'offline', 'failed')
                    except Exception:
                        pass
                elif isinstance(value, bool):
                    return value
        
        logger.debug("No health indicators found for %s, assuming alive", name)
        return True

    def _cached_health_valid(self, name: str) -> bool:
        cached = self._cached_health.get(name)
        if cached is None:
            return False
        return time.monotonic() < cached.expires_at

    def _cache_health(self, name: str, health: EngineHealth) -> None:
        self._cached_health[name] = CachedHealth(health=health, expires_at=time.monotonic() + self.HEALTH_CACHE_SECONDS)

    def _calculate_health_score(self, health: EngineHealth) -> float:
        if health.status == EngineStatus.OFFLINE:
            return 0.0
        if health.status == EngineStatus.UNKNOWN:
            return 40.0
        score = 100.0
        if health.status == EngineStatus.DEGRADED:
            score -= 25.0
        elif health.status == EngineStatus.STALE:
            score -= 35.0
        total = health.error_count_1h + health.success_count_1h
        if total:
            error_rate = health.error_count_1h / total
            score -= error_rate * 50.0
        if health.avg_latency_ms > 1000:
            score -= min(25.0, (health.avg_latency_ms - 1000) / 200)
        if health.cpu_percent > 90:
            score -= 10
        if health.memory_mb > 90:
            score -= 10
        if health.queue_depth > 100:
            score -= min(15, health.queue_depth / 20)
        return round(clamp(score), 2)

    def _calculate_risk_score(self, health: EngineHealth) -> float:
        risk = 0.0
        if health.status == EngineStatus.OFFLINE:
            risk += 80
        elif health.status == EngineStatus.DEGRADED:
            risk += 40
        elif health.status == EngineStatus.STALE:
            risk += 30
        elif health.status == EngineStatus.UNKNOWN:
            risk += 40
        total = health.error_count_1h + health.success_count_1h
        if total:
            risk += (health.error_count_1h / total) * 40
        if health.cpu_percent > 90:
            risk += 15
        if health.memory_mb > 90:
            risk += 15
        risk += min(25, len(health.anomaly_flags) * 5)
        return round(clamp(risk), 2)

    # ═══════════════════════════════════════════════════════════════════
    # SCAN & DIAGNOSE (Retained)
    # ═══════════════════════════════════════════════════════════════════

    def _scan_all_engines(self, force: bool = False) -> SystemSnapshot:
        snapshot = SystemSnapshot()
        snapshot.uptime_seconds = (utcnow() - self._start_time).total_seconds()
        snapshot.resources = self._sample_resources()

        health_scores: List[float] = []
        risk_scores: List[float] = []
        engine_names = list(self._engines.keys())

        if snapshot.resources.mode == ResourceMode.EMERGENCY:
            engine_names = engine_names[:max(1, min(5, len(engine_names)))]

        for name in engine_names:
            try:
                health = self.check_engine_health(name, force=force, record_history=True)
                snapshot.engines[name] = health
                health_scores.append(health.health_score)
                risk_scores.append(health.risk_score)

                if health.status == EngineStatus.OFFLINE:
                    if self._should_alert_for_service(name, health):
                        snapshot.critical_alerts += 1
                elif health.status == EngineStatus.DEGRADED:
                    if self._should_alert_for_service(name, health):
                        snapshot.active_alerts += 1
                elif health.status == EngineStatus.STALE:
                    pass
            except Exception as exc:
                logger.debug("Engine scan failed for %s: %s", name, exc)

        for name, health in snapshot.engines.items():
            dependency_penalty = 0
            unhealthy_dependencies = []
            for dependency in health.dependencies:
                dependency_health = snapshot.engines.get(dependency)
                if not dependency_health:
                    continue
                if dependency_health.status == EngineStatus.OFFLINE:
                    dependency_penalty += 20
                    unhealthy_dependencies.append(dependency)
                elif dependency_health.status == EngineStatus.DEGRADED:
                    dependency_penalty += 10
                    unhealthy_dependencies.append(dependency)
            health.risk_score = round(clamp(health.risk_score + dependency_penalty), 2)

            if health.status in (EngineStatus.OFFLINE, EngineStatus.DEGRADED):
                if unhealthy_dependencies:
                    health.failure_class = ("secondary_cascade" if health.status == EngineStatus.OFFLINE else "degraded_due_to_dependency")
                    health.root_dependency = unhealthy_dependencies[0]
                elif self._engine_has_health_contract(name):
                    health.failure_class = "primary_failure"
                else:
                    health.failure_class = "unobservable"
            elif health.status == EngineStatus.UNKNOWN:
                health.failure_class = "unknown"
            else:
                health.failure_class = "healthy"

        snapshot.health_score = round(mean(health_scores), 2) if health_scores else 0.0
        snapshot.risk_score = round(mean(risk_scores), 2) if risk_scores else 0.0

        if snapshot.critical_alerts:
            snapshot.overall_status = EngineStatus.DEGRADED
        elif snapshot.active_alerts:
            snapshot.overall_status = EngineStatus.DEGRADED
        elif any(health.status == EngineStatus.UNKNOWN for health in snapshot.engines.values()):
            # Do not publish a nominal system-wide status while registered
            # engines have no trustworthy health evidence.
            snapshot.overall_status = EngineStatus.UNKNOWN
        else:
            snapshot.overall_status = EngineStatus.NOMINAL

        snapshot.active_incidents = sum(incident.status not in (IncidentStatus.RESOLVED,) for incident in self._incidents.values())

        self._snapshots.append(snapshot)
        self._current_snapshot = snapshot
        self._last_monitor_cycle = snapshot.timestamp

        _publish_event("service_heartbeat", {"service": "meta", "status": snapshot.overall_status.value, "health_score": snapshot.health_score})

        return snapshot

    def _sample_resources(self) -> SystemResources:
        resources = self._resource_monitor.sample()
        self._current_resources = resources

        if resources.mode == ResourceMode.NORMAL:
            interval = self.NORMAL_INTERVAL_SECONDS
        elif resources.mode == ResourceMode.CAUTIOUS:
            interval = self.CAUTIOUS_INTERVAL_SECONDS
        elif resources.mode == ResourceMode.PRESSURED:
            interval = self.PRESSURED_INTERVAL_SECONDS
        else:
            interval = self.EMERGENCY_INTERVAL_SECONDS

        self._monitor_interval_seconds = interval
        return resources

    def _is_infrastructure_service(self, name: str) -> bool:
        return name in self.INFRASTRUCTURE_SERVICES or name.startswith("redis") or name.startswith("event_bus")

    def _should_alert_for_service(self, name: str, health: EngineHealth) -> bool:
        if not self._is_infrastructure_service(name):
            return True
        consecutive = self._unhealthy_cycles[name]
        if consecutive < self.INFRA_CONFIRMATION_CYCLES:
            return False
        if health.status == EngineStatus.OFFLINE:
            return True
        if health.error_count_1h < self.INFRA_MIN_ERROR_COUNT:
            return False
        return True

    def detect_anomalies(self, snapshot: Optional[SystemSnapshot] = None) -> List[Anomaly]:
        if snapshot is None:
            snapshot = self._current_snapshot
        if snapshot is None:
            return []

        detected: List[Anomaly] = []

        for name, health in snapshot.engines.items():
            latency_values = [value for _, value in self._latency_samples[name]]
            if len(latency_values) >= self.MIN_SAMPLES_FOR_ANOMALY:
                average = mean(latency_values)
                standard_deviation = pstdev(latency_values)
                recent_values = [value for timestamp, value in self._latency_samples[name] if timestamp >= utcnow() - timedelta(minutes=5)]
                if recent_values and standard_deviation > 0:
                    recent_average = mean(recent_values)
                    z_score = (recent_average - average) / standard_deviation
                    if z_score >= self.LATENCY_Z_THRESHOLD:
                        detected.append(Anomaly(
                            id="an_" + uuid.uuid4().hex[:10], engine=name,
                            anomaly_type=AnomalyType.LATENCY_SPIKE,
                            description=f"Latency {z_score:.1f}σ above baseline",
                            severity=AlertLevel.WARNING,
                            observed_value=recent_average, expected_value=average,
                            deviation=z_score, confidence=min(1.0, z_score / 6.0),
                        ))

            errors_5m = health.error_count_5m
            errors_1h = health.error_count_1h
            baseline_5m = errors_1h / 12.0 if errors_1h else 0.0

            if errors_5m >= 3 and (baseline_5m == 0 or errors_5m >= baseline_5m * self.ERROR_SPIKE_MULTIPLIER):
                if self._is_infrastructure_service(name) and errors_5m < self.INFRA_MIN_ERROR_COUNT:
                    continue
                detected.append(Anomaly(
                    id="an_" + uuid.uuid4().hex[:10], engine=name,
                    anomaly_type=AnomalyType.ERROR_SPIKE,
                    description=f"{errors_5m} errors in five minutes",
                    severity=AlertLevel.CRITICAL if errors_5m >= 10 else AlertLevel.WARNING,
                    observed_value=errors_5m, expected_value=baseline_5m,
                    deviation=errors_5m / max(baseline_5m, 1.0), confidence=0.90,
                ))

        for anomaly in detected:
            duplicate = False
            for existing in reversed(self._anomalies):
                if (existing.engine == anomaly.engine and existing.anomaly_type == anomaly.anomaly_type and not existing.resolved and (utcnow() - existing.detected_at).total_seconds() < 300):
                    duplicate = True
                    break
            if not duplicate:
                self._anomalies.append(anomaly)

        return detected

    def diagnose(self, snapshot: Optional[SystemSnapshot] = None) -> List[Diagnosis]:
        if snapshot is None:
            snapshot = self._current_snapshot
        if snapshot is None:
            return []

        diagnoses: List[Diagnosis] = []

        for name, health in snapshot.engines.items():
            if health.status not in (EngineStatus.OFFLINE, EngineStatus.DEGRADED):
                continue
            if self._is_infrastructure_service(name) and not self._should_alert_for_service(name, health):
                continue

            root_cause, chain, confidence = self._find_root_cause(name, snapshot)

            if health.status == EngineStatus.OFFLINE:
                severity = AlertLevel.CRITICAL
                problem = "offline"
            else:
                total = health.error_count_1h + health.success_count_1h
                error_rate = health.error_count_1h / max(total, 1)
                severity = AlertLevel.CRITICAL if error_rate >= self.ERROR_CRITICAL_THRESHOLD else AlertLevel.WARNING
                problem = "degraded"

            diagnosis = Diagnosis(
                id="diag_" + uuid.uuid4().hex[:10], engine=name,
                symptom=f"{name} {health.status.value}", root_cause=root_cause,
                severity=severity,
                affected_engines=self._deps.get_dependents(name),
                dependency_chain=chain,
                recommended_action=self._recommend_fix(name, problem),
                auto_fixable=self._can_auto_fix(name, problem),
                confidence=confidence, risk_score=health.risk_score,
            )
            diagnosis.escalation = self._determine_escalation(diagnosis)
            diagnoses.append(diagnosis)

        self._diagnoses.extend(diagnoses)

        for diagnosis in diagnoses:
            self._maybe_enrich_diagnosis_narrative(diagnosis)
            _publish_event("engine_alert", {
                "engine": diagnosis.engine,
                "status": diagnosis.severity.value,
                "message": diagnosis.symptom,
                "root_cause": diagnosis.root_cause,
                "narrative": diagnosis.narrative,
            })

        return diagnoses

    def _maybe_enrich_diagnosis_narrative(self, diagnosis: "Diagnosis") -> None:
        import os
        if os.environ.get("META_LLM_NARRATIVE", "").lower() not in ("1", "true", "yes"):
            return
        try:
            from ai.gateway import get_llm_gateway
            prompt = (
                "In 2-3 sentences, explain this platform diagnosis for an operator. "
                "Do not invent causes not in the facts.\n"
                f"Engine: {diagnosis.engine}\n"
                f"Symptom: {diagnosis.symptom}\n"
                f"Root cause (deterministic): {diagnosis.root_cause}\n"
                f"Recommended action: {diagnosis.recommended_action}\n"
                f"Confidence: {diagnosis.confidence}\n"
            )
            result = get_llm_gateway().complete_structured(
                prompt + "\nReturn JSON: {\"summary\": \"...\", \"risk\": \"low|medium|high\"}",
                workspace_id="platform",
                temperature=0.3,
                max_tokens=200,
                caller="meta",
                required_keys=["summary"],
            )
            if result.ok and result.data:
                diagnosis.narrative = str(result.data.get("summary") or "")[:1000]
            elif result.ok and result.text:
                diagnosis.narrative = result.text.strip()[:1000]
        except Exception as exc:
            logger.debug("Meta LLM narrative skipped: %s", exc)

    def _find_root_cause(self, engine_name: str, snapshot: SystemSnapshot) -> Tuple[str, List[str], float]:
        health = snapshot.engines.get(engine_name)
        if not health:
            return ("Engine not present in snapshot", [], 0.0)

        chain = [engine_name]
        visited: Set[str] = set()
        current = engine_name

        while current not in visited:
            visited.add(current)
            dependencies = self._deps.get_dependencies(current)
            unhealthy = []
            for dependency in dependencies:
                dependency_health = snapshot.engines.get(dependency)
                if dependency_health and dependency_health.status in (EngineStatus.OFFLINE, EngineStatus.DEGRADED):
                    unhealthy.append(dependency)

            if not unhealthy:
                break

            offline = [dependency for dependency in unhealthy if snapshot.engines[dependency].status == EngineStatus.OFFLINE]
            current = offline[0] if offline else unhealthy[0]
            chain.append(current)

        if len(chain) > 1:
            return ("Dependency failure: " + " → ".join(chain), chain, 0.92)
        if health.error_count_1h > 10:
            return ("High error rate", chain, 0.72)
        if health.avg_latency_ms > 5000:
            return ("Severe latency", chain, 0.78)
        if health.status == EngineStatus.DEGRADED:
            return ("Engine degradation", chain, 0.60)
        return ("No definitive root cause", chain, 0.35)

    def _recommend_fix(self, engine_name: str, problem: str) -> str:
        # v7.1 — Use learned best action if available
        if self.ENABLE_ADAPTIVE_RECOVERY:
            best = self._pattern_learner.get_best_recovery_action(engine_name)
            if best:
                return f"Recover {engine_name} via {best} (learned optimal)"
        
        recommendations = {
            "offline": f"Recover {engine_name}, then verify health",
            "degraded": f"Inspect {engine_name} error rate and dependencies",
        }
        return recommendations.get(problem, f"Investigate {engine_name}")

    def _can_auto_fix(self, engine_name: str, problem: str) -> bool:
        if self._current_resources.mode in (ResourceMode.PRESSURED, ResourceMode.EMERGENCY):
            return False
        if engine_name not in self._recovery_handlers:
            return False
        return self._recovery_allowed(engine_name)

    def _recovery_allowed(self, engine_name: str) -> bool:
        timestamps = self._recovery_timestamps[engine_name]
        cutoff = utcnow() - timedelta(hours=1)
        while timestamps and timestamps[0] < cutoff:
            timestamps.popleft()
        return len(timestamps) < self.MAX_RECOVERY_ATTEMPTS_PER_HOUR

    def _determine_escalation(self, diagnosis: Diagnosis) -> EscalationTier:
        if diagnosis.severity == AlertLevel.WARNING:
            return EscalationTier.DASHBOARD
        if diagnosis.severity == AlertLevel.CRITICAL and diagnosis.auto_fixable and diagnosis.confidence >= self.RECOVERY_MIN_CONFIDENCE:
            return EscalationTier.TELEGRAM
        if diagnosis.severity == AlertLevel.CRITICAL:
            return EscalationTier.EMAIL
        return EscalationTier.DASHBOARD

    # ═══════════════════════════════════════════════════════════════════
    # CASCADING & RECOVERY (Retained + Intelligent)
    # ═══════════════════════════════════════════════════════════════════

    def detect_cascading_failures(self, snapshot: Optional[SystemSnapshot] = None) -> List[Diagnosis]:
        if snapshot is None:
            snapshot = self._current_snapshot
        if snapshot is None:
            return []

        results: List[Diagnosis] = []

        for root in self._deps.nodes():
            root_health = snapshot.engines.get(root)
            if not root_health or root_health.status not in (EngineStatus.OFFLINE, EngineStatus.DEGRADED):
                continue
            if self._is_infrastructure_service(root) and not self._should_alert_for_service(root, root_health):
                continue

            affected: List[str] = []
            queue = deque(self._deps.get_dependents(root))
            visited: Set[str] = set()

            while queue:
                current = queue.popleft()
                if current in visited:
                    continue
                visited.add(current)
                current_health = snapshot.engines.get(current)
                if current_health and current_health.status in (EngineStatus.OFFLINE, EngineStatus.DEGRADED):
                    affected.append(current)
                queue.extend(self._deps.get_dependents(current))

            if len(affected) >= 2:
                results.append(Diagnosis(
                    id="cascade_" + uuid.uuid4().hex[:10], engine=root,
                    symptom=f"{len(affected)} dependent engines affected",
                    root_cause=f"Potential cascading failure originating at {root}",
                    severity=AlertLevel.CRITICAL,
                    affected_engines=affected[:20],
                    dependency_chain=[root] + affected[:20],
                    recommended_action=f"Stabilize {root} before dependent engines",
                    auto_fixable=self._can_auto_fix(root, "degraded"),
                    confidence=0.88,
                ))

        return results

    def _perform_recovery(self, diagnosis: Diagnosis) -> RecoveryAttempt:
        started = utcnow()
        attempt = RecoveryAttempt(
            id="rec_" + uuid.uuid4().hex[:10], engine=diagnosis.engine,
            reason=diagnosis.symptom, started_at=started,
            action=diagnosis.recommended_action, status=RecoveryStatus.RUNNING,
        )

        handler = self._recovery_handlers.get(diagnosis.engine)
        if not handler:
            attempt.status = RecoveryStatus.FAILED
            attempt.error = "No recovery handler registered"
            attempt.finished_at = utcnow()
            if self.ENABLE_ADAPTIVE_RECOVERY:
                self._pattern_learner.record_recovery(diagnosis.engine, diagnosis.recommended_action, False)
            return attempt

        success = False
        try:
            result = handler(diagnosis)
            if inspect.isawaitable(result):
                result = asyncio.run(result)
            if isinstance(result, dict) and result.get("ok") is False:
                raise RuntimeError(result.get("error") or "Recovery handler rejected repair")
            attempt.status = RecoveryStatus.SUCCESS
            attempt.finished_at = utcnow()
            self._cached_health.pop(diagnosis.engine, None)
            health = self.check_engine_health(diagnosis.engine, force=True, record_history=False)
            if health.status == EngineStatus.NOMINAL:
                attempt.status = RecoveryStatus.VERIFIED
                attempt.verification_passed = True
                success = True
            else:
                attempt.status = RecoveryStatus.FAILED
                attempt.error = "Recovery completed but health verification failed"
        except Exception as exc:
            attempt.status = RecoveryStatus.FAILED
            attempt.error = f"{type(exc).__name__}: {exc}"
            logger.error("Recovery failed for %s: %s", diagnosis.engine, exc)

        # v7.1 — Record recovery effectiveness
        if self.ENABLE_ADAPTIVE_RECOVERY:
            self._pattern_learner.record_recovery(diagnosis.engine, diagnosis.recommended_action, success)

        attempt.finished_at = utcnow()
        return attempt

    def _start_recovery(self, diagnosis: Diagnosis) -> bool:
        if self._current_resources.mode in (ResourceMode.PRESSURED, ResourceMode.EMERGENCY):
            return False
        if not diagnosis.auto_fixable or diagnosis.confidence < self.RECOVERY_MIN_CONFIDENCE or diagnosis.risk_score > self.RECOVERY_MAX_RISK_SCORE:
            return False
        if not self._recovery_allowed(diagnosis.engine):
            return False
        if self._recovery_executor.busy():
            return False

        self._recovery_timestamps[diagnosis.engine].append(utcnow())
        future = self._recovery_executor.submit(self._perform_recovery, diagnosis)
        if future is None:
            return False

        def _complete(completed: Future) -> None:
            try:
                attempt = completed.result()
                self._recovery_history.append(attempt)
                for incident in self._incidents.values():
                    if incident.primary_engine == diagnosis.engine and incident.status not in (IncidentStatus.RESOLVED,):
                        incident.recovery_attempts.append(attempt.id)
                        incident.status = IncidentStatus.HEALING
                        incident.updated_at = utcnow()
            except Exception:
                logger.exception("Meta Engine recovery callback failed")

        future.add_done_callback(_complete)
        return True

    # ═══════════════════════════════════════════════════════════════════
    # PREDICTIONS (Retained)
    # ═══════════════════════════════════════════════════════════════════

    def predict_failures(self, snapshot: Optional[SystemSnapshot] = None) -> List[Prediction]:
        predictions: List[Prediction] = []
        if snapshot is None:
            snapshot = self._current_snapshot
        if snapshot is None:
            return predictions

        for name in snapshot.engines:
            history = list(self._health_history[name])
            if len(history) < self.PREDICTION_MIN_SAMPLES:
                continue

            scores = [health.health_score for health in history[-20:]]
            count = len(scores)
            if count < 2:
                continue

            x_mean = (count - 1) / 2
            y_mean = mean(scores)
            numerator = sum((index - x_mean) * (scores[index] - y_mean) for index in range(count))
            denominator = sum((index - x_mean) ** 2 for index in range(count))
            if denominator == 0:
                continue

            slope = numerator / denominator
            if slope >= self.PREDICTION_DECLINE_SLOPE:
                continue

            current_score = scores[-1]
            cycles_to_failure = (current_score - self.PREDICTION_CRITICAL_SCORE) / abs(slope)
            if cycles_to_failure <= 0 or cycles_to_failure > self.PREDICTION_MAX_CYCLES:
                continue

            confidence = clamp(abs(slope) * 50, 0, 90) / 100
            predictions.append(Prediction(
                engine=name,
                predicted_failure=f"Critical in approximately {cycles_to_failure:.0f} cycles",
                confidence=confidence,
                estimated_time_to_failure_minutes=cycles_to_failure * (self._monitor_interval_seconds / 60.0),
                contributing_factors=[f"health slope={slope:.2f}", f"current score={current_score:.1f}"],
            ))

        predictions.sort(key=lambda prediction: prediction.estimated_time_to_failure_minutes)
        self._predictions = deque(predictions[:self.MAX_PREDICTIONS], maxlen=self.MAX_PREDICTIONS)
        return predictions

    # ═══════════════════════════════════════════════════════════════════
    # API COMPATIBILITY (Retained + v7.1 Enhancements)
    # ═══════════════════════════════════════════════════════════════════

    def get_dependency_graph(self) -> Dict[str, Any]:
        return {
            "edges": {name: self._deps.get_dependencies(name) for name in self._deps.edges},
            "reverse": {name: self._deps.get_dependents(name) for name in self._deps.reverse},
            "nodes": sorted(self._deps.nodes()),
            "total_nodes": len(self._deps.nodes()),
        }

    def get_root_cause(self) -> Optional[Dict[str, Any]]:
        if not self._diagnoses:
            return None
        latest = self._diagnoses[-1]
        return {
            "id": latest.id,
            "engine": latest.engine,
            "symptom": latest.symptom,
            "root_cause": latest.root_cause,
            "severity": latest.severity.value,
            "confidence": latest.confidence,
            "affected_engines": latest.affected_engines,
            "dependency_chain": latest.dependency_chain,
            "recommended_action": latest.recommended_action,
            "detected_at": iso(latest.detected_at),
            "resolved_at": iso(latest.resolved_at),
        }

    def get_cascading_predictions(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": diagnosis.id,
                "engine": diagnosis.engine,
                "symptom": diagnosis.symptom,
                "root_cause": diagnosis.root_cause,
                "affected_engines": diagnosis.affected_engines,
                "confidence": diagnosis.confidence,
                "detected_at": iso(diagnosis.detected_at),
            }
            for diagnosis in self._diagnoses
            if diagnosis.id.startswith("cascade_")
        ][-10:]

    def get_impact_assessment(self) -> Dict[str, Any]:
        snapshot = self._current_snapshot
        if not snapshot:
            return {}
        
        topology_info = None
        if self._topology_drift:
            real_missing = [s for s in self._topology_drift.missing_runtime_nodes if s not in self.EXTERNAL_SERVICES]
            real_invalid = [
                {"source": s, "dependency": d}
                for s, d in self._topology_drift.invalid_dependencies
                if d not in self.EXTERNAL_SERVICES and s not in self.EXTERNAL_SERVICES
            ]
            topology_info = {
                "missing": real_missing,
                "undeclared": self._topology_drift.undeclared_runtime_nodes,
                "invalid_dependencies": real_invalid,
                "cycles": self._topology_drift.cycles,
                "healthy": len(real_missing) == 0 and len(real_invalid) == 0,
            }
        
        return {
            "overall_status": snapshot.overall_status.value,
            "health_score": snapshot.health_score,
            "risk_score": snapshot.risk_score,
            "critical_alerts": snapshot.critical_alerts,
            "active_alerts": snapshot.active_alerts,
            "active_incidents": snapshot.active_incidents,
            "uptime_seconds": snapshot.uptime_seconds,
            "resources": {
                "rss_mb": snapshot.resources.rss_mb,
                "memory_percent": snapshot.resources.memory_percent,
                "cpu_percent": snapshot.resources.cpu_percent,
                "mode": snapshot.resources.mode.value,
            },
            "engine_count": len(snapshot.engines),
            "topology_drift": topology_info,
        }

    def get_recovery_plans(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": attempt.id,
                "engine": attempt.engine,
                "reason": attempt.reason,
                "status": attempt.status.value,
                "action": attempt.action,
                "started_at": iso(attempt.started_at),
                "finished_at": iso(attempt.finished_at),
                "verification_passed": attempt.verification_passed,
            }
            for attempt in self._recovery_history
        ][-20:]

    def get_topology(self) -> Dict[str, Any]:
        return self.get_dependency_graph()

    def get_alerts(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": diagnosis.id,
                "engine": diagnosis.engine,
                "symptom": diagnosis.symptom,
                "severity": diagnosis.severity.value,
                "escalation": diagnosis.escalation.value,
                "detected_at": iso(diagnosis.detected_at),
            }
            for diagnosis in self._diagnoses
        ][-20:]

    def get_incidents(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": incident.id,
                "title": incident.title,
                "severity": incident.severity.value,
                "status": incident.status.value,
                "primary_engine": incident.primary_engine,
                "root_cause": incident.root_cause,
                "created_at": iso(incident.created_at),
                "resolved_at": iso(incident.resolved_at),
            }
            for incident in self._incidents.values()
        ]

    def get_status(self) -> Dict[str, Any]:
        snapshot = self._current_snapshot
        if snapshot is None:
            snapshot = SystemSnapshot(resources=self._current_resources)
        return {
            "version": self.VERSION,
            "timestamp": iso(snapshot.timestamp),
            "overall": snapshot.overall_status.value,
            "health_score": snapshot.health_score,
            "risk_score": snapshot.risk_score,
            "uptime_hours": round(snapshot.uptime_seconds / 3600, 2),
            "engine_count": len(self._engines),
            "resources": {
                "rss_mb": snapshot.resources.rss_mb,
                "memory_percent": snapshot.resources.memory_percent,
                "cpu_percent": snapshot.resources.cpu_percent,
                "available_memory_mb": snapshot.resources.available_memory_mb,
                "disk_percent": snapshot.resources.disk_percent,
                "mode": snapshot.resources.mode.value,
            },
            "engines": {
                name: {
                    "status": health.status.value,
                    "health_score": health.health_score,
                    "risk_score": health.risk_score,
                    "error_count_5m": health.error_count_5m,
                    "error_count_1h": health.error_count_1h,
                    "throughput_5m": health.throughput_5m,
                    "avg_latency_ms": round(health.avg_latency_ms, 2),
                    "p95_latency_ms": round(health.p95_latency_ms, 2),
                    "cpu_percent": round(health.cpu_percent, 1),
                    "memory_mb": round(health.memory_mb, 1),
                    "queue_depth": health.queue_depth,
                    "dependents": health.dependents,
                    "dependencies": health.dependencies,
                    "anomalies": health.anomaly_flags,
                }
                for name, health in snapshot.engines.items()
            },
            "active_incidents": sum(incident.status not in (IncidentStatus.RESOLVED,) for incident in self._incidents.values()),
            "critical_alerts": snapshot.critical_alerts,
            "predictions": [
                {
                    "engine": prediction.engine,
                    "predicted_failure": prediction.predicted_failure,
                    "confidence": prediction.confidence,
                    "eta_minutes": prediction.estimated_time_to_failure_minutes,
                }
                for prediction in list(self._predictions)[:5]
            ],
            "failure_patterns_learned": len(self._failure_patterns),
            "monitoring": {
                "running": self._running,
                "cycle_count": self._cycle_count,
                "last_cycle": iso(self._last_monitor_cycle),
                "interval_seconds": self._monitor_interval_seconds,
                "resource_mode": self._current_resources.mode.value,
                "recovery_worker_busy": self._recovery_executor.busy(),
                "health_probe_worker": "single-bounded-worker",
            },
            "learning": self._pattern_learner.get_learning_report(),
        }

    # ═══════════════════════════════════════════════════════════════════
    # v7.1 — INTELLIGENCE ENDPOINTS
    # ═══════════════════════════════════════════════════════════════════

    def get_learning_report(self) -> Dict[str, Any]:
        """Full pattern learning report."""
        return self._pattern_learner.get_learning_report()
    
    def get_engine_pattern(self, engine_name: str) -> Dict[str, Any]:
        """Deep-dive pattern analysis for one engine."""
        return {
            "engine": engine_name,
            "risky_hours": self._pattern_learner.get_risky_hours(engine_name),
            "current_time_risk": self._pattern_learner.get_time_risk(engine_name),
            "recent_failure_rate": self._pattern_learner.get_recent_failure_rate(engine_name),
            "recovery_effectiveness": self._pattern_learner.get_recovery_stats(engine_name),
            "best_recovery_action": self._pattern_learner.get_best_recovery_action(engine_name),
            "hourly_failure_rate": {
                hour: round(data["failures"] / max(data["checks"], 1), 3)
                for hour, data in self._pattern_learner.engine_hour_stats.get(engine_name, {}).items()
                if data["checks"] >= 3
            },
        }
    
    def get_engine_report(self, engine_name: str) -> Dict[str, Any]:
        health = self.check_engine_health(engine_name, force=False, record_history=False)
        history = list(self._health_history[engine_name])[-20:]
        return {
            "engine": engine_name,
            "current": {
                "status": health.status.value,
                "health_score": health.health_score,
                "risk_score": health.risk_score,
                "error_count_5m": health.error_count_5m,
                "error_count_1h": health.error_count_1h,
                "throughput_5m": health.throughput_5m,
                "avg_latency_ms": health.avg_latency_ms,
                "p95_latency_ms": health.p95_latency_ms,
                "cpu_percent": health.cpu_percent,
                "memory_mb": health.memory_mb,
                "queue_depth": health.queue_depth,
            },
            "dependencies": health.dependencies,
            "dependents": health.dependents,
            "history": [
                {"status": item.status.value, "health_score": item.health_score, "risk_score": item.risk_score, "timestamp": iso(item.checked_at)}
                for item in history
            ],
            "learned_pattern": self.get_engine_pattern(engine_name),
        }

    # ═══════════════════════════════════════════════════════════════════
    # MONITORING CYCLE (Retained)
    # ═══════════════════════════════════════════════════════════════════

    def _expensive_operations_allowed(self) -> bool:
        return self._current_resources.mode in (ResourceMode.NORMAL, ResourceMode.CAUTIOUS)

    def _run_cycle(self) -> None:
        if self._cycle_running:
            logger.warning("Meta Engine skipped overlapping cycle")
            return
        self._cycle_running = True
        cycle_started = time.monotonic()
        try:
            self._cycle_count += 1
            resources = self._sample_resources()
            snapshot = self._scan_all_engines(force=True)
            anomalies = self.detect_anomalies(snapshot)
            diagnoses = self.diagnose(snapshot)
            if resources.mode != ResourceMode.EMERGENCY:
                diagnoses.extend(self.detect_cascading_failures(snapshot))
            self._create_incidents(diagnoses)
            for diagnosis in diagnoses:
                if diagnosis.severity == AlertLevel.CRITICAL:
                    self._send_alert(diagnosis)
                    self._schedule_telegram_alert(diagnosis)
                elif diagnosis.severity == AlertLevel.WARNING:
                    self._send_alert(diagnosis)
                elif diagnosis.auto_fixable and diagnosis.confidence >= self.RECOVERY_MIN_CONFIDENCE and diagnosis.risk_score <= self.RECOVERY_MAX_RISK_SCORE:
                    self._start_recovery(diagnosis)
            resolved = self._incident_manager.resolve_healthy_incidents(snapshot)
            escalated = self._incident_manager.escalate_old_incidents()
            self._cleanup_old_incidents()
            now = time.monotonic()
            expensive_due = now - self._last_full_diagnostic >= 300
            if expensive_due and self._expensive_operations_allowed():
                self.predict_failures(snapshot)
                self._last_full_diagnostic = now
            self._persistence.save_snapshot(snapshot)
            self._last_monitor_cycle = utcnow()
            if resolved:
                logger.info("Meta Engine resolved %d incidents", resolved)
            if escalated:
                logger.warning("Meta Engine escalated %d incidents", escalated)
            if anomalies:
                logger.info("Meta Engine detected %d anomalies", len(anomalies))
            elapsed = time.monotonic() - cycle_started
            logger.debug("Meta Engine cycle %d completed in %.2fs (resource mode=%s)", self._cycle_count, elapsed, resources.mode.value)
        except Exception:
            logger.error("Meta Engine cycle failed:\n%s", traceback.format_exc())
        finally:
            self._cycle_running = False

    # ═══════════════════════════════════════════════════════════════════
    # TELEGRAM ALERT SCHEDULER
    # ═══════════════════════════════════════════════════════════════════

    def _schedule_telegram_alert(self, diagnosis: Diagnosis) -> None:
        try:
            loop = asyncio.get_running_loop()
            asyncio.run_coroutine_threadsafe(self._send_telegram_alert(diagnosis), loop)
        except RuntimeError:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.run_coroutine_threadsafe(self._send_telegram_alert(diagnosis), loop)
                else:
                    self._send_telegram_alert_sync(diagnosis)
            except Exception:
                self._send_telegram_alert_sync(diagnosis)

    def _send_telegram_alert_sync(self, diagnosis: Diagnosis) -> None:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._send_telegram_alert(diagnosis))
        except Exception as e:
            logger.warning("Telegram alert sync fallback failed: %s", e)
        finally:
            loop.close()

    async def _send_telegram_alert(self, diagnosis: Diagnosis) -> None:
        if not self._alert_limiter.can_send():
            return
        key = diagnosis.engine + ":" + diagnosis.severity.value
        now = time.monotonic()
        if now - self._last_alert.get(key, 0.0) < 300:
            return
        self._last_alert[key] = now
        self._alert_limiter.record_send()
        try:
            from services.email_service import email_service
            await email_service.send_telegram(
                f"🔴 <b>OVERSEER CRITICAL: {diagnosis.engine}</b>\n"
                f"{diagnosis.symptom}\n{diagnosis.root_cause}\n"
                f"<i>{utcnow().strftime('%H:%M UTC')}</i>"
            )
        except Exception as exc:
            logger.debug("Telegram alert unavailable: %s", exc)

    def _monitoring_loop_sync(self) -> None:
        while self._running:
            cycle_started = time.monotonic()
            try:
                self._run_cycle()
            except Exception:
                logger.exception("Meta Engine sync supervisor loop error")
            interval = self._monitor_interval_seconds
            elapsed = time.monotonic() - cycle_started
            sleep_for = max(1.0, interval - elapsed)
            end = time.monotonic() + sleep_for
            while self._running and time.monotonic() < end:
                time.sleep(min(1.0, max(0.05, end - time.monotonic())))

    def _send_alert(self, diagnosis: Diagnosis) -> None:
        key = diagnosis.engine + ":" + diagnosis.severity.value
        now = time.monotonic()
        if now - self._last_alert.get(key, 0.0) < self.ALERT_DEDUP_SECONDS:
            return
        self._last_alert[key] = now
        if not self._alert_callback:
            return
        try:
            self._alert_callback(diagnosis)
        except Exception as exc:
            logger.warning("Alert callback failed: %s", exc)

    def _create_incidents(self, diagnoses: List[Diagnosis]) -> List[Incident]:
        created: List[Incident] = []
        for diagnosis in diagnoses:
            existing = None
            for incident in self._incidents.values():
                if incident.primary_engine != diagnosis.engine:
                    continue
                if incident.status in (IncidentStatus.RESOLVED,):
                    continue
                if (utcnow() - incident.created_at).total_seconds() <= self.INCIDENT_DEDUP_SECONDS:
                    existing = incident
                    break
            if existing:
                if diagnosis.id not in existing.diagnoses:
                    existing.diagnoses.append(diagnosis.id)
                existing.updated_at = utcnow()
                continue
            if len(self._incidents) >= self.MAX_INCIDENTS:
                self._cleanup_old_incidents()
                if len(self._incidents) >= self.MAX_INCIDENTS:
                    continue
            incident = Incident(
                id="inc_" + uuid.uuid4().hex[:10], title=diagnosis.symptom,
                severity=diagnosis.severity, status=IncidentStatus.OPEN,
                primary_engine=diagnosis.engine,
                affected_engines=diagnosis.affected_engines[:20],
                diagnoses=[diagnosis.id], root_cause=diagnosis.root_cause,
                confidence=diagnosis.confidence,
            )
            self._incidents[incident.id] = incident
            created.append(incident)
        return created

    def _cleanup_old_incidents(self) -> None:
        cutoff = utcnow() - timedelta(seconds=self.RESOLVED_INCIDENT_RETENTION_SECONDS)
        for incident_id in list(self._incidents.keys()):
            incident = self._incidents[incident_id]
            if incident.status == IncidentStatus.RESOLVED and incident.resolved_at and incident.resolved_at < cutoff:
                del self._incidents[incident_id]

    def learn_from_incident(self, incident: Incident) -> None:
        if incident.status != IncidentStatus.RESOLVED:
            return
        signature = incident.primary_engine + ":" + incident.root_cause
        existing = next((pattern for pattern in self._failure_patterns if pattern.signature == signature), None)
        if existing:
            existing.occurrences += 1
            existing.last_seen = utcnow()
            if incident.recovery_attempts:
                existing.fix_applied = incident.recovery_attempts[-1]
        else:
            self._failure_patterns.append(FailurePattern(
                id="fp_" + uuid.uuid4().hex[:10], signature=signature,
                root_cause=incident.root_cause,
                fix_applied=incident.recovery_attempts[0] if incident.recovery_attempts else "manual",
                occurrences=1,
            ))


# ═══════════════════════════════════════════════════════════════════════════
# SINGLETON (Retained)
# ═══════════════════════════════════════════════════════════════════════════

meta_engine = MetaEngineV7()


def init_meta_engine(event_bus: Any = None, container: Any = None, supabase_client: Any = None) -> MetaEngineV7:
    """Initialize the Meta Engine singleton."""
    meta_engine.event_bus = event_bus
    meta_engine.container = container
    meta_engine.supabase = supabase_client
    meta_engine._persistence = StatePersistence(supabase_client=supabase_client, meta_engine_ref=meta_engine)
    meta_engine.register_all_engines()

    def generic_restart(diagnosis: Diagnosis) -> None:
        engine = meta_engine._engines.get(diagnosis.engine)
        if not engine:
            raise RuntimeError(f"Unknown engine: {diagnosis.engine}")
        restart = getattr(engine, "restart", None)
        reset = getattr(engine, "reset", None)
        stop = getattr(engine, "stop", None)
        start = getattr(engine, "start", None)
        if callable(restart):
            result = restart()
            if inspect.isawaitable(result):
                asyncio.run(result)
            return
        if callable(reset):
            result = reset()
            if inspect.isawaitable(result):
                asyncio.run(result)
            return
        if callable(stop) and callable(start):
            result = stop()
            if inspect.isawaitable(result):
                asyncio.run(result)
            time.sleep(0.1)
            result = start()
            if inspect.isawaitable(result):
                asyncio.run(result)
            return
        raise RuntimeError(f"No safe recovery interface for {diagnosis.engine}")

    for name, engine in list(meta_engine._engines.items()):
        if any(callable(getattr(engine, method, None)) for method in ("restart", "reset")):
            meta_engine.register_recovery_handler(name, generic_restart)

    logger.info("KANYOZA META ENGINE v%s initialized — monitoring deferred until boot convergence", meta_engine.VERSION)
    return meta_engine


def shutdown_meta_engine() -> None:
    try:
        result = meta_engine.stop()
        if asyncio.iscoroutine(result):
            loop = asyncio.new_event_loop()
            loop.run_until_complete(result)
            loop.close()
    except Exception:
        pass


atexit.register(shutdown_meta_engine)


__all__ = [
    "MetaEngineV7",
    "meta_engine",
    "init_meta_engine",
    "shutdown_meta_engine",
    "HealthPatternLearner",
    "EngineStatus",
    "AlertLevel",
    "IncidentStatus",
    "RecoveryStatus",
    "AnomalyType",
    "EscalationTier",
    "ResourceMode",
    "EngineHealth",
    "SystemSnapshot",
    "Diagnosis",
    "Anomaly",
    "RecoveryAttempt",
    "Incident",
    "Prediction",
    "FailurePattern",
]
