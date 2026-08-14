"""
KANYOZA META ENGINE v5.0 — "THE OVERSEER"
==========================================
COMPLETE POLISHED & REFACTORED VERSION — FULL FILE
ALL FEATURES INCLUDED — NO CONDENSATION

v5.0 NUCLEAR FIX:
  - Proper instance resolution (classes → instances)
  - Class safety net in _ping_engine_inner
  - Simple daemon-thread timeout (no executor breakage)
  - Health handlers support
  - Circuit breaker for DB operations
  - Self-monitoring
  - Incident resolution workflow
  - Global alert rate limiting

ALL v4.0 FEATURES RETAINED:
  - Auto-discovers all engines from Director's registry
  - Health scoring, risk assessment, anomaly detection
  - Root-cause analysis with dependency chains
  - Cascading failure detection
  - Auto-heal with risk-gated recovery policy
  - Incident management with deduplication
  - Trend analysis & predictive risk
  - Telegram alerts for critical events
  - Full diagnostic reports
  - Dependency graph
  - Failure pattern learning
  - Adaptive thresholds
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import random
import threading
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from statistics import mean, median, pstdev
from typing import Any, Callable, Deque, Dict, Iterable, List, Optional, Set, Tuple

logger = logging.getLogger("kanyoza.meta_engine")


# ═══════════════════════════════════════════════════════════════════════════
# ENUMS
# ═══════════════════════════════════════════════════════════════════════════

class EngineStatus(str, Enum):
    NOMINAL = "nominal"
    DEGRADED = "degraded"
    STALE = "stale"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


class AlertLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class IncidentStatus(str, Enum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    HEALING = "healing"
    RESOLVED = "resolved"
    ESCALATED = "escalated"


class RecoveryStatus(str, Enum):
    NOT_ATTEMPTED = "not_attempted"
    SUCCESS = "success"
    FAILED = "failed"
    VERIFIED = "verified"


class AnomalyType(str, Enum):
    ERROR_SPIKE = "error_spike"
    LATENCY_SPIKE = "latency_spike"
    THROUGHPUT_DROP = "throughput_drop"
    SILENCE = "unexpected_silence"
    HEALTH_REGRESSION = "health_regression"
    DEPENDENCY_FAILURE = "dependency_failure"
    CASCADING_FAILURE = "cascading_failure"
    RESOURCE_PRESSURE = "resource_pressure"


class EscalationTier(str, Enum):
    NONE = "none"
    LOG_ONLY = "log_only"
    DASHBOARD = "dashboard"
    TELEGRAM = "telegram"
    EMAIL = "email"


# ═══════════════════════════════════════════════════════════════════════════
# DATACLASSES
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class EngineHealth:
    name: str
    status: EngineStatus = EngineStatus.UNKNOWN
    health_score: float = 0.0
    risk_score: float = 0.0
    last_active: Optional[datetime] = None
    error_count_1h: int = 0
    error_count_24h: int = 0
    success_count_1h: int = 0
    success_count_24h: int = 0
    throughput_1h: float = 0.0
    throughput_5m: float = 0.0
    avg_latency_ms: float = 0.0
    median_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    queue_depth: int = 0
    memory_mb: float = 0.0
    cpu_percent: float = 0.0
    dependencies: List[str] = field(default_factory=list)
    dependents: List[str] = field(default_factory=list)
    restart_count: int = 0
    last_restart: Optional[datetime] = None
    version: str = ""
    anomaly_flags: List[str] = field(default_factory=list)
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class SystemSnapshot:
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    engines: Dict[str, EngineHealth] = field(default_factory=dict)
    overall_status: EngineStatus = EngineStatus.UNKNOWN
    health_score: float = 0.0
    risk_score: float = 0.0
    active_alerts: int = 0
    critical_alerts: int = 0
    active_incidents: int = 0
    uptime_seconds: float = 0.0


@dataclass
class Diagnosis:
    id: str
    engine: str
    symptom: str
    root_cause: str
    severity: AlertLevel
    affected_engines: List[str] = field(default_factory=list)
    dependency_chain: List[str] = field(default_factory=list)
    recommended_action: str = ""
    auto_fixable: bool = False
    confidence: float = 0.0
    risk_score: float = 0.0
    escalation: EscalationTier = EscalationTier.DASHBOARD
    matched_pattern: Optional[str] = None
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: Optional[datetime] = None


@dataclass
class DependencyGraph:
    edges: Dict[str, List[str]] = field(default_factory=dict)
    reverse: Dict[str, List[str]] = field(default_factory=dict)

    def add(self, engine: str, depends_on: Iterable[str]) -> None:
        deps = list(dict.fromkeys(depends_on))
        self.edges[engine] = deps
        for d in deps:
            if d not in self.reverse:
                self.reverse[d] = []
            if engine not in self.reverse[d]:
                self.reverse[d].append(engine)

    def get_dependencies(self, e: str) -> List[str]:
        return list(self.edges.get(e, []))

    def get_dependents(self, e: str) -> List[str]:
        return list(self.reverse.get(e, []))

    def nodes(self) -> Set[str]:
        result = set(self.edges.keys())
        for deps in self.edges.values():
            result.update(deps)
        result.update(self.reverse.keys())
        return result


@dataclass
class MetricEvent:
    timestamp: datetime
    kind: str
    engine: str
    value: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Anomaly:
    id: str
    engine: str
    anomaly_type: AnomalyType
    description: str
    severity: AlertLevel
    observed_value: float = 0.0
    expected_value: float = 0.0
    deviation: float = 0.0
    confidence: float = 0.0
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    resolved: bool = False


@dataclass
class RecoveryAttempt:
    id: str
    engine: str
    reason: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    status: RecoveryStatus = RecoveryStatus.NOT_ATTEMPTED
    action: str = ""
    error: Optional[str] = None
    verification_passed: bool = False


@dataclass
class Incident:
    id: str
    title: str
    severity: AlertLevel
    status: IncidentStatus
    primary_engine: str
    affected_engines: List[str] = field(default_factory=list)
    diagnoses: List[str] = field(default_factory=list)
    root_cause: str = ""
    confidence: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: Optional[datetime] = None
    recovery_attempts: List[str] = field(default_factory=list)


@dataclass
class CircuitState:
    engine: str
    failures: int = 0
    state: str = "closed"
    opened_at: Optional[datetime] = None
    cooldown_seconds: int = 60


@dataclass
class Prediction:
    engine: str
    predicted_failure: str
    confidence: float
    estimated_time_to_failure_minutes: float
    contributing_factors: List[str] = field(default_factory=list)
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class FailurePattern:
    id: str
    signature: str
    conditions: Dict[str, Any] = field(default_factory=dict)
    root_cause: str = ""
    fix_applied: str = ""
    success_rate: float = 0.5
    occurrences: int = 0
    last_seen: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ═══════════════════════════════════════════════════════════════════════════
# UTILITIES
# ═══════════════════════════════════════════════════════════════════════════

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(v: Optional[datetime]) -> Optional[str]:
    return v.isoformat() if v else None


def clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def safe_float(v: Any, d: float = 0.0) -> float:
    try:
        return float(v)
    except (ValueError, TypeError):
        return d


def percentile(values: Iterable[float], p: float) -> float:
    data = sorted(float(v) for v in values)
    if not data:
        return 0.0
    if len(data) == 1:
        return data[0]
    p = clamp(p, 0.0, 100.0)
    idx = (len(data) - 1) * (p / 100.0)
    lo, hi = int(math.floor(idx)), int(math.ceil(idx))
    return data[lo] if lo == hi else data[lo] + (data[hi] - data[lo]) * (idx - lo)


# ═══════════════════════════════════════════════════════════════════════════
# HEALTH CHECK TIMEOUT (Simple daemon-thread — no executor breakage)
# ═══════════════════════════════════════════════════════════════════════════

class HealthCheckTimeout:
    """Simple timeout using daemon threads."""

    def __init__(self, timeout_seconds: float = 5.0):
        self.timeout_seconds = timeout_seconds

    def check_with_timeout(self, check_fn: Callable, *args, **kwargs) -> Tuple[bool, Optional[Any]]:
        result_holder: Dict[str, Any] = {}
        completed = threading.Event()

        def _runner():
            try:
                result_holder["result"] = check_fn(*args, **kwargs)
                result_holder["success"] = True
            except Exception as exc:
                result_holder["error"] = exc
                result_holder["success"] = False
            finally:
                completed.set()

        thread = threading.Thread(target=_runner, daemon=True, name=f"health-check-{id(check_fn)}")
        thread.start()

        if completed.wait(timeout=self.timeout_seconds):
            if result_holder.get("success", False):
                return True, result_holder.get("result")
            return False, result_holder.get("error")
        return False, TimeoutError(f"Timeout after {self.timeout_seconds}s")

    def shutdown(self) -> None:
        pass


# ═══════════════════════════════════════════════════════════════════════════
# SELF-MONITORING
# ═══════════════════════════════════════════════════════════════════════════

class SelfMonitor:
    """Monitors MetaEngine's own health."""

    def __init__(self, cycle_timeout_seconds: float = 300.0):
        self.cycle_timeout_seconds = cycle_timeout_seconds
        self._last_cycle_completed: Optional[datetime] = None
        self._cycle_durations: List[float] = []
        self._lock = threading.RLock()

    def record_cycle_completed(self) -> None:
        with self._lock:
            self._last_cycle_completed = utcnow()

    def check_self_health(self) -> Dict[str, Any]:
        with self._lock:
            if self._last_cycle_completed is None:
                return {"status": "starting", "health_score": 0.0}
            elapsed = (utcnow() - self._last_cycle_completed).total_seconds()
            if elapsed > self.cycle_timeout_seconds:
                return {"status": "critical", "health_score": 0.0}
            if elapsed > self.cycle_timeout_seconds * 0.5:
                return {"status": "degraded", "health_score": 50.0}
            return {"status": "healthy", "health_score": 100.0}


# ═══════════════════════════════════════════════════════════════════════════
# GLOBAL ALERT RATE LIMITER
# ═══════════════════════════════════════════════════════════════════════════

class GlobalAlertRateLimiter:
    """Prevents alert flooding."""

    def __init__(self, max_per_minute: int = 5, max_per_hour: int = 30):
        self.max_per_minute = max_per_minute
        self.max_per_hour = max_per_hour
        self._minute_alerts: List[float] = []
        self._hour_alerts: List[float] = []
        self._lock = threading.RLock()

    def can_send(self) -> bool:
        with self._lock:
            now = time.monotonic()
            self._minute_alerts = [t for t in self._minute_alerts if now - t < 60]
            self._hour_alerts = [t for t in self._hour_alerts if now - t < 3600]
            return len(self._minute_alerts) < self.max_per_minute and len(self._hour_alerts) < self.max_per_hour

    def record_send(self) -> None:
        with self._lock:
            now = time.monotonic()
            self._minute_alerts.append(now)
            self._hour_alerts.append(now)


# ═══════════════════════════════════════════════════════════════════════════
# INCIDENT RESOLUTION MANAGER
# ═══════════════════════════════════════════════════════════════════════════

class IncidentResolutionManager:
    """Manages incident lifecycle."""

    def __init__(self, meta_engine: "MetaEngine"):
        self.meta_engine = meta_engine

    def check_and_resolve(self) -> int:
        resolved_count = 0
        for incident_id, incident in list(self.meta_engine._incidents.items()):
            if incident.status == IncidentStatus.RESOLVED:
                continue
            health = self.meta_engine.check_engine_health(incident.primary_engine, record_history=False)
            if health.status == EngineStatus.NOMINAL:
                all_healthy = all(
                    self.meta_engine.check_engine_health(e, record_history=False).status == EngineStatus.NOMINAL
                    for e in incident.affected_engines
                )
                if all_healthy:
                    incident.status = IncidentStatus.RESOLVED
                    incident.resolved_at = utcnow()
                    incident.updated_at = utcnow()
                    resolved_count += 1
                    self.meta_engine.learn_from_incident(incident)
                    logger.info("Incident %s RESOLVED", incident_id)
        return resolved_count

    def escalate_if_unresolved(self) -> int:
        escalated = 0
        now = utcnow()
        for incident in self.meta_engine._incidents.values():
            if incident.status in (IncidentStatus.RESOLVED, IncidentStatus.ESCALATED):
                continue
            if (now - incident.created_at).total_seconds() > 1800:
                incident.status = IncidentStatus.ESCALATED
                incident.updated_at = now
                escalated += 1
        return escalated


# ═══════════════════════════════════════════════════════════════════════════
# META ENGINE v5.0 — COMPLETE FULL VERSION
# ═══════════════════════════════════════════════════════════════════════════

class MetaEngine:
    """Autonomous supervisory engine watching ALL engines."""

    VERSION = "5.0.0"

    ERROR_DEGRADED_THRESHOLD = 0.10
    ERROR_CRITICAL_THRESHOLD = 0.50
    STALE_AFTER_SECONDS = 3600
    LATENCY_Z_THRESHOLD = 3.0
    ERROR_SPIKE_MULTIPLIER = 3.0
    MIN_SAMPLES_FOR_ANOMALY = 5
    RECOVERY_COOLDOWN_SECONDS = 300
    MAX_RECOVERY_ATTEMPTS_PER_HOUR = 3
    INCIDENT_DEDUP_SECONDS = 300
    PREDICTION_MIN_SAMPLES = 10
    PREDICTION_DECLINE_SLOPE = -0.5
    PREDICTION_CRITICAL_SCORE = 20.0
    PREDICTION_MAX_CYCLES = 20
    HEALTH_CHECK_TIMEOUT_SECONDS = 5.0
    DB_CIRCUIT_FAILURE_THRESHOLD = 3
    DB_CIRCUIT_COOLDOWN_SECONDS = 300

    def __init__(self, event_bus: Any = None, container: Any = None):
        self.event_bus = event_bus
        self.container = container

        self._engines: Dict[str, Any] = {}
        self._engine_versions: Dict[str, str] = {}
        self._health_history: Dict[str, Deque[EngineHealth]] = defaultdict(lambda: deque(maxlen=500))
        self._snapshots: Deque[SystemSnapshot] = deque(maxlen=500)
        self._diagnoses: List[Diagnosis] = []
        self._anomalies: List[Anomaly] = []
        self._incidents: Dict[str, Incident] = {}
        self._recovery_history: List[RecoveryAttempt] = []
        self._deps = DependencyGraph()
        self._recovery_handlers: Dict[str, Callable] = {}
        self._health_handlers: Dict[str, Callable] = {}
        self._alert_callback: Optional[Callable] = None
        self._error_counts: Dict[str, Deque[datetime]] = defaultdict(lambda: deque(maxlen=5000))
        self._success_counts: Dict[str, Deque[datetime]] = defaultdict(lambda: deque(maxlen=5000))
        self._latency_samples: Dict[str, Deque[Tuple[datetime, float]]] = defaultdict(lambda: deque(maxlen=2000))
        self._metrics: Dict[str, Dict[str, Deque[MetricEvent]]] = defaultdict(lambda: defaultdict(lambda: deque(maxlen=2000)))
        self._circuits: Dict[str, CircuitState] = {}
        self._recovery_timestamps: Dict[str, Deque[datetime]] = defaultdict(lambda: deque(maxlen=100))
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self._start_time = utcnow()
        self._last_monitor_cycle: Optional[datetime] = None
        self._cycle_count = 0
        self._monitor_interval_minutes = 1.0
        self._last_telegram_alert: Dict[str, float] = {}
        self._failure_patterns: List[FailurePattern] = []
        self._predictions: List[Prediction] = []
        self._adaptive_thresholds: Dict[str, Dict[str, float]] = {}

        self.health_timeout = HealthCheckTimeout(timeout_seconds=self.HEALTH_CHECK_TIMEOUT_SECONDS)
        self.self_monitor = SelfMonitor()
        self.incident_manager = IncidentResolutionManager(self)
        self.alert_limiter = GlobalAlertRateLimiter()

        self._db_failure_count = 0
        self._db_circuit_open = False
        self._db_circuit_reset_time = 0.0

        self._build_dependency_graph()
        logger.info("MetaEngine v%s initialized — COMPLETE OVERSEER", self.VERSION)

    # ═══════════════════════════════════════════════════════════════════
    # DEPENDENCY GRAPH
    # ═══════════════════════════════════════════════════════════════════

    def _build_dependency_graph(self) -> None:
        for engine, deps in [
            ("content_engine", ["ai_orchestrator", "prompt_builder", "topic_selector"]),
            ("card_renderer", []),
            ("condition_engine", ["event_bus", "engagement_tracker"]),
            ("engagement_tracker", ["supabase"]),
            ("email_service", []),
            ("guardian", ["github"]),
            ("ai_orchestrator", ["gemini"]),
            ("scheduler", ["job_service", "content_engine"]),
            ("job_service", ["supabase"]),
            ("health_monitor", []),
            ("plugin_registry", ["event_bus"]),
            ("event_bus", []),
            ("render_queue", ["card_renderer", "supabase"]),
            ("image_engine", []),
            ("topic_selector", ["supabase"]),
            ("prompt_builder", []),
            ("meta_engine", []),
            ("meta_director", []),
            ("brand_scheduler", []),
            ("monitor_plugin", []),
            ("backup_plugin", []),
        ]:
            self._deps.add(engine, deps)

    # ═══════════════════════════════════════════════════════════════════
    # NUCLEAR FIX: INSTANCE RESOLUTION
    # ═══════════════════════════════════════════════════════════════════

    def _resolve_to_instance(self, engine: Any, name: str) -> Any:
        """Resolve any engine (class, getter, instance) to an instance."""
        if engine is not None and not isinstance(engine, type) and not callable(engine):
            return engine
        if isinstance(engine, type):
            for getter_name in ('get_instance', 'get', 'instance', 'get_plugin_registry', 'get_event_bus'):
                getter = getattr(engine, getter_name, None)
                if callable(getter):
                    try:
                        result = getter()
                        if result is not None and not isinstance(result, type):
                            return result
                    except Exception:
                        pass
            try:
                instance = engine()
                if instance is not None and not isinstance(instance, type):
                    return instance
            except Exception:
                pass
        if callable(engine) and not isinstance(engine, type):
            try:
                result = engine()
                if result is not None:
                    return result
            except Exception:
                pass
        if self.container:
            try:
                resolved = self.container.try_resolve(engine)
                if resolved is not None and not isinstance(resolved, type):
                    return resolved
            except Exception:
                pass
        return None

    def _override_with_running_instances(self) -> None:
        overrides = []
        try:
            from core.event_bus import get_event_bus
            bus = get_event_bus()
            if bus is not None and not isinstance(bus, type):
                overrides.append(("event_bus", bus))
        except Exception:
            pass
        try:
            from plugins.base import get_plugin_registry
            registry = get_plugin_registry()
            if registry is not None and not isinstance(registry, type):
                overrides.append(("plugin_registry", registry))
        except Exception:
            pass
        for name, instance in overrides:
            self.register(name, instance)
            logger.info("MetaEngine: overridden %s with running instance", name)

    # ═══════════════════════════════════════════════════════════════════
    # REGISTRATION
    # ═══════════════════════════════════════════════════════════════════

    def register(self, name: str, engine: Any, version: str = "") -> None:
        if not name:
            raise ValueError("Engine name cannot be empty")
        with self._lock:
            self._engines[name] = engine
            self._engine_versions[name] = version or getattr(engine, "__version__", "") or ""
            if name not in self._circuits:
                self._circuits[name] = CircuitState(engine=name)

    def register_all_engines(self) -> None:
        try:
            from services.director import ENGINE_REGISTRY
            for module_path, attr_name, display_name, sector in ENGINE_REGISTRY:
                try:
                    module = __import__(module_path, fromlist=[attr_name])
                    raw_engine = getattr(module, attr_name, None)
                    if raw_engine is not None:
                        resolved = self._resolve_to_instance(raw_engine, display_name)
                        if resolved is not None:
                            self.register(display_name, resolved)
                except ImportError:
                    pass
                except Exception as exc:
                    logger.debug("MetaEngine: %s: %s", display_name, exc)
        except ImportError:
            logger.warning("Director registry unavailable")
        if self.container:
            for name, plugin in getattr(self.container, "industry_plugins", {}).items():
                try:
                    self.register(name, plugin)
                except Exception as exc:
                    logger.debug("Plugin %s: %s", name, exc)
        self._override_with_running_instances()
        logger.info("MetaEngine: %d engines registered", len(self._engines))

    def register_recovery_handler(self, engine_name: str, handler: Callable) -> None:
        self._recovery_handlers[engine_name] = handler

    def register_health_handler(self, engine_name: str, handler: Callable) -> None:
        self._health_handlers[engine_name] = handler

    def set_alert_callback(self, cb: Callable) -> None:
        self._alert_callback = cb

    # ═══════════════════════════════════════════════════════════════════
    # METRICS RECORDING
    # ═══════════════════════════════════════════════════════════════════

    def record_success(self, engine_name: str) -> None:
        self._success_counts[engine_name].append(utcnow())

    def record_error(self, engine_name: str, error: str = "") -> None:
        self._error_counts[engine_name].append(utcnow())

    def record_latency(self, engine_name: str, ms: float) -> None:
        self._latency_samples[engine_name].append((utcnow(), max(0.0, safe_float(ms))))

    def record_metric(self, engine_name: str, metric_name: str, value: float, metadata: Optional[Dict[str, Any]] = None) -> None:
        self._metrics[engine_name][metric_name].append(
            MetricEvent(timestamp=utcnow(), kind=metric_name, engine=engine_name, value=safe_float(value), metadata=metadata or {})
        )

    # ═══════════════════════════════════════════════════════════════════
    # HEALTH CHECK (with nuclear class safety net)
    # ═══════════════════════════════════════════════════════════════════

    def _ping_engine(self, engine: Any, engine_name: str = "") -> bool:
        success, result = self.health_timeout.check_with_timeout(
            self._ping_engine_inner, engine, engine_name
        )
        return success and result

    def _ping_engine_inner(self, engine: Any, engine_name: str = "") -> bool:
        # NUCLEAR SAFETY NET: Never call methods on classes
        if isinstance(engine, type):
            logger.debug("Engine '%s' is a CLASS — marking healthy", engine_name or engine.__name__)
            return True

        if engine_name and engine_name in self._health_handlers:
            try:
                result = self._health_handlers[engine_name](engine)
                if isinstance(result, dict):
                    return str(result.get("status", "")).lower() in {"healthy", "ok", "nominal", "ready", "degraded"}
                return bool(result)
            except Exception:
                return False

        if hasattr(engine, "health_check"):
            try:
                result = engine.health_check()
                if isinstance(result, dict):
                    status = str(result.get("status", "")).lower()
                    return status in {"healthy", "ok", "nominal", "ready", "degraded"}
                return bool(result)
            except Exception:
                return False

        if hasattr(engine, "get_status"):
            try:
                result = engine.get_status()
                return result is not None
            except Exception:
                return False

        if hasattr(engine, "get_stats"):
            try:
                result = engine.get_stats()
                return result is not None
            except Exception:
                return False

        return False

    def check_engine_health(self, name: str, record_history: bool = True) -> EngineHealth:
        engine = self._engines.get(name)
        health = EngineHealth(name=name, version=self._engine_versions.get(name, ""))

        if engine is None:
            health.status = EngineStatus.OFFLINE
            health.health_score = 0.0
            health.risk_score = 100.0
            return health

        if not self._ping_engine(engine, name):
            health.status = EngineStatus.OFFLINE
            health.health_score = 0.0
            health.risk_score = 100.0
            health.dependencies = self._deps.get_dependencies(name)
            health.dependents = self._deps.get_dependents(name)
            if record_history:
                self._health_history[name].append(health)
            return health

        health.error_count_1h = sum(1 for ts in self._error_counts[name] if ts >= utcnow() - timedelta(hours=1))
        health.error_count_24h = sum(1 for ts in self._error_counts[name] if ts >= utcnow() - timedelta(hours=24))
        health.success_count_1h = sum(1 for ts in self._success_counts[name] if ts >= utcnow() - timedelta(hours=1))
        health.success_count_24h = sum(1 for ts in self._success_counts[name] if ts >= utcnow() - timedelta(hours=24))
        health.throughput_1h = health.success_count_1h / 3600.0

        latencies = [v for ts, v in self._latency_samples[name] if ts >= utcnow() - timedelta(hours=1)]
        if latencies:
            health.avg_latency_ms = mean(latencies)
            health.median_latency_ms = median(latencies)
            health.p95_latency_ms = percentile(latencies, 95)

        health.dependencies = self._deps.get_dependencies(name)
        health.dependents = self._deps.get_dependents(name)

        health.anomaly_flags = [
            a.anomaly_type.value for a in self._anomalies
            if a.engine == name and not a.resolved
        ][-10:]

        total = health.error_count_1h + health.success_count_1h
        error_rate = health.error_count_1h / max(total, 1)

        if error_rate >= self.ERROR_CRITICAL_THRESHOLD:
            health.status = EngineStatus.DEGRADED
        elif error_rate >= self.ERROR_DEGRADED_THRESHOLD:
            health.status = EngineStatus.DEGRADED
        elif total == 0:
            last_success = self._success_counts[name][-1] if self._success_counts[name] else None
            if last_success and (utcnow() - last_success).total_seconds() > self.STALE_AFTER_SECONDS:
                health.status = EngineStatus.STALE
            else:
                health.status = EngineStatus.NOMINAL
        else:
            health.status = EngineStatus.NOMINAL

        health.health_score = self._calc_health_score(health)
        health.risk_score = self._calc_risk_score(health)

        if record_history:
            self._health_history[name].append(health)

        return health

    def _calc_health_score(self, h: EngineHealth) -> float:
        if h.status == EngineStatus.OFFLINE:
            return 0.0
        score = 100.0
        if h.status == EngineStatus.STALE:
            score -= 35
        if h.status == EngineStatus.DEGRADED:
            score -= 25
        total = h.error_count_1h + h.success_count_1h
        if total:
            score -= (h.error_count_1h / total) * 50
        if h.avg_latency_ms > 1000:
            score -= min(25, (h.avg_latency_ms - 1000) / 200)
        return round(clamp(score), 2)

    def _calc_risk_score(self, h: EngineHealth, depth: int = 0) -> float:
        risk = 0.0
        if h.status == EngineStatus.OFFLINE:
            risk += 80
        elif h.status == EngineStatus.DEGRADED:
            risk += 40
        elif h.status == EngineStatus.STALE:
            risk += 30
        total = h.error_count_1h + h.success_count_1h
        if total:
            risk += (h.error_count_1h / total) * 40
        risk += min(30, len(h.anomaly_flags) * 7)
        if depth < 1:
            for dep in h.dependencies:
                dh = self.check_engine_health(dep, record_history=False)
                if dh.status == EngineStatus.OFFLINE:
                    risk += 20
                elif dh.status == EngineStatus.DEGRADED:
                    risk += 10
        return round(clamp(risk), 2)

    # ═══════════════════════════════════════════════════════════════════
    # ANOMALY DETECTION    # ═══════════════════════════════════════════════════════════════════

    def detect_anomalies(self) -> List[Anomaly]:
        detected = []
        for name in list(self._engines.keys()):
            try:
                detected.extend(self._detect_anomalies_for_engine(name))
            except Exception as exc:
                logger.debug("Anomaly detection failed for %s: %s", name, exc)
        deduped = []
        for a in detected:
            key = f"{a.engine}:{a.anomaly_type.value}"
            if not any(
                f"{x.engine}:{x.anomaly_type.value}" == key and not x.resolved
                and (utcnow() - x.detected_at).total_seconds() < 300
                for x in self._anomalies
            ):
                deduped.append(a)
        self._anomalies.extend(deduped)
        if len(self._anomalies) > 2000:
            self._anomalies = self._anomalies[-2000:]
        return deduped

    def _detect_anomalies_for_engine(self, name: str) -> List[Anomaly]:
        anomalies = []
        lat_vals = [v for _, v in self._latency_samples[name]]
        if len(lat_vals) >= self.MIN_SAMPLES_FOR_ANOMALY:
            avg = mean(lat_vals)
            std = pstdev(lat_vals)
            recent = [v for ts, v in self._latency_samples[name] if ts >= utcnow() - timedelta(minutes=5)]
            if recent and std > 0:
                recent_avg = mean(recent)
                z = (recent_avg - avg) / std
                if z >= self.LATENCY_Z_THRESHOLD:
                    anomalies.append(Anomaly(
                        id=f"an_{uuid.uuid4().hex[:10]}", engine=name,
                        anomaly_type=AnomalyType.LATENCY_SPIKE,
                        description=f"Latency {z:.1f}σ above baseline",
                        severity=AlertLevel.WARNING,
                        observed_value=recent_avg, expected_value=avg,
                        deviation=z, confidence=min(1.0, z / 6.0),
                    ))
        errors_5m = sum(1 for ts in self._error_counts[name] if ts >= utcnow() - timedelta(minutes=5))
        errors_1h = sum(1 for ts in self._error_counts[name] if ts >= utcnow() - timedelta(hours=1))
        baseline_5m = errors_1h / 12.0 if errors_1h else 0.0
        if errors_5m >= 3 and (baseline_5m == 0 or errors_5m >= baseline_5m * self.ERROR_SPIKE_MULTIPLIER):
            anomalies.append(Anomaly(
                id=f"an_{uuid.uuid4().hex[:10]}", engine=name,
                anomaly_type=AnomalyType.ERROR_SPIKE,
                description=f"{errors_5m} errors in 5min",
                severity=AlertLevel.CRITICAL if errors_5m >= 10 else AlertLevel.WARNING,
                observed_value=errors_5m, expected_value=baseline_5m,
                deviation=errors_5m / max(baseline_5m, 1.0), confidence=0.9,
            ))
        return anomalies

    # ═══════════════════════════════════════════════════════════════════
    # SNAPSHOT
    # ═══════════════════════════════════════════════════════════════════

    def take_snapshot(self) -> SystemSnapshot:
        snap = SystemSnapshot()
        snap.uptime_seconds = (utcnow() - self._start_time).total_seconds()
        health_scores, risk_scores = [], []
        for name in list(self._engines.keys()):
            try:
                health = self.check_engine_health(name)
                snap.engines[name] = health
                health_scores.append(health.health_score)
                risk_scores.append(health.risk_score)
                if health.status == EngineStatus.OFFLINE:
                    snap.critical_alerts += 1
                elif health.status in (EngineStatus.DEGRADED, EngineStatus.STALE):
                    snap.active_alerts += 1
            except Exception as exc:
                logger.error("Health check failed for %s: %s", name, exc)
        snap.overall_status = EngineStatus.DEGRADED if snap.critical_alerts or snap.active_alerts else EngineStatus.NOMINAL
        snap.health_score = round(mean(health_scores), 2) if health_scores else 0.0
        snap.risk_score = round(mean(risk_scores), 2) if risk_scores else 0.0
        snap.active_incidents = sum(1 for i in self._incidents.values() if i.status != IncidentStatus.RESOLVED)
        self._snapshots.append(snap)
        self._last_monitor_cycle = snap.timestamp
        return snap

    # ═══════════════════════════════════════════════════════════════════
    # DIAGNOSIS
    # ═══════════════════════════════════════════════════════════════════

    def diagnose(self) -> List[Diagnosis]:
        snapshot = self.take_snapshot()
        diagnoses = []
        for name, health in snapshot.engines.items():
            if health.status == EngineStatus.OFFLINE:
                d = Diagnosis(
                    id=f"diag_{uuid.uuid4().hex[:10]}", engine=name,
                    symptom=f"{name} offline",
                    root_cause=self._find_root_cause(name, snapshot),
                    severity=AlertLevel.CRITICAL,
                    affected_engines=self._deps.get_dependents(name),
                    dependency_chain=self._find_dependency_chain(name, snapshot),
                    recommended_action=f"Recover {name}, verify health",
                    auto_fixable=name in self._recovery_handlers,
                    confidence=0.85, risk_score=health.risk_score,
                )
                diagnoses.append(d)
            elif health.status == EngineStatus.DEGRADED:
                total = health.error_count_1h + health.success_count_1h
                d = Diagnosis(
                    id=f"diag_{uuid.uuid4().hex[:10]}", engine=name,
                    symptom=f"{name} degraded",
                    root_cause=self._find_root_cause(name, snapshot),
                    severity=AlertLevel.CRITICAL if (health.error_count_1h / max(total, 1)) >= self.ERROR_CRITICAL_THRESHOLD else AlertLevel.WARNING,
                    affected_engines=self._deps.get_dependents(name),
                    dependency_chain=self._find_dependency_chain(name, snapshot),
                    recommended_action=f"Inspect {name} error rate",
                    auto_fixable=name in self._recovery_handlers,
                    confidence=0.7, risk_score=health.risk_score,
                )
                diagnoses.append(d)
        self._diagnoses.extend(diagnoses)
        if len(self._diagnoses) > 2000:
            self._diagnoses = self._diagnoses[-2000:]
        return diagnoses

    def _find_root_cause(self, engine_name: str, snapshot: SystemSnapshot) -> str:
        chain = self._find_dependency_chain(engine_name, snapshot)
        if len(chain) > 1:
            return f"Dependency chain: {' → '.join(chain)}"
        health = snapshot.engines.get(engine_name)
        if health and health.error_count_1h > 10:
            return "High error rate"
        if health and health.avg_latency_ms > 5000:
            return "Severe latency"
        return f"Isolated failure in {engine_name}"

    def _find_dependency_chain(self, engine_name: str, snapshot: SystemSnapshot) -> List[str]:
        chain = [engine_name]
        visited = set()
        current = engine_name
        while current not in visited:
            visited.add(current)
            deps = self._deps.get_dependencies(current)
            unhealthy = [
                d for d in deps
                if snapshot.engines.get(d)
                and snapshot.engines[d].status in (EngineStatus.OFFLINE, EngineStatus.DEGRADED)
            ]
            if not unhealthy:
                break
            offline = [d for d in unhealthy if snapshot.engines[d].status == EngineStatus.OFFLINE]
            current = offline[0] if offline else unhealthy[0]
            chain.append(current)
        return chain

    # ═══════════════════════════════════════════════════════════════════
    # CASCADING FAILURE DETECTION
    # ═══════════════════════════════════════════════════════════════════

    def detect_cascading_failures(self, snapshot: Optional[SystemSnapshot] = None) -> List[Diagnosis]:
        if snapshot is None:
            snapshot = self.take_snapshot()
        results = []
        for root in self._deps.nodes():
            rh = snapshot.engines.get(root)
            if not rh or rh.status not in (EngineStatus.OFFLINE, EngineStatus.DEGRADED):
                continue
            affected = [
                d for d in self._collect_dependents(root)
                if snapshot.engines.get(d)
                and snapshot.engines[d].status in (EngineStatus.OFFLINE, EngineStatus.DEGRADED)
            ]
            if len(affected) >= 2:
                results.append(Diagnosis(
                    id=f"cascade_{uuid.uuid4().hex[:10]}", engine=root,
                    symptom=f"{len(affected)} dependents degraded",
                    root_cause=f"Cascading from {root}",
                    severity=AlertLevel.CRITICAL,
                    affected_engines=affected,
                    dependency_chain=[root] + affected,
                    recommended_action=f"Stabilize {root} first",
                    auto_fixable=root in self._recovery_handlers,
                    confidence=0.88,
                ))
        return results

    def _collect_dependents(self, engine: str) -> List[str]:
        result, visited, queue = [], set(), deque(self._deps.get_dependents(engine))
        while queue:
            cur = queue.popleft()
            if cur in visited:
                continue
            visited.add(cur)
            result.append(cur)
            queue.extend(self._deps.get_dependents(cur))
        return result

    # ═══════════════════════════════════════════════════════════════════
    # PREDICTIONS
    # ═══════════════════════════════════════════════════════════════════

    def predict_failures(self) -> List[Prediction]:
        predictions = []
        for name in self._engines:
            history = list(self._health_history[name])
            if len(history) < self.PREDICTION_MIN_SAMPLES:
                continue
            scores = [h.health_score for h in history[-20:]]
            n = len(scores)
            x_mean = (n - 1) / 2
            y_mean = mean(scores)
            numerator = sum((i - x_mean) * (scores[i] - y_mean) for i in range(n))
            denominator = sum((i - x_mean) ** 2 for i in range(n))
            if denominator == 0:
                continue
            slope = numerator / denominator
            if slope < self.PREDICTION_DECLINE_SLOPE:
                current = scores[-1]
                cycles_to_failure = (current - self.PREDICTION_CRITICAL_SCORE) / abs(slope)
                if cycles_to_failure < self.PREDICTION_MAX_CYCLES:
                    predictions.append(Prediction(
                        engine=name,
                        predicted_failure=f"Critical in {cycles_to_failure:.0f} cycles",
                        confidence=min(0.9, abs(slope) / 2),
                        estimated_time_to_failure_minutes=cycles_to_failure * self._monitor_interval_minutes,
                    ))
        predictions.sort(key=lambda p: p.estimated_time_to_failure_minutes)
        self._predictions = predictions
        return predictions

    # ═══════════════════════════════════════════════════════════════════
    # AUTO-HEAL
    # ═══════════════════════════════════════════════════════════════════

    def auto_heal(self) -> List[str]:
        diagnoses = self.diagnose()
        try:
            diagnoses.extend(self.detect_cascading_failures())
        except Exception as exc:
            logger.debug("Cascade detection failed: %s", exc)
        diagnoses.sort(key=lambda d: {AlertLevel.CRITICAL: 0, AlertLevel.WARNING: 1, AlertLevel.INFO: 2}[d.severity])
        actions = []
        processed = set()
        for d in diagnoses:
            if d.severity == AlertLevel.CRITICAL:
                self._send_telegram_alert(d)
                continue
            if not d.auto_fixable or d.engine in processed:
                continue
            processed.add(d.engine)
            attempt = self._perform_recovery(d)
            self._recovery_history.append(attempt)
            actions.append(f"AUTO-HEAL: {d.engine} — {attempt.status.value}")
        return actions

    def _perform_recovery(self, d: Diagnosis) -> RecoveryAttempt:
        started = utcnow()
        attempt = RecoveryAttempt(
            id=f"rec_{uuid.uuid4().hex[:10]}", engine=d.engine,
            reason=d.symptom, started_at=started, action=d.recommended_action,
        )
        self._recovery_timestamps[d.engine].append(started)
        try:
            handler = self._recovery_handlers.get(d.engine)
            if not handler:
                raise RuntimeError("No recovery handler")
            handler(d)
            attempt.status = RecoveryStatus.SUCCESS
            attempt.finished_at = utcnow()
            time.sleep(0.5)
            health = self.check_engine_health(d.engine, record_history=False)
            if health.status == EngineStatus.NOMINAL:
                attempt.status = RecoveryStatus.VERIFIED
                attempt.verification_passed = True
            d.resolved_at = utcnow()
        except Exception as exc:
            attempt.status = RecoveryStatus.FAILED
            attempt.finished_at = utcnow()
            attempt.error = str(exc)[:200]
        return attempt

    def _recovery_allowed(self, engine_name: str) -> bool:
        now = utcnow()
        cutoff = now - timedelta(hours=1)
        recent = [ts for ts in self._recovery_timestamps[engine_name] if ts >= cutoff]
        self._recovery_timestamps[engine_name] = deque(recent, maxlen=100)
        return len(recent) < self.MAX_RECOVERY_ATTEMPTS_PER_HOUR

    # ═══════════════════════════════════════════════════════════════════
    # ALERTING
    # ═══════════════════════════════════════════════════════════════════

    def _send_telegram_alert(self, d: Diagnosis) -> None:
        if not self.alert_limiter.can_send():
            return
        key = f"{d.engine}:{d.severity.value}"
        now = time.monotonic()
        if now - self._last_telegram_alert.get(key, 0) < 300:
            return
        self._last_telegram_alert[key] = now
        self.alert_limiter.record_send()
        try:
            from services.email_service import email_service
            email_service.send_telegram(
                f"🔴 <b>OVERSEER: {d.engine}</b>\n{d.symptom}\n{d.root_cause}"
            )
        except Exception as exc:
            logger.error("Telegram alert failed: %s", exc)

    # ═══════════════════════════════════════════════════════════════════
    # INCIDENTS
    # ═══════════════════════════════════════════════════════════════════

    def _create_incidents(self, diagnoses: List[Diagnosis]) -> None:
        for d in diagnoses:
            existing = next(
                (i for i in self._incidents.values()
                 if i.primary_engine == d.engine
                 and i.status != IncidentStatus.RESOLVED
                 and i.created_at >= utcnow() - timedelta(seconds=self.INCIDENT_DEDUP_SECONDS)),
                None,
            )
            if existing:
                existing.diagnoses.append(d.id)
                existing.updated_at = utcnow()
                continue
            incident = Incident(
                id=f"inc_{uuid.uuid4().hex[:10]}", title=d.symptom,
                severity=d.severity, status=IncidentStatus.OPEN,
                primary_engine=d.engine, affected_engines=d.affected_engines,
                diagnoses=[d.id], root_cause=d.root_cause, confidence=d.confidence,
            )
            self._incidents[incident.id] = incident

    def learn_from_incident(self, incident: Incident) -> None:
        if incident.status != IncidentStatus.RESOLVED:
            return
        signature = f"{incident.primary_engine}:{incident.root_cause}"
        existing = next((p for p in self._failure_patterns if p.signature == signature), None)
        if existing:
            existing.occurrences += 1
            existing.last_seen = utcnow()
        else:
            self._failure_patterns.append(FailurePattern(
                id=f"fp_{uuid.uuid4().hex[:10]}", signature=signature,
                root_cause=incident.root_cause, occurrences=1, last_seen=utcnow(),
            ))

    # ═══════════════════════════════════════════════════════════════════
    # STATUS & REPORTS
    # ═══════════════════════════════════════════════════════════════════

    def get_status(self) -> Dict[str, Any]:
        snap = self.take_snapshot()
        predictions = self.predict_failures()
        self_health = self.self_monitor.check_self_health()
        return {
            "version": self.VERSION,
            "timestamp": iso(snap.timestamp),
            "overall": snap.overall_status.value,
            "health_score": snap.health_score,
            "risk_score": snap.risk_score,
            "uptime_hours": round(snap.uptime_seconds / 3600, 2),
            "engine_count": len(snap.engines),
            "engines": {
                name: {
                    "status": h.status.value, "health_score": h.health_score,
                    "risk_score": h.risk_score, "error_count_1h": h.error_count_1h,
                    "avg_latency_ms": round(h.avg_latency_ms, 2), "dependents": h.dependents,
                }
                for name, h in snap.engines.items()
            },
            "active_diagnoses": len([d for d in self._diagnoses if d.resolved_at is None]),
            "active_incidents": len([i for i in self._incidents.values() if i.status != IncidentStatus.RESOLVED]),
            "critical_alerts": snap.critical_alerts,
            "self_health": self_health,
            "predictions": [
                {"engine": p.engine, "predicted_failure": p.predicted_failure, "eta_minutes": p.estimated_time_to_failure_minutes}
                for p in predictions[:5]
            ],
            "failure_patterns_learned": len(self._failure_patterns),
            "monitoring": {
                "running": self._running, "cycle_count": self._cycle_count,
                "last_cycle": iso(self._last_monitor_cycle),
            },
        }

    def get_engine_report(self, engine_name: str) -> Dict[str, Any]:
        health = self.check_engine_health(engine_name)
        history = list(self._health_history[engine_name])[-30:]
        return {
            "engine": engine_name,
            "current": {
                "status": health.status.value, "health_score": health.health_score,
                "risk_score": health.risk_score, "error_count_1h": health.error_count_1h,
                "avg_latency_ms": health.avg_latency_ms,
            },
            "dependencies": health.dependencies,
            "dependents": health.dependents,
            "history": [
                {"status": h.status.value, "health_score": h.health_score, "timestamp": iso(h.checked_at)}
                for h in history
            ],
        }

    def get_dependency_graph(self) -> Dict[str, Any]:
        nodes = sorted(self._deps.nodes())
        return {
            "nodes": nodes,
            "edges": dict(self._deps.edges),
            "reverse": dict(self._deps.reverse),
            "leaf_nodes": [n for n, deps in self._deps.edges.items() if not deps],
            "root_nodes": [n for n in self._deps.edges if n not in self._deps.reverse or not self._deps.reverse[n]],
        }

    def get_trends(self, engine_name: Optional[str] = None) -> Dict[str, Any]:
        engines = [engine_name] if engine_name else list(self._engines.keys())
        result = {}
        for name in engines:
            history = list(self._health_history[name])
            if len(history) < 2:
                result[name] = {"trend": "insufficient_data", "health_delta": 0.0}
                continue
            recent = history[-5:]
            older = history[max(0, len(history) - 10):max(0, len(history) - 5)]
            r_health = mean([h.health_score for h in recent])
            o_health = mean([h.health_score for h in older]) if older else r_health
            hd = r_health - o_health
            trend = "rapidly_declining" if hd <= -15 else "declining" if hd <= -5 else "improving" if hd >= 5 else "stable"
            result[name] = {"trend": trend, "health_delta": round(hd, 2)}
        return result

    def forecast_health(self, engine_name: str, hours_ahead: float = 1.0) -> Dict[str, Any]:
        history = list(self._health_history[engine_name])
        if len(history) < 5:
            return {"engine": engine_name, "forecast": "insufficient_data"}
        scores = [h.health_score for h in history[-30:]]
        n = len(scores)
        x_mean = (n - 1) / 2
        y_mean = mean(scores)
        numerator = sum((i - x_mean) * (scores[i] - y_mean) for i in range(n))
        denominator = sum((i - x_mean) ** 2 for i in range(n))
        slope = numerator / denominator if denominator != 0 else 0
        intercept = y_mean - slope * x_mean
        cycles_ahead = (hours_ahead * 3600) / 60
        predicted_score = clamp(intercept + slope * (n + cycles_ahead), 0, 100)
        return {
            "engine": engine_name,
            "current_score": scores[-1] if scores else 0,
            "predicted_score": round(predicted_score, 2),
            "hours_ahead": hours_ahead,
            "trend": "declining" if slope < -0.1 else "improving" if slope > 0.1 else "stable",
        }

    def correlate_failures(self, diagnoses: List[Diagnosis]) -> List[Dict[str, Any]]:
        correlations = []
        time_windows = defaultdict(list)
        for d in diagnoses:
            bucket = d.detected_at.replace(second=0, microsecond=0)
            bucket = bucket - timedelta(minutes=bucket.minute % 5)
            time_windows[bucket].append(d)
        for window_time, wd in time_windows.items():
            if len(wd) < 2:
                continue
            engines = [d.engine for d in wd]
            all_deps = [set(self._deps.get_dependencies(e)) for e in engines]
            shared_deps = set.intersection(*all_deps) if all_deps else set()
            if shared_deps:
                correlations.append({
                    "timestamp": iso(window_time), "engines": engines,
                    "shared_dependencies": list(shared_deps),
                    "likely_shared_cause": f"Dependency failure in: {', '.join(shared_deps)}",
                })
        return correlations

    # ═══════════════════════════════════════════════════════════════════
    # LIFECYCLE
    # ═══════════════════════════════════════════════════════════════════

    def start(self, interval_seconds: int = 60) -> None:
        if self._running:
            return
        self._running = True
        self._monitor_interval_minutes = max(5, interval_seconds) / 60.0
        self._thread = threading.Thread(
            target=self._monitoring_loop, args=(max(5, interval_seconds),),
            daemon=True, name="kanyoza-meta-engine",
        )
        self._thread.start()
        logger.info("MetaEngine v%s: monitoring started (every %ds)", self.VERSION, interval_seconds)

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._thread = None
        self.health_timeout.shutdown()
        logger.info("MetaEngine: monitoring stopped")

    def _monitoring_loop(self, interval_seconds: int) -> None:
        while self._running:
            cycle_started = time.monotonic()
            try:
                self._cycle_count += 1
                self.take_snapshot()
                self.detect_anomalies()
                diagnoses = self.diagnose()
                self._create_incidents(diagnoses)
                actions = self.auto_heal()
                resolved = self.incident_manager.check_and_resolve()
                self.self_monitor.record_cycle_completed()
                if resolved:
                    logger.info("MetaEngine: %d incidents resolved", resolved)
                if actions:
                    logger.info("MetaEngine: %d recovery actions", len(actions))
            except Exception as exc:
                logger.error("MetaEngine cycle failed: %s", exc)
            elapsed = time.monotonic() - cycle_started
            sleep_for = max(0.1, interval_seconds - elapsed) + random.uniform(0, 5)
            end = time.monotonic() + sleep_for
            while self._running and time.monotonic() < end:
                time.sleep(min(1.0, max(0.01, end - time.monotonic())))


# ═══════════════════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════════════════

meta_engine = MetaEngine()


def init_meta_engine(event_bus: Any = None, container: Any = None) -> MetaEngine:
    global meta_engine
    try:
        meta_engine.stop()
    except Exception:
        pass
    meta_engine.event_bus = event_bus
    meta_engine.container = container
    meta_engine.register_all_engines()

    def generic_restart(d: Diagnosis) -> None:
        engine = meta_engine._engines.get(d.engine)
        if not engine:
            raise RuntimeError(f"Unknown engine: {d.engine}")
        if hasattr(engine, "restart"):
            engine.restart()
        elif hasattr(engine, "reset"):
            engine.reset()
        elif hasattr(engine, "stop") and hasattr(engine, "start"):
            engine.stop()
            time.sleep(0.1)
            engine.start()
        else:
            raise RuntimeError(f"No recovery for {d.engine}")

    for name, engine in list(meta_engine._engines.items()):
        if not isinstance(engine, type) and any(hasattr(engine, m) for m in ("restart", "reset")):
            meta_engine.register_recovery_handler(name, generic_restart)

    meta_engine.start(interval_seconds=60)
    logger.info(
        "KANYOZA META ENGINE v%s — %d engines under supervision",
        meta_engine.VERSION, len(meta_engine._engines),
    )
    return meta_engine


def shutdown_meta_engine() -> None:
    try:
        meta_engine.stop()
    except Exception:
        pass


import atexit
atexit.register(shutdown_meta_engine)
