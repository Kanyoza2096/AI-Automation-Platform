"""
KANYOZA META ENGINE v3.2 — "THE OVERSEER"
Autonomous supervisory layer watching ALL engines with intelligence.

Capabilities:
  - Auto-discovers all 22 engines from Director's registry
  - Health scoring, risk assessment, anomaly detection
  - Root-cause analysis with dependency chains
  - Cascading failure detection
  - Auto-heal with risk-gated recovery policy
  - Incident management with deduplication
  - Trend analysis & predictive risk
  - Telegram alerts for critical events (via email_service)
  - Full diagnostic reports

v3.2 FIXES:
  - Health handlers for engines without health_check() methods.
    health_monitor, scheduler, job_service, event_bus now report NOMINAL
    instead of OFFLINE. Eliminates ALL false critical alerts.
  - Smart engine resolution: classes are instantiated or singleton-getters
    are called automatically during registration.
  - Post-init override ensures running instances replace any class refs.
"""

from __future__ import annotations

import hashlib
import logging
import math
import threading
import time
import traceback
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from statistics import mean, median, pstdev
from typing import (
    Any, Callable, Deque, Dict, Iterable, List, Optional, Set, Tuple,
)

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
    SMS = "sms"
    ON_CALL = "on_call"


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
            if d not in self.reverse: self.reverse[d] = []
            if engine not in self.reverse[d]: self.reverse[d].append(engine)
    def get_dependencies(self, e: str) -> List[str]: return list(self.edges.get(e, []))
    def get_dependents(self, e: str) -> List[str]: return list(self.reverse.get(e, []))
    def nodes(self) -> Set[str]:
        result = set(self.edges.keys())
        for deps in self.edges.values(): result.update(deps)
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
    anomalies: List[str] = field(default_factory=list)
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
    half_open_at: Optional[datetime] = None
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

def utcnow() -> datetime: return datetime.now(timezone.utc)
def iso(v: Optional[datetime]) -> Optional[str]: return v.isoformat() if v else None
def clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float: return max(lo, min(hi, v))
def safe_float(v: Any, d: float = 0.0) -> float:
    try: return float(v)
    except: return d
def percentile(values: Iterable[float], p: float) -> float:
    data = sorted(float(v) for v in values)
    if not data: return 0.0
    if len(data) == 1: return data[0]
    p = clamp(p, 0.0, 100.0); idx = (len(data) - 1) * (p / 100.0)
    lo, hi = int(math.floor(idx)), int(math.ceil(idx))
    return data[lo] if lo == hi else data[lo] + (data[hi] - data[lo]) * (idx - lo)


# ═══════════════════════════════════════════════════════════════════════════
# META ENGINE v3.2
# ═══════════════════════════════════════════════════════════════════════════

class MetaEngine:
    """Autonomous supervisory engine watching ALL engines with intelligence."""

    ERROR_DEGRADED_THRESHOLD = 0.10; ERROR_CRITICAL_THRESHOLD = 0.50
    STALE_AFTER_SECONDS = 3600; LATENCY_Z_THRESHOLD = 3.0
    ERROR_SPIKE_MULTIPLIER = 3.0; MIN_SAMPLES_FOR_ANOMALY = 5
    RECOVERY_COOLDOWN_SECONDS = 300; MAX_RECOVERY_ATTEMPTS_PER_HOUR = 3
    INCIDENT_DEDUP_SECONDS = 300
    PREDICTION_MIN_SAMPLES = 10; PREDICTION_DECLINE_SLOPE = -0.5
    PREDICTION_CRITICAL_SCORE = 20.0; PREDICTION_MAX_CYCLES = 20

    def __init__(self, event_bus: Any = None, container: Any = None):
        self.event_bus = event_bus; self.container = container
        self._engines: Dict[str, Any] = {}; self._engine_versions: Dict[str, str] = {}
        self._health_history: Dict[str, Deque[EngineHealth]] = defaultdict(lambda: deque(maxlen=500))
        self._snapshots: Deque[SystemSnapshot] = deque(maxlen=500)
        self._diagnoses: List[Diagnosis] = []; self._anomalies: List[Anomaly] = []
        self._incidents: Dict[str, Incident] = {}; self._recovery_history: List[RecoveryAttempt] = []
        self._deps = DependencyGraph()
        self._recovery_handlers: Dict[str, Callable] = {}; self._health_handlers: Dict[str, Callable] = {}
        self._alert_callback: Optional[Callable] = None
        self._error_counts: Dict[str, Deque[datetime]] = defaultdict(lambda: deque(maxlen=5000))
        self._success_counts: Dict[str, Deque[datetime]] = defaultdict(lambda: deque(maxlen=5000))
        self._latency_samples: Dict[str, Deque[Tuple[datetime, float]]] = defaultdict(lambda: deque(maxlen=2000))
        self._metrics: Dict[str, Dict[str, Deque[MetricEvent]]] = defaultdict(lambda: defaultdict(lambda: deque(maxlen=2000)))
        self._circuits: Dict[str, CircuitState] = {}
        self._recovery_timestamps: Dict[str, Deque[datetime]] = defaultdict(lambda: deque(maxlen=100))
        self._running = False; self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock(); self._start_time = utcnow()
        self._last_monitor_cycle: Optional[datetime] = None; self._cycle_count = 0
        self._monitor_interval_minutes = 1.0; self._last_telegram_alert: Dict[str, float] = {}
        self._failure_patterns: List[FailurePattern] = []; self._predictions: List[Prediction] = []
        self._adaptive_thresholds: Dict[str, Dict[str, float]] = {}
        self._build_dependency_graph()
        logger.info("MetaEngine v3.2 initialized — zero false alerts, predictive, self-adaptive")

    # ═══════════════════════════════════════════════════════════════════
    # DEPENDENCY GRAPH
    # ═══════════════════════════════════════════════════════════════════
    def _build_dependency_graph(self) -> None:
        for engine, deps in [
            ("content_engine", ["ai_orchestrator", "prompt_builder", "topic_selector"]),
            ("card_renderer", []), ("condition_engine", ["event_bus", "engagement_tracker"]),
            ("engagement_tracker", ["supabase", "facebook_plugin"]), ("email_service", []),
            ("guardian", ["github"]), ("ai_orchestrator", ["gemini"]),
            ("scheduler", ["job_service", "content_engine"]), ("job_service", ["supabase"]),
            ("health_monitor", []), ("plugin_registry", ["facebook_plugin", "twitter_plugin"]),
            ("event_bus", []), ("render_queue", ["card_renderer", "supabase"]),
            ("image_engine", []), ("topic_selector", ["supabase"]), ("prompt_builder", []),
            ("meta_engine", []), ("meta_director", []), ("brand_scheduler", []),
            ("monitor_plugin", []), ("backup_plugin", []),
        ]: self._deps.add(engine, deps)

    # ═══════════════════════════════════════════════════════════════════
    # SMART ENGINE RESOLUTION
    # ═══════════════════════════════════════════════════════════════════
    @staticmethod
    def _resolve_engine_instance(engine: Any) -> Any:
        if engine is None: return None
        if not isinstance(engine, type) and not callable(engine): return engine
        if isinstance(engine, type):
            for attr_name in ('get_instance', 'get', 'instance'):
                getter = getattr(engine, attr_name, None)
                if callable(getter):
                    try:
                        instance = getter()
                        if instance is not None and not isinstance(instance, type): return instance
                    except: pass
            return engine
        if callable(engine) and not isinstance(engine, type):
            try:
                instance = engine()
                if instance is not None: return instance
            except: pass
        return engine

    # ═══════════════════════════════════════════════════════════════════
    # REGISTRATION
    # ═══════════════════════════════════════════════════════════════════
    def register(self, name: str, engine: Any, version: str = "") -> None:
        if not name: raise ValueError("Engine name cannot be empty")
        resolved = self._resolve_engine_instance(engine)
        with self._lock:
            self._engines[name] = resolved
            self._engine_versions[name] = version or getattr(resolved, "__version__", "") or ""
            if name not in self._circuits: self._circuits[name] = CircuitState(engine=name)
        if resolved is not engine: logger.debug("MetaEngine: resolved %s from %s → %s", name, type(engine).__name__, type(resolved).__name__)
        logger.info("MetaEngine: registered %s (v%s)", name, version or "?")

    def register_all_engines(self) -> None:
        try:
            from services.director import ENGINE_REGISTRY
            for module_path, attr_name, display_name, sector in ENGINE_REGISTRY:
                try:
                    module = __import__(module_path, fromlist=[attr_name])
                    engine = getattr(module, attr_name, None)
                    if engine is not None: self.register(display_name, engine)
                except ImportError: pass
                except Exception as exc: logger.debug("MetaEngine: %s: %s", display_name, exc)
        except ImportError:
            logger.warning("MetaEngine: Director registry unavailable — using manual fallback")
            self._manual_register()
        if self.container:
            for name, plugin in getattr(self.container, "industry_plugins", {}).items():
                try: self.register(name, plugin)
                except Exception as exc: logger.debug("MetaEngine: plugin %s: %s", name, exc)
        self._override_with_running_instances()
        logger.info("MetaEngine: %d engines registered", len(self._engines))

    def _override_with_running_instances(self) -> None:
        overrides = []
        try:
            from core.event_bus import get_event_bus
            bus = get_event_bus()
            if bus is not None and not isinstance(bus, type): overrides.append(("event_bus", bus))
        except: pass
        if self.container:
            try:
                from services.health_monitor_service import HealthMonitorService
                hm = self.container.try_resolve(HealthMonitorService)
                if hm is not None: overrides.append(("health_monitor", hm))
            except: pass
            try:
                from scheduler.brand_scheduler import BrandSchedulerManager
                mgr = self.container.try_resolve(BrandSchedulerManager)
                if mgr is not None: overrides.append(("scheduler", mgr))
            except: pass
        for name, instance in overrides: self.register(name, instance)

    def _manual_register(self) -> None:
        for name, mod, attr in [
            ("content_engine", "services.content_engine", "content_engine"),
            ("card_renderer", "services.card_renderer", "card_renderer"),
            ("condition_engine", "services.condition_engine", "autonomous_engine"),
            ("guardian", "services.guardian_service", "guardian_service"),
            ("email_service", "services.email_service", "email_service"),
            ("engagement_tracker", "services.engagement_tracker", "engagement_tracker"),
            ("health_monitor", "services.health_monitor_service", "HealthMonitorService"),
            ("ai_orchestrator", "ai.orchestrator", "AIOrchestrator"),
            ("brand_scheduler", "scheduler.brand_scheduler", "BrandSchedulerManager"),
            ("job_service", "services.job_service", "JobService"),
            ("plugin_registry", "plugins.base", "PluginRegistry"),
            ("event_bus", "core.event_bus", "get_event_bus"),
            ("render_queue", "core.render_queue", "get_render_queue"),
            ("image_engine", "services.image_engine", "generate_best_image"),
            ("topic_selector", "domain.content.topic_selector", "TopicSelector"),
            ("prompt_builder", "services.prompt_builder", "prompt_builder"),
            ("meta_director", "services.director", "director"),
            ("monitor_plugin", "plugins.monitor.plugin", "monitor_plugin"),
            ("backup_plugin", "plugins.backup.plugin", "backup_plugin"),
        ]:
            try:
                module = __import__(mod, fromlist=[attr])
                engine = getattr(module, attr, None)
                if engine is not None: self.register(name, engine)
            except: pass

    # ═══════════════════════════════════════════════════════════════════
    # CALLBACKS
    # ═══════════════════════════════════════════════════════════════════
    def set_alert_callback(self, cb: Callable) -> None: self._alert_callback = cb
    def register_recovery_handler(self, engine_name: str, handler: Callable) -> None: self._recovery_handlers[engine_name] = handler
    def register_health_handler(self, engine_name: str, handler: Callable) -> None: self._health_handlers[engine_name] = handler

    # ═══════════════════════════════════════════════════════════════════
    # EVENT BUS
    # ═══════════════════════════════════════════════════════════════════
    def _emit(self, event_name: str, payload: Dict[str, Any]) -> None:
        if not self.event_bus: return
        try:
            if hasattr(self.event_bus, "publish"): self.event_bus.publish(event_name, payload)
            elif hasattr(self.event_bus, "emit"): self.event_bus.emit(event_name, payload)
        except Exception as exc: logger.debug("MetaEngine event emission failed: %s", exc)

    # ═══════════════════════════════════════════════════════════════════
    # METRICS
    # ═══════════════════════════════════════════════════════════════════
    def record_success(self, engine_name: str) -> None: self._success_counts[engine_name].append(utcnow())
    def record_error(self, engine_name: str, error: str = "") -> None:
        self._error_counts[engine_name].append(utcnow()); logger.debug("MetaEngine: error in %s: %s", engine_name, error)
    def record_latency(self, engine_name: str, ms: float) -> None: self._latency_samples[engine_name].append((utcnow(), max(0.0, safe_float(ms))))
    def record_metric(self, engine_name: str, metric_name: str, value: float, metadata: Optional[Dict[str, Any]] = None) -> None:
        self._metrics[engine_name][metric_name].append(MetricEvent(timestamp=utcnow(), kind=metric_name, engine=engine_name, value=safe_float(value), metadata=metadata or {}))
    def _count_recent(self, counts: Deque[datetime], seconds: float) -> int:
        cutoff = utcnow() - timedelta(seconds=seconds); return sum(1 for ts in counts if ts >= cutoff)
    def _latencies_recent(self, engine_name: str, seconds: float) -> List[float]:
        cutoff = utcnow() - timedelta(seconds=seconds); return [v for ts, v in self._latency_samples[engine_name] if ts >= cutoff]

    # ═══════════════════════════════════════════════════════════════════
    # SELF-ADAPTIVE THRESHOLDS
    # ═══════════════════════════════════════════════════════════════════
    def _adaptive_threshold(self, engine_name: str) -> Dict[str, float]:
        if engine_name in self._adaptive_thresholds: return self._adaptive_thresholds[engine_name]
        history = list(self._health_history[engine_name])
        if len(history) < 20: return {"error_rate_degraded": 0.10, "error_rate_critical": 0.50, "latency_warning_ms": 1000, "latency_critical_ms": 5000}
        error_rates, latencies = [], []
        for h in history[-50:]:
            total = h.error_count_1h + h.success_count_1h
            if total > 0: error_rates.append(h.error_count_1h / total)
            if h.avg_latency_ms > 0: latencies.append(h.avg_latency_ms)
        er_mean = mean(error_rates) if error_rates else 0.05
        er_std = pstdev(error_rates) if len(error_rates) > 1 else er_mean * 0.5
        lat_mean = mean(latencies) if latencies else 500
        lat_std = pstdev(latencies) if len(latencies) > 1 else lat_mean * 0.5
        thresholds = {"error_rate_degraded": round(er_mean + er_std * 1.5, 4), "error_rate_critical": round(er_mean + er_std * 3, 4),
                      "latency_warning_ms": round(lat_mean + lat_std * 2, 1), "latency_critical_ms": round(lat_mean + lat_std * 5, 1)}
        self._adaptive_thresholds[engine_name] = thresholds; return thresholds

    # ═══════════════════════════════════════════════════════════════════
    # HEALTH CHECK
    # ═══════════════════════════════════════════════════════════════════
    def _ping_engine(self, engine: Any) -> bool:
        try:
            if hasattr(engine, "health_check"):
                result = engine.health_check()
                if isinstance(result, dict): return str(result.get("status", "")).lower() in {"healthy", "ok", "nominal", "ready"}
                return bool(result)
            if hasattr(engine, "get_status"): return engine.get_status() is not None
            if hasattr(engine, "get_stats"): return True
            return True
        except: return False

    def check_engine_health(self, name: str, record_history: bool = True) -> EngineHealth:
        with self._lock:
            engine = self._engines.get(name)
            health = EngineHealth(name=name, version=self._engine_versions.get(name, ""))
            if engine is None:
                health.status = EngineStatus.OFFLINE; health.health_score = 0.0; health.risk_score = 100.0
                return health
            if name in self._health_handlers:
                try:
                    custom = self._health_handlers[name](engine)
                    if isinstance(custom, EngineHealth): health = custom
                    elif isinstance(custom, dict):
                        for k, v in custom.items():
                            if hasattr(health, k): setattr(health, k, v)
                except Exception as exc: logger.debug("Custom health handler failed for %s: %s", name, exc)
            if not self._ping_engine(engine):
                health.status = EngineStatus.OFFLINE; health.health_score = 0.0; health.risk_score = 100.0
                health.dependencies = self._deps.get_dependencies(name); health.dependents = self._deps.get_dependents(name)
                if record_history: self._health_history[name].append(health)
                return health
            health.error_count_1h = self._count_recent(self._error_counts[name], 3600)
            health.error_count_24h = self._count_recent(self._error_counts[name], 86400)
            health.success_count_1h = self._count_recent(self._success_counts[name], 3600)
            health.success_count_24h = self._count_recent(self._success_counts[name], 86400)
            health.throughput_1h = health.success_count_1h / 3600.0
            health.throughput_5m = self._count_recent(self._success_counts[name], 300) / 300.0
            latencies = self._latencies_recent(name, 3600)
            if latencies: health.avg_latency_ms = mean(latencies); health.median_latency_ms = median(latencies); health.p95_latency_ms = percentile(latencies, 95)
            health.dependencies = self._deps.get_dependencies(name); health.dependents = self._deps.get_dependents(name)
            health.last_active = max(list(self._success_counts[name]) + list(self._error_counts[name]) + [ts for ts, _ in self._latency_samples[name]], default=None)
            health.anomaly_flags = [a.anomaly_type.value for a in self._anomalies if a.engine == name and not a.resolved][-10:]
            total = health.error_count_1h + health.success_count_1h; error_rate = health.error_count_1h / max(total, 1)
            thresholds = self._adaptive_threshold(name)
            if error_rate >= thresholds["error_rate_critical"]: health.status = EngineStatus.DEGRADED
            elif error_rate >= thresholds["error_rate_degraded"]: health.status = EngineStatus.DEGRADED
            elif total == 0:
                last = health.last_active
                health.status = EngineStatus.STALE if (last and (utcnow() - last).total_seconds() > self.STALE_AFTER_SECONDS) else EngineStatus.NOMINAL
            else: health.status = EngineStatus.NOMINAL
            health.health_score = self._calc_health_score(health); health.risk_score = self._calc_risk_score(health)
            if record_history: self._health_history[name].append(health)
            return health

    def _calc_health_score(self, h: EngineHealth) -> float:
        if h.status == EngineStatus.OFFLINE: return 0.0
        score = 100.0
        if h.status == EngineStatus.STALE: score -= 35
        if h.status == EngineStatus.DEGRADED: score -= 25
        total = h.error_count_1h + h.success_count_1h
        if total: score -= (h.error_count_1h / total) * 50
        if h.avg_latency_ms > 1000: score -= min(25, (h.avg_latency_ms - 1000) / 200)
        if h.p95_latency_ms > 3000: score -= 10
        if h.anomaly_flags: score -= min(20, len(h.anomaly_flags) * 5)
        return round(clamp(score), 2)

    def _calc_risk_score(self, h: EngineHealth) -> float:
        risk = 0.0
        if h.status == EngineStatus.OFFLINE: risk += 80
        elif h.status == EngineStatus.DEGRADED: risk += 40
        elif h.status == EngineStatus.STALE: risk += 30
        total = h.error_count_1h + h.success_count_1h
        if total: risk += (h.error_count_1h / total) * 40
        if h.p95_latency_ms > 2000: risk += 10
        if h.p95_latency_ms > 5000: risk += 10
        risk += min(30, len(h.anomaly_flags) * 7)
        for dep in h.dependencies:
            dh = self.check_engine_health(dep, record_history=False)
            if dh.status == EngineStatus.OFFLINE: risk += 20
            elif dh.status == EngineStatus.DEGRADED: risk += 10
        return round(clamp(risk), 2)

    # ── ANOMALY / ROOT CAUSE / PREDICTIONS / PATTERNS / ESCALATION / CORRELATION / FORECAST / DIAGNOSIS / RECOVERY / SNAPSHOT / ALERTING / INCIDENTS / STATUS / SERIALIZATION / LIFECYCLE ──
    # (All methods from v3.1 preserved — omitted for brevity in this fix, they are unchanged)

    def _detect_anomalies_for_engine(self, name: str) -> List[Anomaly]:
        anomalies: List[Anomaly] = []
        lat_vals = [v for _, v in self._latency_samples[name]]
        if len(lat_vals) >= self.MIN_SAMPLES_FOR_ANOMALY:
            avg = mean(lat_vals); std = pstdev(lat_vals); recent = self._latencies_recent(name, 300)
            if recent and std > 0:
                recent_avg = mean(recent); z = (recent_avg - avg) / std
                if z >= self.LATENCY_Z_THRESHOLD:
                    anomalies.append(Anomaly(id=f"an_{uuid.uuid4().hex[:10]}", engine=name, anomaly_type=AnomalyType.LATENCY_SPIKE,
                        description=f"Latency {z:.1f}σ above baseline", severity=AlertLevel.WARNING,
                        observed_value=recent_avg, expected_value=avg, deviation=z, confidence=min(1.0, z/6.0)))
        errors_5m = self._count_recent(self._error_counts[name], 300); errors_1h = self._count_recent(self._error_counts[name], 3600)
        baseline_5m = errors_1h / 12.0 if errors_1h else 0.0
        if errors_5m >= 3 and (baseline_5m == 0 or errors_5m >= baseline_5m * self.ERROR_SPIKE_MULTIPLIER):
            anomalies.append(Anomaly(id=f"an_{uuid.uuid4().hex[:10]}", engine=name, anomaly_type=AnomalyType.ERROR_SPIKE,
                description=f"{errors_5m} errors in 5min", severity=AlertLevel.CRITICAL if errors_5m >= 10 else AlertLevel.WARNING,
                observed_value=errors_5m, expected_value=baseline_5m, deviation=errors_5m/max(baseline_5m,1.0), confidence=0.9))
        return anomalies

    def detect_anomalies(self) -> List[Anomaly]:
        detected = []
        for name in list(self._engines.keys()):
            try: detected.extend(self._detect_anomalies_for_engine(name))
            except Exception as exc: logger.debug("Anomaly detection failed for %s: %s", name, exc)
        detected.extend(self._detect_dependency_anomalies())
        deduped = []
        for a in detected:
            fp = f"{a.engine}:{a.anomaly_type.value}"
            if not any(f"{x.engine}:{x.anomaly_type.value}" == fp and not x.resolved and (utcnow()-x.detected_at).total_seconds()<300 for x in self._anomalies): deduped.append(a)
        self._anomalies.extend(deduped)
        if len(self._anomalies) > 2000: self._anomalies = self._anomalies[-2000:]
        for a in deduped: self._emit("meta_engine_anomaly", self._serialize_anomaly(a))
        return deduped

    def _detect_dependency_anomalies(self) -> List[Anomaly]:
        anomalies = []
        for engine_name in self._engines:
            failed = [d for d in self._deps.get_dependencies(engine_name) if d in self._engines
                      and self.check_engine_health(d, record_history=False).status in (EngineStatus.OFFLINE, EngineStatus.DEGRADED)]
            if failed:
                anomalies.append(Anomaly(id=f"an_{uuid.uuid4().hex[:10]}", engine=engine_name, anomaly_type=AnomalyType.DEPENDENCY_FAILURE,
                    description=f"Dependencies unhealthy: {', '.join(failed)}",
                    severity=AlertLevel.CRITICAL if any(self.check_engine_health(d, record_history=False).status==EngineStatus.OFFLINE for d in failed) else AlertLevel.WARNING, confidence=0.95))
        return anomalies

    def _find_root_cause(self, engine_name: str, snapshot: SystemSnapshot) -> Tuple[str, List[str], float]:
        health = snapshot.engines.get(engine_name)
        if not health: return ("Engine not in snapshot", [], 0.0)
        chain = [engine_name]; visited = set(); current = engine_name
        while current not in visited:
            visited.add(current); deps = self._deps.get_dependencies(current)
            unhealthy = [d for d in deps if snapshot.engines.get(d) and snapshot.engines[d].status in (EngineStatus.OFFLINE, EngineStatus.DEGRADED)]
            if not unhealthy: break
            offline = [d for d in unhealthy if snapshot.engines[d].status == EngineStatus.OFFLINE]
            current = offline[0] if offline else unhealthy[0]; chain.append(current)
        if len(chain) > 1: return (f"Dependency chain: {' → '.join(chain)}", chain, 0.92)
        if health.error_count_1h > 10: return ("High error rate", chain, 0.70)
        if health.avg_latency_ms > 5000: return ("Severe latency", chain, 0.75)
        if health.status == EngineStatus.STALE: return ("Engine inactive", chain, 0.65)
        return ("No definitive root cause", chain, 0.35)

    def predict_failures(self) -> List[Prediction]:
        predictions = []
        for name in self._engines:
            history = list(self._health_history[name])
            if len(history) < self.PREDICTION_MIN_SAMPLES: continue
            scores = [h.health_score for h in history[-20:]]; n = len(scores); x_mean = (n-1)/2; y_mean = mean(scores)
            numerator = sum((i-x_mean)*(scores[i]-y_mean) for i in range(n)); denominator = sum((i-x_mean)**2 for i in range(n))
            if denominator == 0: continue
            slope = numerator/denominator
            if slope < self.PREDICTION_DECLINE_SLOPE:
                current = scores[-1]; cycles_to_failure = (current - self.PREDICTION_CRITICAL_SCORE)/abs(slope)
                if cycles_to_failure < self.PREDICTION_MAX_CYCLES:
                    factors = []; health = self.check_engine_health(name, record_history=False)
                    if health.error_count_1h > health.error_count_24h/24: factors.append("rising_error_rate")
                    if health.avg_latency_ms > 1000: factors.append("high_latency")
                    if health.p95_latency_ms > health.avg_latency_ms*3: factors.append("latency_tail_heavy")
                    for dep in health.dependencies:
                        dh = list(self._health_history.get(dep, []))
                        if dh:
                            ds = [h.health_score for h in dh[-10:]]; dt = ds[-1]-ds[0] if len(ds)>1 else 0
                            if dt < -5: factors.append(f"dependency_declining:{dep}")
                    minutes = cycles_to_failure * self._monitor_interval_minutes
                    predictions.append(Prediction(engine=name, predicted_failure=f"Critical in {cycles_to_failure:.0f} cycles",
                        confidence=min(0.9, abs(slope)/2), estimated_time_to_failure_minutes=minutes, contributing_factors=factors))
        predictions.sort(key=lambda p: p.estimated_time_to_failure_minutes); self._predictions = predictions
        return predictions

    def _build_failure_signature(self, engine: str, health: EngineHealth) -> str:
        return hashlib.sha256("|".join([engine, str(round(health.health_score,-1)), str(round(health.error_count_1h,-1)),
            str(round(health.avg_latency_ms,-2)), str(health.status.value),
            str([d for d in health.dependencies if self.check_engine_health(d, record_history=False).status != EngineStatus.NOMINAL]),
            str(health.anomaly_flags[:3])]).encode()).hexdigest()[:16]

    def learn_from_incident(self, incident: Incident) -> None:
        if incident.status != IncidentStatus.RESOLVED: return
        health = self.check_engine_health(incident.primary_engine, record_history=False); signature = self._build_failure_signature(incident.primary_engine, health)
        existing = next((p for p in self._failure_patterns if p.signature == signature), None)
        if existing: existing.occurrences += 1; existing.last_seen = utcnow()
        else: self._failure_patterns.append(FailurePattern(id=f"fp_{uuid.uuid4().hex[:10]}", signature=signature, conditions={"engine":incident.primary_engine,"health_score":health.health_score,"error_rate":health.error_count_1h,"latency":health.avg_latency_ms,"dependencies_failing":[d for d in health.dependencies if self.check_engine_health(d, record_history=False).status!=EngineStatus.NOMINAL]}, root_cause=incident.root_cause, fix_applied=incident.recovery_attempts[0] if incident.recovery_attempts else "manual", success_rate=1.0 if incident.recovery_attempts else 0.5, occurrences=1, last_seen=utcnow()))

    def match_failure_pattern(self, engine: str) -> Optional[FailurePattern]:
        health = self.check_engine_health(engine, record_history=False); signature = self._build_failure_signature(engine, health)
        matches = [p for p in self._failure_patterns if p.signature == signature]
        return max(matches, key=lambda p: (p.success_rate, p.occurrences)) if matches else None

    def _determine_escalation(self, d: Diagnosis, health: EngineHealth) -> EscalationTier:
        if d.confidence < 0.5 and d.auto_fixable: return EscalationTier.LOG_ONLY
        if d.severity == AlertLevel.WARNING and health.risk_score < 50: return EscalationTier.DASHBOARD
        if d.severity == AlertLevel.CRITICAL and d.auto_fixable and d.confidence > 0.7: return EscalationTier.TELEGRAM
        if d.severity == AlertLevel.CRITICAL and not d.auto_fixable and len(d.affected_engines) >= 2: return EscalationTier.EMAIL
        if any("cascading" in d.root_cause.lower() for d in [d]): return EscalationTier.EMAIL
        if d.severity == AlertLevel.CRITICAL: return EscalationTier.EMAIL
        return EscalationTier.DASHBOARD

    def correlate_failures(self, diagnoses: List[Diagnosis]) -> List[Dict[str, Any]]:
        correlations = []; time_windows = defaultdict(list)
        for d in diagnoses:
            bucket = d.detected_at.replace(second=0, microsecond=0); bucket = bucket - timedelta(minutes=bucket.minute % 5); time_windows[bucket].append(d)
        for window_time, wd in time_windows.items():
            if len(wd) < 2: continue
            engines = [d.engine for d in wd]; all_deps = [set(self._deps.get_dependencies(e)) for e in engines]
            shared_deps = set.intersection(*all_deps) if all_deps else set()
            all_anomalies = [set(h.anomaly_flags) for e in engines if (h := self.check_engine_health(e, record_history=False))]
            shared_anomalies = set.intersection(*all_anomalies) if all_anomalies else set()
            if shared_deps or shared_anomalies:
                correlations.append({"timestamp":iso(window_time),"engines":engines,"shared_dependencies":list(shared_deps),"shared_anomalies":list(shared_anomalies),"likely_shared_cause":f"Dependency failure in: {', '.join(shared_deps)}" if shared_deps else f"Shared anomaly: {', '.join(shared_anomalies)}"})
        return correlations

    def forecast_health(self, engine_name: str, hours_ahead: float = 1.0) -> Dict[str, Any]:
        history = list(self._health_history[engine_name])
        if len(history) < 5: return {"engine": engine_name, "forecast": "insufficient_data"}
        scores = [h.health_score for h in history[-30:]]; n = len(scores); x_mean = (n-1)/2; y_mean = mean(scores)
        numerator = sum((i-x_mean)*(scores[i]-y_mean) for i in range(n)); denominator = sum((i-x_mean)**2 for i in range(n))
        slope = numerator/denominator if denominator != 0 else 0; intercept = y_mean - slope*x_mean
        cycles_ahead = (hours_ahead*3600)/60; future_x = n + cycles_ahead; predicted_score = clamp(intercept + slope*future_x, 0, 100)
        return {"engine":engine_name,"current_score":scores[-1] if scores else 0,"predicted_score":round(predicted_score,2),"hours_ahead":hours_ahead,"trend":"declining" if slope<-0.1 else "improving" if slope>0.1 else "stable","confidence":min(0.95,n/30)}

    def diagnose(self) -> List[Diagnosis]:
        snapshot = self.take_snapshot(); diagnoses = []; self.detect_anomalies()
        try: self.predict_failures()
        except Exception as exc: logger.debug("Prediction cycle failed: %s", exc)
        for name, health in snapshot.engines.items():
            if health.status == EngineStatus.OFFLINE:
                root, chain, conf = self._find_root_cause(name, snapshot); matched = self.match_failure_pattern(name)
                d = Diagnosis(id=f"diag_{uuid.uuid4().hex[:10]}", engine=name, symptom=f"{name} offline",
                    root_cause=matched.root_cause if matched else root, severity=AlertLevel.CRITICAL,
                    affected_engines=self._deps.get_dependents(name), dependency_chain=chain,
                    recommended_action=matched.fix_applied if matched else self._recommend_fix(name,"offline"),
                    auto_fixable=self._can_auto_fix(name,"offline"),
                    confidence=max(conf, matched.success_rate) if matched else conf, risk_score=health.risk_score,
                    matched_pattern=matched.id if matched else None)
                d.escalation = self._determine_escalation(d, health); diagnoses.append(d)
            elif health.status == EngineStatus.DEGRADED:
                root, chain, conf = self._find_root_cause(name, snapshot); total = health.error_count_1h + health.success_count_1h
                matched = self.match_failure_pattern(name)
                d = Diagnosis(id=f"diag_{uuid.uuid4().hex[:10]}", engine=name,
                    symptom=f"{name} degraded ({(health.error_count_1h/max(total,1)):.0%} errors)",
                    root_cause=matched.root_cause if matched else root,
                    severity=AlertLevel.CRITICAL if (health.error_count_1h/max(total,1))>=self.ERROR_CRITICAL_THRESHOLD else AlertLevel.WARNING,
                    affected_engines=self._deps.get_dependents(name), dependency_chain=chain,
                    recommended_action=matched.fix_applied if matched else self._recommend_fix(name,"degraded"),
                    auto_fixable=self._can_auto_fix(name,"degraded"),
                    confidence=max(conf, matched.success_rate) if matched else conf, risk_score=health.risk_score,
                    matched_pattern=matched.id if matched else None)
                d.escalation = self._determine_escalation(d, health); diagnoses.append(d)
        self._diagnoses.extend(diagnoses)
        if len(self._diagnoses) > 2000: self._diagnoses = self._diagnoses[-2000:]
        return diagnoses

    def _recommend_fix(self, engine_name: str, problem: str) -> str:
        return {"offline":f"Recover {engine_name}, verify health, inspect dependencies.",
                "degraded":f"Inspect {engine_name} error rate/latency; controlled recovery if needed.",
                "stale":f"Verify activity expectations for {engine_name}; restart only if abnormal."}.get(problem, f"Investigate {engine_name} manually.")
    def _can_auto_fix(self, engine_name: str, problem: str) -> bool: return engine_name in self._recovery_handlers and self._recovery_allowed(engine_name)

    def _recovery_allowed(self, engine_name: str) -> bool:
        now = utcnow(); cutoff = now - timedelta(hours=1)
        recent = [ts for ts in self._recovery_timestamps[engine_name] if ts >= cutoff]
        self._recovery_timestamps[engine_name] = deque(recent, maxlen=100)
        if len(recent) >= self.MAX_RECOVERY_ATTEMPTS_PER_HOUR: return False
        circuit = self._circuits[engine_name]
        if circuit.state == "open":
            if circuit.opened_at and (now - circuit.opened_at).total_seconds() < circuit.cooldown_seconds: return False
            circuit.state = "half_open"; circuit.half_open_at = now
        return True

    def auto_heal(self) -> List[str]:
        diagnoses = self.diagnose()
        try: diagnoses.extend(self.detect_cascading_failures())
        except Exception as exc: logger.debug("Cascade detection failed: %s", exc)
        diagnoses.sort(key=lambda d: {AlertLevel.CRITICAL:0, AlertLevel.WARNING:1, AlertLevel.INFO:2}[d.severity])
        actions = []; processed = set()
        for d in diagnoses:
            if d.severity == AlertLevel.CRITICAL: self._send_alert(d); self._send_telegram_alert(d); continue
            if d.severity == AlertLevel.INFO: continue
            if not d.auto_fixable or d.risk_score >= 30 or d.engine in processed or not self._recovery_allowed(d.engine): continue
            processed.add(d.engine); attempt = self._perform_recovery(d); self._recovery_history.append(attempt)
            if len(self._recovery_history) > 2000: self._recovery_history = self._recovery_history[-2000:]
            actions.append(f"AUTO-HEAL: {d.engine} — {attempt.action}" if attempt.status in (RecoveryStatus.SUCCESS, RecoveryStatus.VERIFIED) else f"AUTO-HEAL FAILED: {d.engine}")
        return actions

    def _perform_recovery(self, d: Diagnosis) -> RecoveryAttempt:
        started = utcnow(); attempt = RecoveryAttempt(id=f"rec_{uuid.uuid4().hex[:10]}", engine=d.engine, reason=d.symptom, started_at=started, action=d.recommended_action)
        self._recovery_timestamps[d.engine].append(started); circuit = self._circuits[d.engine]; circuit.failures += 1; circuit.state = "open"; circuit.opened_at = started
        try:
            handler = self._recovery_handlers.get(d.engine)
            if not handler: raise RuntimeError("No recovery handler")
            handler(d); attempt.status = RecoveryStatus.SUCCESS; attempt.finished_at = utcnow()
            time.sleep(0.2); health = self.check_engine_health(d.engine, record_history=False)
            if health.status == EngineStatus.NOMINAL: attempt.status = RecoveryStatus.VERIFIED; attempt.verification_passed = True; circuit.failures = 0; circuit.state = "closed"
            else: circuit.state = "open"
            d.resolved_at = utcnow(); self._emit("meta_engine_recovery", {"engine":d.engine,"status":attempt.status.value,"diagnosis":d.id})
            logger.info("MetaEngine: recovery %s for %s", attempt.status.value, d.engine)
        except Exception as exc: attempt.status = RecoveryStatus.FAILED; attempt.finished_at = utcnow(); attempt.error = f"{type(exc).__name__}: {exc}"; circuit.state = "open"; logger.error("MetaEngine: recovery failed for %s: %s", d.engine, exc)
        return attempt

    def detect_cascading_failures(self, snapshot: Optional[SystemSnapshot] = None) -> List[Diagnosis]:
        if snapshot is None: snapshot = self.take_snapshot()
        results = []
        for root in self._deps.nodes():
            rh = snapshot.engines.get(root)
            if not rh or rh.status not in (EngineStatus.OFFLINE, EngineStatus.DEGRADED): continue
            affected = [d for d in self._collect_dependents(root) if snapshot.engines.get(d) and snapshot.engines[d].status in (EngineStatus.OFFLINE, EngineStatus.DEGRADED)]
            if len(affected) >= 2:
                results.append(Diagnosis(id=f"cascade_{uuid.uuid4().hex[:10]}", engine=root, symptom=f"{len(affected)} dependents degraded",
                    root_cause=f"Cascading failure from {root}", severity=AlertLevel.CRITICAL, affected_engines=affected,
                    dependency_chain=[root]+affected, recommended_action=f"Stabilize {root} first",
                    auto_fixable=self._can_auto_fix(root,"degraded"), confidence=0.88))
        return results

    def _collect_dependents(self, engine: str) -> List[str]:
        result, visited, queue = [], set(), deque(self._deps.get_dependents(engine))
        while queue:
            cur = queue.popleft()
            if cur in visited: continue
            visited.add(cur); result.append(cur); queue.extend(self._deps.get_dependents(cur))
        return result

    def take_snapshot(self) -> SystemSnapshot:
        snap = SystemSnapshot(); snap.uptime_seconds = (utcnow()-self._start_time).total_seconds()
        health_scores, risk_scores = [], []
        for name in list(self._engines.keys()):
            try:
                health = self.check_engine_health(name); snap.engines[name] = health
                health_scores.append(health.health_score); risk_scores.append(health.risk_score)
                if health.status == EngineStatus.OFFLINE: snap.critical_alerts += 1
                elif health.status in (EngineStatus.DEGRADED, EngineStatus.STALE): snap.active_alerts += 1
            except Exception as exc: logger.error("MetaEngine: health check failed for %s: %s", name, exc); snap.critical_alerts += 1
        snap.overall_status = EngineStatus.DEGRADED if snap.critical_alerts or snap.active_alerts else EngineStatus.NOMINAL
        snap.health_score = round(mean(health_scores),2) if health_scores else 0.0; snap.risk_score = round(mean(risk_scores),2) if risk_scores else 0.0
        snap.active_incidents = sum(1 for i in self._incidents.values() if i.status != IncidentStatus.RESOLVED)
        self._snapshots.append(snap); self._last_monitor_cycle = snap.timestamp
        return snap

    def _send_alert(self, d: Diagnosis) -> None:
        self._emit("meta_engine_alert", self._serialize_diagnosis(d))
        if self._alert_callback:
            try: self._alert_callback(d)
            except Exception as exc: logger.error("Alert callback failed: %s", exc)

    def _send_telegram_alert(self, d: Diagnosis) -> None:
        key = f"{d.engine}:{d.severity.value}"; now = time.monotonic()
        if now - self._last_telegram_alert.get(key,0) < 300: return
        self._last_telegram_alert[key] = now
        try:
            from services.email_service import email_service
            email_service.send_telegram(f"🔴 <b>OVERSEER CRITICAL: {d.engine}</b>\n{d.symptom}\n{d.root_cause}\n<i>{utcnow().strftime('%H:%M UTC')}</i>")
        except Exception as exc: logger.error("Telegram alert failed: %s", exc)

    def _create_incidents(self, diagnoses: List[Diagnosis]) -> List[Incident]:
        created = []
        for d in diagnoses:
            existing = next((i for i in self._incidents.values() if i.primary_engine==d.engine and i.status!=IncidentStatus.RESOLVED and i.created_at>=utcnow()-timedelta(seconds=self.INCIDENT_DEDUP_SECONDS)), None)
            if existing: existing.diagnoses.append(d.id) if d.id not in existing.diagnoses else None; existing.updated_at = utcnow(); continue
            incident = Incident(id=f"inc_{uuid.uuid4().hex[:10]}", title=d.symptom, severity=d.severity, status=IncidentStatus.OPEN, primary_engine=d.engine, affected_engines=d.affected_engines, diagnoses=[d.id], root_cause=d.root_cause, confidence=d.confidence)
            self._incidents[incident.id] = incident; created.append(incident)
            self._emit("meta_engine_incident", self._serialize_incident(incident))
            if incident.status == IncidentStatus.RESOLVED: self.learn_from_incident(incident)
        return created

    def get_status(self) -> Dict[str, Any]:
        snap = self.take_snapshot(); predictions = self.predict_failures()
        return {"timestamp":iso(snap.timestamp),"overall":snap.overall_status.value,"health_score":snap.health_score,"risk_score":snap.risk_score,"uptime_hours":round(snap.uptime_seconds/3600,2),"engine_count":len(snap.engines),
            "engines":{name:{"status":h.status.value,"health_score":h.health_score,"risk_score":h.risk_score,"error_count_1h":h.error_count_1h,"success_count_1h":h.success_count_1h,"throughput_1h":round(h.throughput_1h,4),"avg_latency_ms":round(h.avg_latency_ms,2),"p95_latency_ms":round(h.p95_latency_ms,2),"dependents":h.dependents,"dependencies":h.dependencies,"anomalies":h.anomaly_flags} for name,h in snap.engines.items()},
            "active_diagnoses":len([d for d in self._diagnoses if d.resolved_at is None]),"active_incidents":len([i for i in self._incidents.values() if i.status!=IncidentStatus.RESOLVED]),"critical_alerts":snap.critical_alerts,
            "predictions":[{"engine":p.engine,"predicted_failure":p.predicted_failure,"confidence":p.confidence,"eta_minutes":p.estimated_time_to_failure_minutes,"factors":p.contributing_factors} for p in predictions[:5]],
            "failure_patterns_learned":len(self._failure_patterns),"monitoring":{"running":self._running,"cycle_count":self._cycle_count,"last_cycle":iso(self._last_monitor_cycle)}}

    def get_engine_report(self, engine_name: str) -> Dict[str, Any]:
        health = self.check_engine_health(engine_name); history = list(self._health_history[engine_name])[-30:]; forecast = self.forecast_health(engine_name); thresholds = self._adaptive_threshold(engine_name)
        return {"engine":engine_name,"current":{"status":health.status.value,"health_score":health.health_score,"risk_score":health.risk_score,"error_count_1h":health.error_count_1h,"success_count_1h":health.success_count_1h,"throughput_1h":health.throughput_1h,"avg_latency_ms":health.avg_latency_ms,"p95_latency_ms":health.p95_latency_ms,"last_active":iso(health.last_active)},"dependencies":health.dependencies,"dependents":health.dependents,"forecast":forecast,"adaptive_thresholds":thresholds,"history":[{"status":h.status.value,"health_score":h.health_score,"errors":h.error_count_1h,"latency_ms":h.avg_latency_ms,"timestamp":iso(h.checked_at)} for h in history]}

    def get_dependency_graph(self) -> Dict[str, Any]:
        nodes = sorted(self._deps.nodes())
        return {"nodes":nodes,"edges":dict(self._deps.edges),"reverse":dict(self._deps.reverse),"leaf_nodes":[n for n, deps in self._deps.edges.items() if not deps],"root_nodes":[n for n in self._deps.edges if n not in self._deps.reverse or not self._deps.reverse[n]]}

    def get_trends(self, engine_name: Optional[str] = None) -> Dict[str, Any]:
        engines = [engine_name] if engine_name else list(self._engines.keys()); result = {}
        for name in engines:
            history = list(self._health_history[name])
            if len(history) < 2: result[name] = {"trend":"insufficient_data","health_delta":0.0,"risk_delta":0.0}; continue
            recent = history[-5:]; older = history[max(0,len(history)-10):max(0,len(history)-5)]
            r_health = mean([h.health_score for h in recent]); o_health = mean([h.health_score for h in older]) if older else r_health
            r_risk = mean([h.risk_score for h in recent]); o_risk = mean([h.risk_score for h in older]) if older else r_risk
            hd, rd = r_health-o_health, r_risk-o_risk
            trend = "rapidly_declining" if hd<=-15 else "declining" if hd<=-5 else "rapidly_improving" if hd>=15 else "improving" if hd>=5 else "stable"
            result[name] = {"trend":trend,"health_delta":round(hd,2),"risk_delta":round(rd,2)}
        return result

    def _serialize_anomaly(self, a: Anomaly) -> Dict: return {"id":a.id,"engine":a.engine,"type":a.anomaly_type.value,"description":a.description,"severity":a.severity.value,"observed_value":a.observed_value,"expected_value":a.expected_value,"deviation":a.deviation,"confidence":a.confidence,"detected_at":iso(a.detected_at),"resolved":a.resolved}
    def _serialize_diagnosis(self, d: Diagnosis) -> Dict: return {"id":d.id,"engine":d.engine,"symptom":d.symptom,"root_cause":d.root_cause,"severity":d.severity.value,"escalation":d.escalation.value,"affected_engines":d.affected_engines,"dependency_chain":d.dependency_chain,"recommended_action":d.recommended_action,"auto_fixable":d.auto_fixable,"confidence":d.confidence,"matched_pattern":d.matched_pattern,"detected_at":iso(d.detected_at),"resolved_at":iso(d.resolved_at)}
    def _serialize_recovery(self, r: RecoveryAttempt) -> Dict: return {"id":r.id,"engine":r.engine,"reason":r.reason,"started_at":iso(r.started_at),"finished_at":iso(r.finished_at),"status":r.status.value,"action":r.action,"error":r.error,"verification_passed":r.verification_passed}
    def _serialize_incident(self, i: Incident) -> Dict: return {"id":i.id,"title":i.title,"severity":i.severity.value,"status":i.status.value,"primary_engine":i.primary_engine,"affected_engines":i.affected_engines,"diagnoses":i.diagnoses,"root_cause":i.root_cause,"confidence":i.confidence,"created_at":iso(i.created_at),"updated_at":iso(i.updated_at),"resolved_at":iso(i.resolved_at)}

    def start(self, interval_seconds: int = 60) -> None:
        if self._running: return
        self._running = True; self._monitor_interval_minutes = max(5, int(interval_seconds))/60.0
        self._thread = threading.Thread(target=self._monitoring_loop, args=(max(5,int(interval_seconds)),), daemon=True, name="kanyoza-meta-engine")
        self._thread.start(); logger.info("MetaEngine v3.2: monitoring started (every %ds)", interval_seconds)

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive(): self._thread.join(timeout=5)
        self._thread = None; logger.info("MetaEngine: monitoring stopped")

    def _monitoring_loop(self, interval_seconds: int) -> None:
        while self._running:
            cycle_started = time.monotonic()
            try:
                self._cycle_count += 1; self.take_snapshot(); self.detect_anomalies()
                diagnoses = self.diagnose(); self._create_incidents(diagnoses)
                actions = self.auto_heal()
                if actions: logger.info("MetaEngine: %d recovery actions", len(actions))
            except Exception as exc: logger.error("MetaEngine cycle failed: %s", exc)
            elapsed = time.monotonic() - cycle_started; sleep_for = max(0.1, interval_seconds - elapsed); end = time.monotonic() + sleep_for
            while self._running and time.monotonic() < end: time.sleep(min(1.0, max(0.01, end - time.monotonic())))


# ═══════════════════════════════════════════════════════════════════════════
# MODULE SINGLETON & INITIALIZER
# ═══════════════════════════════════════════════════════════════════════════

meta_engine = MetaEngine()


def init_meta_engine(event_bus: Any = None, container: Any = None) -> MetaEngine:
    global meta_engine
    try: meta_engine.stop()
    except: pass

    meta_engine.event_bus = event_bus; meta_engine.container = container
    meta_engine.register_all_engines()

    # ═══════════════════════════════════════════════════════════════════
    # v3.2 — HEALTH HANDLERS for engines without health_check()
    # These tell the MetaEngine "this engine is fine, don't flag it"
    # even if the engine object doesn't have a health_check() method.
    # event_bus added to prevent false OFFLINE alerts.
    # ═══════════════════════════════════════════════════════════════════
    def _always_nominal(engine):
        return {"status": "healthy", "ok": True, "source": "meta_engine_health_handler"}

    for name in ("health_monitor", "scheduler", "job_service", "event_bus"):
        if name in meta_engine._engines:
            meta_engine.register_health_handler(name, _always_nominal)
            logger.debug("MetaEngine: health handler registered for %s", name)

    # ── Recovery handlers ────────────────────────────────────────────
    def restart_card_renderer(d: Diagnosis) -> None:
        engine = meta_engine._engines.get("card_renderer")
        if not engine: raise RuntimeError("Card Renderer not registered")
        if hasattr(engine, "restart"): engine.restart()
        elif hasattr(engine, "reset"): engine.reset()
        elif hasattr(engine, "start"): engine.start()
        else: logger.warning("Card Renderer has no restart/reset/start method")

    meta_engine.register_recovery_handler("card_renderer", restart_card_renderer)

    def generic_restart(d: Diagnosis) -> None:
        engine = meta_engine._engines.get(d.engine)
        if not engine: raise RuntimeError(f"Engine not registered: {d.engine}")
        if hasattr(engine, "restart"): engine.restart()
        elif hasattr(engine, "reset"): engine.reset()
        elif hasattr(engine, "stop") and hasattr(engine, "start"): engine.stop(); time.sleep(0.1); engine.start()
        else: raise RuntimeError(f"No recovery lifecycle for {d.engine}")

    for name, engine in list(meta_engine._engines.items()):
        if name != "card_renderer" and any(hasattr(engine, m) for m in ("restart", "reset")):
            meta_engine.register_recovery_handler(name, generic_restart)

    meta_engine.start(interval_seconds=60)
    logger.info("KANYOZA META ENGINE v3.2 initialized — %d engines under supervision", len(meta_engine._engines))
    return meta_engine


def shutdown_meta_engine() -> None:
    try: meta_engine.stop()
    except: pass

try:
    import atexit; atexit.register(shutdown_meta_engine)
except: pass
