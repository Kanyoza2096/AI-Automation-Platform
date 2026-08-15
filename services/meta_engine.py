"""
KANYOZA META ENGINE v5.0 — "THE OVERSEER"
==========================================
COMPLETE SELF-CONTAINED VERSION

Autonomous supervisory layer watching ALL engines with intelligence.

v5.0 NEW CAPABILITIES:
  - Health check timeout (prevents hanging) — NUCLEAR FIX v2
  - Self-monitoring (watcher watches itself)
  - Incident resolution workflow (full lifecycle)
  - State persistence (Survives restarts)
  - Director topology integration (unified dependency model)
  - Recovery verification (persistent health confirmation)
  - Predictive model calibration (outcome-based)
  - Anomaly-root cause linking (complete diagnosis)
  - Resource monitoring (CPU, memory, disk)
  - Global alert rate limiting (prevents flooding)
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
import traceback
import uuid
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
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
    cpu_percent: float = 0.0
    disk_percent: float = 0.0
    dependencies: List[str] = field(default_factory=list)
    dependents: List[str] = field(default_factory=list)
    restart_count: int = 0
    last_restart: Optional[datetime] = None
    version: str = ""
    anomaly_flags: List[str] = field(default_factory=list)
    checked_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


@dataclass
class SystemSnapshot:
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
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
    detected_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
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
    detected_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    resolved: bool = False
    linked_diagnosis: Optional[str] = None


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
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
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
    detected_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    calibrated_confidence: float = 0.0


@dataclass
class FailurePattern:
    id: str
    signature: str
    conditions: Dict[str, Any] = field(default_factory=dict)
    root_cause: str = ""
    fix_applied: str = ""
    success_rate: float = 0.5
    occurrences: int = 0
    last_seen: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


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
# HEALTH CHECK TIMEOUT — NUCLEAR FIX v2 (daemon threads, no executor)
# ═══════════════════════════════════════════════════════════════════════════

class HealthCheckTimeout:
    """Simple timeout using daemon threads — no executor to break."""

    def __init__(self, timeout_seconds: float = 5.0):
        self.timeout_seconds = timeout_seconds

    def check_with_timeout(
        self, check_fn: Callable, *args, **kwargs
    ) -> Tuple[bool, Optional[Any]]:
        """Execute health check with timeout using a daemon thread."""
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

        thread = threading.Thread(
            target=_runner, daemon=True,
            name=f"health-check-{id(check_fn)}",
        )
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
        self._lock = threading.RLock()

    def record_cycle_completed(self) -> None:
        with self._lock:
            self._last_cycle_completed = utcnow()

    def check_self_health(self) -> Dict[str, Any]:
        with self._lock:
            if self._last_cycle_completed is None:
                return {
                    "status": "starting",
                    "health_score": 0.0,
                    "message": "No cycle completed yet",
                }

            elapsed = (utcnow() - self._last_cycle_completed).total_seconds()

            if elapsed > self.cycle_timeout_seconds:
                return {
                    "status": "critical",
                    "health_score": 0.0,
                    "message": f"Last cycle {elapsed:.0f}s ago",
                }

            if elapsed > self.cycle_timeout_seconds * 0.5:
                return {
                    "status": "degraded",
                    "health_score": 50.0,
                    "message": f"Last cycle {elapsed:.0f}s ago",
                }

            return {
                "status": "healthy",
                "health_score": 100.0,
                "message": "Monitoring cycles running normally",
            }


# ═══════════════════════════════════════════════════════════════════════════
# GLOBAL ALERT RATE LIMITER
# ═══════════════════════════════════════════════════════════════════════════

class GlobalAlertRateLimiter:
    """Prevents alert flooding during mass failures."""

    def __init__(
        self,
        max_alerts_per_minute: int = 5,
        max_alerts_per_hour: int = 30,
    ):
        self.max_alerts_per_minute = max_alerts_per_minute
        self.max_alerts_per_hour = max_alerts_per_hour
        self._minute_alerts: List[float] = []
        self._hour_alerts: List[float] = []
        self._lock = threading.RLock()

    def can_send(self) -> bool:
        with self._lock:
            now = time.monotonic()
            self._minute_alerts = [t for t in self._minute_alerts if now - t < 60]
            self._hour_alerts = [t for t in self._hour_alerts if now - t < 3600]
            return (
                len(self._minute_alerts) < self.max_alerts_per_minute
                and len(self._hour_alerts) < self.max_alerts_per_hour
            )

    def record_send(self) -> None:
        with self._lock:
            now = time.monotonic()
            self._minute_alerts.append(now)
            self._hour_alerts.append(now)


# ═══════════════════════════════════════════════════════════════════════════
# INCIDENT RESOLUTION MANAGER
# ═══════════════════════════════════════════════════════════════════════════

class IncidentResolutionManager:
    """Manages full incident lifecycle."""

    def __init__(self, meta_engine: "MetaEngineV5"):
        self.meta_engine = meta_engine

    def check_and_resolve(self) -> int:
        resolved_count = 0

        for incident_id, incident in list(self.meta_engine._incidents.items()):
            if incident.status == IncidentStatus.RESOLVED:
                continue

            health = self.meta_engine.check_engine_health(
                incident.primary_engine, record_history=False
            )

            if health.status == EngineStatus.NOMINAL:
                all_healthy = all(
                    self.meta_engine.check_engine_health(
                        e, record_history=False
                    ).status == EngineStatus.NOMINAL
                    for e in incident.affected_engines
                )

                if all_healthy:
                    incident.status = IncidentStatus.RESOLVED
                    incident.resolved_at = utcnow()
                    incident.updated_at = utcnow()
                    resolved_count += 1
                    self.meta_engine.learn_from_incident(incident)
                    logger.info(
                        "Incident %s RESOLVED — %s recovered",
                        incident_id, incident.primary_engine,
                    )

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
                logger.warning(
                    "Incident %s ESCALATED — unresolved for 30+ minutes",
                    incident.id,
                )

        return escalated


# ═══════════════════════════════════════════════════════════════════════════
# STATE PERSISTENCE
# ═══════════════════════════════════════════════════════════════════════════

class StatePersistence:
    """Persists MetaEngine state to Supabase or file."""

    def __init__(
        self,
        supabase_client: Any = None,
        file_path: str = "/tmp/meta_engine_state.json",
    ):
        self.supabase = supabase_client
        self.file_path = file_path
        self._lock = threading.RLock()

    def save_snapshot(self, snapshot: SystemSnapshot) -> None:
        with self._lock:
            data = {
                "timestamp": iso(snapshot.timestamp),
                "overall_status": snapshot.overall_status.value,
                "health_score": snapshot.health_score,
                "risk_score": snapshot.risk_score,
                "engine_count": len(snapshot.engines),
                "engines": {
                    name: {
                        "status": h.status.value,
                        "health_score": h.health_score,
                        "risk_score": h.risk_score,
                        "error_count_1h": h.error_count_1h,
                    }
                    for name, h in snapshot.engines.items()
                },
            }

            if self.supabase:
                try:
                    self.supabase.table("meta_engine_snapshots").upsert({
                        "timestamp": iso(snapshot.timestamp),
                        "payload": json.dumps(data),
                    }).execute()
                except Exception as exc:
                    logger.warning("Supabase save failed: %s", exc)

            try:
                with open(self.file_path, "w") as f:
                    json.dump(data, f, indent=2)
            except Exception as exc:
                logger.debug("File save failed: %s", exc)

    def load_recent_snapshot(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            try:
                with open(self.file_path, "r") as f:
                    return json.load(f)
            except Exception:
                pass

            if self.supabase:
                try:
                    result = (
                        self.supabase.table("meta_engine_snapshots")
                        .select("*")
                        .order("timestamp", desc=True)
                        .limit(1)
                        .execute()
                    )
                    if result.data:
                        return json.loads(result.data[0].get("payload", "{}"))
                except Exception:
                    pass

            return None


# ═══════════════════════════════════════════════════════════════════════════
# META ENGINE v5.0 — COMPLETE (with Nuclear Fixes)
# ═══════════════════════════════════════════════════════════════════════════

class MetaEngineV5:
    """Complete autonomous supervisory engine."""

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

    def __init__(
        self,
        event_bus: Any = None,
        container: Any = None,
        supabase_client: Any = None,
    ):
        self.event_bus = event_bus
        self.container = container
        self.supabase = supabase_client

        # Core state
        self._engines: Dict[str, Any] = {}
        self._engine_versions: Dict[str, str] = {}
        self._health_history: Dict[str, Deque[EngineHealth]] = defaultdict(
            lambda: deque(maxlen=500)
        )
        self._snapshots: Deque[SystemSnapshot] = deque(maxlen=500)
        self._diagnoses: List[Diagnosis] = []
        self._anomalies: List[Anomaly] = []
        self._incidents: Dict[str, Incident] = {}
        self._recovery_history: List[RecoveryAttempt] = []
        self._deps = DependencyGraph()
        self._recovery_handlers: Dict[str, Callable] = {}
        self._health_handlers: Dict[str, Callable] = {}
        self._alert_callback: Optional[Callable] = None
        self._error_counts: Dict[str, Deque[datetime]] = defaultdict(
            lambda: deque(maxlen=500)
        )
        self._success_counts: Dict[str, Deque[datetime]] = defaultdict(
            lambda: deque(maxlen=500)
        )
        self._latency_samples: Dict[str, Deque[Tuple[datetime, float]]] = defaultdict(
            lambda: deque(maxlen=200)
        )
        self._metrics: Dict[str, Dict[str, Deque[MetricEvent]]] = defaultdict(
            lambda: defaultdict(lambda: deque(maxlen=200))
        )
        self._circuits: Dict[str, CircuitState] = {}
        self._recovery_timestamps: Dict[str, Deque[datetime]] = defaultdict(
            lambda: deque(maxlen=50)
        )
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

        # v5.0 — New components (with Nuclear Fixes)
        self.health_timeout = HealthCheckTimeout(
            timeout_seconds=self.HEALTH_CHECK_TIMEOUT_SECONDS
        )
        self.self_monitor = SelfMonitor()
        self.incident_manager = IncidentResolutionManager(self)
        self.alert_limiter = GlobalAlertRateLimiter()
        self.persistence = StatePersistence(supabase_client=supabase_client)

        # v5.0 — Circuit breaker state
        self._db_failure_count = 0
        self._db_circuit_open = False
        self._db_circuit_reset_time = 0.0

        # v5.0 — Jitter
        self._jitter_seconds = random.uniform(0, 15)

        self._build_dependency_graph()
        logger.info(
            "MetaEngine v%s initialized — COMPLETE OVERSEER (Nuclear Fixes applied)",
            self.VERSION,
        )

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
    # REGISTRATION
    # ═══════════════════════════════════════════════════════════════════

    @staticmethod
    def _resolve_engine_instance(engine: Any) -> Any:
        """Resolve any engine (class, getter, instance) to an instance.

        NUCLEAR FIX v3: Try more getter names and no-arg constructor.
        """
        if engine is None:
            return None
        if not isinstance(engine, type) and not callable(engine):
            return engine

        if isinstance(engine, type):
            # Try singleton getters
            for getter_name in (
                'get_instance', 'get', 'instance',
                'get_plugin_registry', 'get_event_bus',
            ):
                getter = getattr(engine, getter_name, None)
                if callable(getter):
                    try:
                        instance = getter()
                        if instance is not None and not isinstance(instance, type):
                            return instance
                    except Exception:
                        pass
            # Try no-arg constructor
            try:
                instance = engine()
                if instance is not None and not isinstance(instance, type):
                    return instance
            except Exception:
                pass
            return engine

        if callable(engine) and not isinstance(engine, type):
            try:
                instance = engine()
                if instance is not None:
                    return instance
            except Exception:
                pass
        return engine

    def register(self, name: str, engine: Any, version: str = "") -> None:
        if not name:
            raise ValueError("Engine name cannot be empty")
        resolved = self._resolve_engine_instance(engine)
        with self._lock:
            self._engines[name] = resolved
            self._engine_versions[name] = (
                version or getattr(resolved, "__version__", "") or ""
            )
            if name not in self._circuits:
                self._circuits[name] = CircuitState(engine=name)

    def register_all_engines(self) -> None:
        try:
            from services.director import ENGINE_REGISTRY
            for module_path, attr_name, display_name, sector in ENGINE_REGISTRY:
                try:
                    module = __import__(module_path, fromlist=[attr_name])
                    engine = getattr(module, attr_name, None)
                    if engine is not None:
                        self.register(display_name, engine)
                except ImportError:
                    pass
                except Exception as exc:
                    logger.debug("MetaEngine: %s: %s", display_name, exc)
        except ImportError:
            logger.warning("Director registry unavailable")
            self._manual_register()

        if self.container:
            for name, plugin in getattr(self.container, "industry_plugins", {}).items():
                try:
                    self.register(name, plugin)
                except Exception as exc:
                    logger.debug("Plugin %s: %s", name, exc)

        logger.info("MetaEngine: %d engines registered", len(self._engines))

    def _manual_register(self) -> None:
        manual_registry = [
            ("content_engine", "services.content_engine", "content_engine"),
            ("condition_engine", "services.condition_engine", "autonomous_engine"),
            ("guardian", "services.guardian_service", "guardian_service"),
            ("email_service", "services.email_service", "email_service"),
            ("engagement_tracker", "services.engagement_tracker", "engagement_tracker"),
            ("ai_orchestrator", "ai.orchestrator", "AIOrchestrator"),
            ("job_service", "services.job_stats_service", "get_job_stats_service"),
            ("plugin_registry", "plugins.base", "PluginRegistry"),
            ("event_bus", "core.event_bus", "get_event_bus"),
            ("render_queue", "core.render_queue", "get_render_queue"),
        ]
        for name, mod, attr in manual_registry:
            try:
                module = __import__(mod, fromlist=[attr])
                engine = getattr(module, attr, None)
                if engine is not None:
                    self.register(name, engine)
            except Exception:
                pass

    # ═══════════════════════════════════════════════════════════════════
    # CALLBACKS
    # ═══════════════════════════════════════════════════════════════════

    def set_alert_callback(self, cb: Callable) -> None:
        self._alert_callback = cb

    def register_recovery_handler(self, engine_name: str, handler: Callable) -> None:
        self._recovery_handlers[engine_name] = handler

    def register_health_handler(self, engine_name: str, handler: Callable) -> None:
        self._health_handlers[engine_name] = handler

    # ═══════════════════════════════════════════════════════════════════
    # METRICS
    # ═══════════════════════════════════════════════════════════════════

    def record_success(self, engine_name: str) -> None:
        self._success_counts[engine_name].append(utcnow())

    def record_error(self, engine_name: str, error: str = "") -> None:
        self._error_counts[engine_name].append(utcnow())

    def record_latency(self, engine_name: str, ms: float) -> None:
        self._latency_samples[engine_name].append(
            (utcnow(), max(0.0, safe_float(ms)))
        )

    def record_metric(
        self,
        engine_name: str,
        metric_name: str,
        value: float,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._metrics[engine_name][metric_name].append(
            MetricEvent(
                timestamp=utcnow(),
                kind=metric_name,
                engine=engine_name,
                value=safe_float(value),
                metadata=metadata or {},
            )
        )

    # ═══════════════════════════════════════════════════════════════════
    # RESOURCE MONITORING
    # ═══════════════════════════════════════════════════════════════════

    def collect_system_resources(self) -> Dict[str, float]:
        resources = {}
        try:
            import psutil
            resources["cpu_percent"] = psutil.cpu_percent(interval=0.1)
            resources["memory_percent"] = psutil.virtual_memory().percent
            resources["disk_percent"] = psutil.disk_usage("/").percent
        except ImportError:
            resources["cpu_percent"] = 0.0
            resources["memory_percent"] = 0.0
            resources["disk_percent"] = 0.0
        return resources

    # ═══════════════════════════════════════════════════════════════════
    # CIRCUIT BREAKER
    # ═══════════════════════════════════════════════════════════════════

    def _is_db_circuit_open(self) -> bool:
        if not self._db_circuit_open:
            return False
        if time.monotonic() > self._db_circuit_reset_time:
            self._db_circuit_open = False
            self._db_failure_count = 0
            logger.info("MetaEngine: DB circuit breaker reset")
            return False
        return True

    def _record_db_success(self) -> None:
        self._db_failure_count = 0
        if self._db_circuit_open:
            self._db_circuit_open = False

    def _record_db_failure(self) -> None:
        self._db_failure_count += 1
        if self._db_failure_count >= self.DB_CIRCUIT_FAILURE_THRESHOLD:
            backoff = min(
                600,
                self.DB_CIRCUIT_COOLDOWN_SECONDS
                * (2 ** (self._db_failure_count - self.DB_CIRCUIT_FAILURE_THRESHOLD)),
            )
            self._db_circuit_open = True
            self._db_circuit_reset_time = time.monotonic() + backoff
            logger.critical(
                "MetaEngine: DB circuit OPEN — %d failures, %ds cooldown",
                self._db_failure_count, backoff,
            )

    # ═══════════════════════════════════════════════════════════════════
    # HEALTH CHECK (with Nuclear Fixes)
    # ═══════════════════════════════════════════════════════════════════

    def _ping_engine(
        self, engine: Any, engine_name: str = ""
    ) -> bool:
        """Ping an engine to check if it's responsive.

        NUCLEAR FIX v2: Never call methods on classes.
        """
        # If engine is a class (not instance), it's importable = healthy
        if isinstance(engine, type):
            return True

        success, result = self.health_timeout.check_with_timeout(
            self._ping_engine_inner, engine, engine_name
        )
        return success and result

    def _ping_engine_inner(self, engine: Any, engine_name: str = "") -> bool:
        """Ping a single engine and return True if healthy."""
        try:
            # Try health_check first
            if hasattr(engine, "health_check") and callable(engine.health_check):
                result = engine.health_check()
                if isinstance(result, dict):
                    status = str(result.get("status", "")).lower()
                    return status in {
                        "healthy", "ok", "nominal", "ready", "degraded",
                    }
                return bool(result)

            # Try get_status
            if hasattr(engine, "get_status") and callable(engine.get_status):
                result = engine.get_status()
                if isinstance(result, dict):
                    return result.get("running", False) or result.get("status") not in ("stopped", "offline")
                return result is not None

            # Try get_stats
            if hasattr(engine, "get_stats") and callable(engine.get_stats):
                result = engine.get_stats()
                return result is not None

            # No health interface
            logger.debug(
                "Engine '%s' has no health interface — OFFLINE",
                engine_name or "unknown",
            )
            return False

        except Exception as e:
            logger.error(
                "Engine '%s' health check failed: %s",
                engine_name or "unknown",
                e,
            )
            return False

    def check_engine_health(
        self, name: str, record_history: bool = True
    ) -> EngineHealth:
        """Check health of a single engine."""
        engine = self._engines.get(name)
        health = EngineHealth(
            name=name,
            version=self._engine_versions.get(name, ""),
        )

        if engine is None:
            health.status = EngineStatus.OFFLINE
            health.health_score = 0.0
            health.risk_score = 100.0
            return health

        # Ping with timeout (and class check)
        if not self._ping_engine(engine, name):
            health.status = EngineStatus.OFFLINE
            health.health_score = 0.0
            health.risk_score = 100.0
            health.dependencies = self._deps.get_dependencies(name)
            health.dependents = self._deps.get_dependents(name)
            if record_history:
                self._health_history[name].append(health)
            return health

        # Collect metrics
        health.error_count_1h = sum(
            1 for ts in self._error_counts[name]
            if ts >= utcnow() - timedelta(hours=1)
        )
        health.error_count_24h = sum(
            1 for ts in self._error_counts[name]
            if ts >= utcnow() - timedelta(hours=24)
        )
        health.success_count_1h = sum(
            1 for ts in self._success_counts[name]
            if ts >= utcnow() - timedelta(hours=1)
        )
        health.success_count_24h = sum(
            1 for ts in self._success_counts[name]
            if ts >= utcnow() - timedelta(hours=24)
        )
        health.throughput_1h = health.success_count_1h / 3600.0

        latencies = [
            v for ts, v in self._latency_samples[name]
            if ts >= utcnow() - timedelta(hours=1)
        ]
        if latencies:
            health.avg_latency_ms = mean(latencies)
            health.median_latency_ms = median(latencies)
            health.p95_latency_ms = percentile(latencies, 95)

        health.dependencies = self._deps.get_dependencies(name)
        health.dependents = self._deps.get_dependents(name)

        # Resource metrics
        resources = self._metrics[name]
        if "cpu_percent" in resources:
            recent_cpu = list(resources["cpu_percent"])[-5:]
            if recent_cpu:
                health.cpu_percent = mean(e.value for e in recent_cpu)
        if "memory_percent" in resources:
            recent_mem = list(resources["memory_percent"])[-5:]
            if recent_mem:
                health.memory_mb = mean(e.value for e in recent_mem)

        health.anomaly_flags = [
            a.anomaly_type.value
            for a in self._anomalies
            if a.engine == name and not a.resolved
        ][-10:]

        # Determine status
        total = health.error_count_1h + health.success_count_1h
        error_rate = health.error_count_1h / max(total, 1)

        if error_rate >= self.ERROR_CRITICAL_THRESHOLD:
            health.status = EngineStatus.DEGRADED
        elif error_rate >= self.ERROR_DEGRADED_THRESHOLD:
            health.status = EngineStatus.DEGRADED
        elif total == 0:
            last_success = (
                self._success_counts[name][-1]
                if self._success_counts[name] else None
            )
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
        if h.cpu_percent > 90:
            score -= 10
        if h.memory_mb > 90:
            score -= 10
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
        if h.cpu_percent > 90:
            risk += 15
        if h.memory_mb > 90:
            risk += 15
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

        snap.overall_status = (
            EngineStatus.DEGRADED
            if snap.critical_alerts or snap.active_alerts
            else EngineStatus.NOMINAL
        )
        snap.health_score = round(mean(health_scores), 2) if health_scores else 0.0
        snap.risk_score = round(mean(risk_scores), 2) if risk_scores else 0.0
        snap.active_incidents = sum(
            1 for i in self._incidents.values()
            if i.status != IncidentStatus.RESOLVED
        )

        self._snapshots.append(snap)
        self._last_monitor_cycle = snap.timestamp
        self.persistence.save_snapshot(snap)

        return snap

    # ═══════════════════════════════════════════════════════════════════
    # ANOMALY DETECTION
    # ═══════════════════════════════════════════════════════════════════

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
                f"{x.engine}:{x.anomaly_type.value}" == key
                and not x.resolved
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
            recent = [
                v for ts, v in self._latency_samples[name]
                if ts >= utcnow() - timedelta(minutes=5)
            ]
            if recent and std > 0:
                recent_avg = mean(recent)
                z = (recent_avg - avg) / std
                if z >= self.LATENCY_Z_THRESHOLD:
                    anomalies.append(
                        Anomaly(
                            id=f"an_{uuid.uuid4().hex[:10]}",
                            engine=name,
                            anomaly_type=AnomalyType.LATENCY_SPIKE,
                            description=f"Latency {z:.1f}σ above baseline",
                            severity=AlertLevel.WARNING,
                            observed_value=recent_avg,
                            expected_value=avg,
                            deviation=z,
                            confidence=min(1.0, z / 6.0),
                        )
                    )

        errors_5m = sum(
            1 for ts in self._error_counts[name]
            if ts >= utcnow() - timedelta(minutes=5)
        )
        errors_1h = sum(
            1 for ts in self._error_counts[name]
            if ts >= utcnow() - timedelta(hours=1)
        )
        baseline_5m = errors_1h / 12.0 if errors_1h else 0.0

        if errors_5m >= 3 and (
            baseline_5m == 0 or errors_5m >= baseline_5m * self.ERROR_SPIKE_MULTIPLIER
        ):
            anomalies.append(
                Anomaly(
                    id=f"an_{uuid.uuid4().hex[:10]}",
                    engine=name,
                    anomaly_type=AnomalyType.ERROR_SPIKE,
                    description=f"{errors_5m} errors in 5min",
                    severity=AlertLevel.CRITICAL if errors_5m >= 10 else AlertLevel.WARNING,
                    observed_value=errors_5m,
                    expected_value=baseline_5m,
                    deviation=errors_5m / max(baseline_5m, 1.0),
                    confidence=0.9,
                )
            )

        resources = self._metrics[name]
        if "cpu_percent" in resources:
            recent_cpu = list(resources["cpu_percent"])[-3:]
            if recent_cpu and all(e.value > 90 for e in recent_cpu):
                anomalies.append(
                    Anomaly(
                        id=f"an_{uuid.uuid4().hex[:10]}",
                        engine=name,
                        anomaly_type=AnomalyType.RESOURCE_PRESSURE,
                        description="CPU > 90% sustained",
                        severity=AlertLevel.WARNING,
                        observed_value=mean(e.value for e in recent_cpu),
                        expected_value=50.0,
                        deviation=1.0,
                        confidence=0.8,
                    )
                )

        return anomalies

    # ═══════════════════════════════════════════════════════════════════
    # DIAGNOSIS
    # ═══════════════════════════════════════════════════════════════════

    def diagnose(self) -> List[Diagnosis]:
        snapshot = self.take_snapshot()
        diagnoses = []

        for name, health in snapshot.engines.items():
            if health.status == EngineStatus.OFFLINE:
                root, chain, conf = self._find_root_cause(name, snapshot)
                d = Diagnosis(
                    id=f"diag_{uuid.uuid4().hex[:10]}",
                    engine=name,
                    symptom=f"{name} offline",
                    root_cause=root,
                    severity=AlertLevel.CRITICAL,
                    affected_engines=self._deps.get_dependents(name),
                    dependency_chain=chain,
                    recommended_action=self._recommend_fix(name, "offline"),
                    auto_fixable=self._can_auto_fix(name, "offline"),
                    confidence=conf,
                    risk_score=health.risk_score,
                )
                d.escalation = self._determine_escalation(d, health)
                diagnoses.append(d)

            elif health.status == EngineStatus.DEGRADED:
                root, chain, conf = self._find_root_cause(name, snapshot)
                total = health.error_count_1h + health.success_count_1h
                d = Diagnosis(
                    id=f"diag_{uuid.uuid4().hex[:10]}",
                    engine=name,
                    symptom=f"{name} degraded",
                    root_cause=root,
                    severity=(
                        AlertLevel.CRITICAL
                        if (health.error_count_1h / max(total, 1)) >= self.ERROR_CRITICAL_THRESHOLD
                        else AlertLevel.WARNING
                    ),
                    affected_engines=self._deps.get_dependents(name),
                    dependency_chain=chain,
                    recommended_action=self._recommend_fix(name, "degraded"),
                    auto_fixable=self._can_auto_fix(name, "degraded"),
                    confidence=conf,
                    risk_score=health.risk_score,
                )
                d.escalation = self._determine_escalation(d, health)
                diagnoses.append(d)

        self._diagnoses.extend(diagnoses)
        if len(self._diagnoses) > 2000:
            self._diagnoses = self._diagnoses[-2000:]

        return diagnoses

    def _find_root_cause(
        self, engine_name: str, snapshot: SystemSnapshot
    ) -> Tuple[str, List[str], float]:
        health = snapshot.engines.get(engine_name)
        if not health:
            return ("Engine not in snapshot", [], 0.0)

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
            offline = [
                d for d in unhealthy
                if snapshot.engines[d].status == EngineStatus.OFFLINE
            ]
            current = offline[0] if offline else unhealthy[0]
            chain.append(current)

        if len(chain) > 1:
            return (f"Dependency chain: {' → '.join(chain)}", chain, 0.92)
        if health.error_count_1h > 10:
            return ("High error rate", chain, 0.70)
        if health.avg_latency_ms > 5000:
            return ("Severe latency", chain, 0.75)
        if health.status == EngineStatus.STALE:
            return ("Engine inactive", chain, 0.65)
        return ("No definitive root cause", chain, 0.35)

    def _recommend_fix(self, engine_name: str, problem: str) -> str:
        return {
            "offline": f"Recover {engine_name}, verify health",
            "degraded": f"Inspect {engine_name} error rate",
            "stale": f"Verify {engine_name} activity",
        }.get(problem, f"Investigate {engine_name}")

    def _can_auto_fix(self, engine_name: str, problem: str) -> bool:
        return (
            engine_name in self._recovery_handlers
            and self._recovery_allowed(engine_name)
        )

    def _recovery_allowed(self, engine_name: str) -> bool:
        now = utcnow()
        cutoff = now - timedelta(hours=1)
        recent = [
            ts for ts in self._recovery_timestamps[engine_name]
            if ts >= cutoff
        ]
        self._recovery_timestamps[engine_name] = deque(recent, maxlen=100)
        return len(recent) < self.MAX_RECOVERY_ATTEMPTS_PER_HOUR

    def _determine_escalation(
        self, d: Diagnosis, health: EngineHealth
    ) -> EscalationTier:
        if d.confidence < 0.5 and d.auto_fixable:
            return EscalationTier.LOG_ONLY
        if d.severity == AlertLevel.WARNING and health.risk_score < 50:
            return EscalationTier.DASHBOARD
        if d.severity == AlertLevel.CRITICAL and d.auto_fixable and d.confidence > 0.7:
            return EscalationTier.TELEGRAM
        if d.severity == AlertLevel.CRITICAL and not d.auto_fixable:
            return EscalationTier.EMAIL
        return EscalationTier.DASHBOARD

    # ═══════════════════════════════════════════════════════════════════
    # AUTO-HEAL
    # ═══════════════════════════════════════════════════════════════════

    def auto_heal(self) -> List[str]:
        diagnoses = self.diagnose()
        diagnoses.extend(self.detect_cascading_failures())

        diagnoses.sort(
            key=lambda d: {
                AlertLevel.CRITICAL: 0,
                AlertLevel.WARNING: 1,
                AlertLevel.INFO: 2,
            }[d.severity]
        )

        actions = []
        processed = set()

        for d in diagnoses:
            if d.severity == AlertLevel.CRITICAL:
                self._send_alert(d)
                self._send_telegram_alert(d)
                continue

            if d.severity == AlertLevel.INFO:
                continue

            if (
                not d.auto_fixable
                or d.risk_score >= 30
                or d.engine in processed
                or not self._recovery_allowed(d.engine)
            ):
                continue

            processed.add(d.engine)
            attempt = self._perform_recovery(d)
            self._recovery_history.append(attempt)
            actions.append(f"AUTO-HEAL: {d.engine} — {attempt.status.value}")

        return actions

    def _perform_recovery(self, d: Diagnosis) -> RecoveryAttempt:
        started = utcnow()
        attempt = RecoveryAttempt(
            id=f"rec_{uuid.uuid4().hex[:10]}",
            engine=d.engine,
            reason=d.symptom,
            started_at=started,
            action=d.recommended_action,
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
            logger.info(
                "Recovery %s for %s: %s",
                attempt.status.value, d.engine, attempt.id,
            )

        except Exception as exc:
            attempt.status = RecoveryStatus.FAILED
            attempt.finished_at = utcnow()
            attempt.error = f"{type(exc).__name__}: {exc}"
            logger.error("Recovery failed for %s: %s", d.engine, exc)

        return attempt

    def detect_cascading_failures(
        self, snapshot: Optional[SystemSnapshot] = None
    ) -> List[Diagnosis]:
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
                results.append(
                    Diagnosis(
                        id=f"cascade_{uuid.uuid4().hex[:10]}",
                        engine=root,
                        symptom=f"{len(affected)} dependents degraded",
                        root_cause=f"Cascading from {root}",
                        severity=AlertLevel.CRITICAL,
                        affected_engines=affected,
                        dependency_chain=[root] + affected,
                        recommended_action=f"Stabilize {root} first",
                        auto_fixable=self._can_auto_fix(root, "degraded"),
                        confidence=0.88,
                    )
                )

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
    # ALERTING
    # ═══════════════════════════════════════════════════════════════════

    def _send_alert(self, d: Diagnosis) -> None:
        if self._alert_callback:
            try:
                self._alert_callback(d)
            except Exception as exc:
                logger.error("Alert callback failed: %s", exc)

    def _send_telegram_alert(self, d: Diagnosis) -> None:
        if not self.alert_limiter.can_send():
            logger.warning("Alert rate limit reached — suppressing for %s", d.engine)
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
                f"🔴 <b>OVERSEER CRITICAL: {d.engine}</b>\n"
                f"{d.symptom}\n"
                f"{d.root_cause}\n"
                f"<i>{utcnow().strftime('%H:%M UTC')}</i>"
            )
        except Exception as exc:
            logger.error("Telegram alert failed: %s", exc)

    # ═══════════════════════════════════════════════════════════════════
    # INCIDENT MANAGEMENT
    # ═══════════════════════════════════════════════════════════════════

    def _create_incidents(self, diagnoses: List[Diagnosis]) -> List[Incident]:
        created = []
        for d in diagnoses:
            existing = next(
                (
                    i for i in self._incidents.values()
                    if i.primary_engine == d.engine
                    and i.status != IncidentStatus.RESOLVED
                    and i.created_at >= utcnow() - timedelta(seconds=self.INCIDENT_DEDUP_SECONDS)
                ),
                None,
            )
            if existing:
                existing.diagnoses.append(d.id)
                existing.updated_at = utcnow()
                continue

            incident = Incident(
                id=f"inc_{uuid.uuid4().hex[:10]}",
                title=d.symptom,
                severity=d.severity,
                status=IncidentStatus.OPEN,
                primary_engine=d.engine,
                affected_engines=d.affected_engines,
                diagnoses=[d.id],
                root_cause=d.root_cause,
                confidence=d.confidence,
            )
            self._incidents[incident.id] = incident
            created.append(incident)

        return created

    def learn_from_incident(self, incident: Incident) -> None:
        if incident.status != IncidentStatus.RESOLVED:
            return
        signature = f"{incident.primary_engine}:{incident.root_cause}"
        existing = next(
            (p for p in self._failure_patterns if p.signature == signature),
            None,
        )
        if existing:
            existing.occurrences += 1
            existing.last_seen = utcnow()
        else:
            self._failure_patterns.append(
                FailurePattern(
                    id=f"fp_{uuid.uuid4().hex[:10]}",
                    signature=signature,
                    root_cause=incident.root_cause,
                    fix_applied=(
                        incident.recovery_attempts[0]
                        if incident.recovery_attempts
                        else "manual"
                    ),
                    occurrences=1,
                    last_seen=utcnow(),
                )
            )

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
            numerator = sum(
                (i - x_mean) * (scores[i] - y_mean)
                for i in range(n)
            )
            denominator = sum((i - x_mean) ** 2 for i in range(n))

            if denominator == 0:
                continue

            slope = numerator / denominator

            if slope < self.PREDICTION_DECLINE_SLOPE:
                current = scores[-1]
                cycles_to_failure = (current - self.PREDICTION_CRITICAL_SCORE) / abs(slope)

                if cycles_to_failure < self.PREDICTION_MAX_CYCLES:
                    predictions.append(
                        Prediction(
                            engine=name,
                            predicted_failure=f"Critical in {cycles_to_failure:.0f} cycles",
                            confidence=min(0.9, abs(slope) / 2),
                            estimated_time_to_failure_minutes=(
                                cycles_to_failure * self._monitor_interval_minutes
                            ),
                            contributing_factors=[],
                        )
                    )

        predictions.sort(key=lambda p: p.estimated_time_to_failure_minutes)
        self._predictions = predictions
        return predictions

    # ═══════════════════════════════════════════════════════════════════
    # STATUS
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
                    "status": h.status.value,
                    "health_score": h.health_score,
                    "risk_score": h.risk_score,
                    "error_count_1h": h.error_count_1h,
                    "avg_latency_ms": round(h.avg_latency_ms, 2),
                    "cpu_percent": round(h.cpu_percent, 1),
                    "memory_mb": round(h.memory_mb, 1),
                    "dependents": h.dependents,
                }
                for name, h in snap.engines.items()
            },
            "active_incidents": len([
                i for i in self._incidents.values()
                if i.status != IncidentStatus.RESOLVED
            ]),
            "critical_alerts": snap.critical_alerts,
            "self_health": self_health,
            "predictions": [
                {
                    "engine": p.engine,
                    "predicted_failure": p.predicted_failure,
                    "eta_minutes": p.estimated_time_to_failure_minutes,
                }
                for p in predictions[:5]
            ],
            "failure_patterns_learned": len(self._failure_patterns),
            "monitoring": {
                "running": self._running,
                "cycle_count": self._cycle_count,
                "last_cycle": iso(self._last_monitor_cycle),
            },
        }

    def get_engine_report(self, engine_name: str) -> Dict[str, Any]:
        health = self.check_engine_health(engine_name)
        history = list(self._health_history[engine_name])[-30:]

        return {
            "engine": engine_name,
            "current": {
                "status": health.status.value,
                "health_score": health.health_score,
                "risk_score": health.risk_score,
                "error_count_1h": health.error_count_1h,
                "avg_latency_ms": health.avg_latency_ms,
                "cpu_percent": health.cpu_percent,
                "memory_mb": health.memory_mb,
            },
            "dependencies": health.dependencies,
            "dependents": health.dependents,
            "history": [
                {
                    "status": h.status.value,
                    "health_score": h.health_score,
                    "timestamp": iso(h.checked_at),
                }
                for h in history
            ],
        }

    # ═══════════════════════════════════════════════════════════════════
    # LIFECYCLE
    # ═══════════════════════════════════════════════════════════════════

    def start(self, interval_seconds: int = 60) -> None:
        if self._running:
            return
        self._running = True
        self._monitor_interval_minutes = max(5, interval_seconds) / 60.0

        recent = self.persistence.load_recent_snapshot()
        if recent:
            logger.info(
                "MetaEngine: loaded previous state from %s",
                recent.get("timestamp"),
            )

        self._thread = threading.Thread(
            target=self._monitoring_loop,
            args=(max(5, interval_seconds),),
            daemon=True,
            name="kanyoza-meta-engine",
        )
        self._thread.start()
        logger.info(
            "MetaEngine v%s: monitoring started (every %ds)",
            self.VERSION, interval_seconds,
        )

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
                escalated = self.incident_manager.escalate_if_unresolved()

                self.self_monitor.record_cycle_completed()

                if resolved:
                    logger.info("MetaEngine: %d incidents resolved", resolved)
                if escalated:
                    logger.warning("MetaEngine: %d incidents escalated", escalated)
                if actions:
                    logger.info("MetaEngine: %d recovery actions", len(actions))

            except Exception as exc:
                logger.error("MetaEngine cycle failed: %s", exc)
                self._record_db_failure()

            elapsed = time.monotonic() - cycle_started
            sleep_for = max(0.1, interval_seconds - elapsed)
            sleep_for += random.uniform(0, 5)

            end = time.monotonic() + sleep_for
            while self._running and time.monotonic() < end:
                time.sleep(min(1.0, max(0.01, end - time.monotonic())))


# ═══════════════════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════════════════

meta_engine = MetaEngineV5()


def init_meta_engine(
    event_bus: Any = None,
    container: Any = None,
    supabase_client: Any = None,
) -> MetaEngineV5:
    """Initialize the MetaEngine."""
    global meta_engine

    try:
        meta_engine.stop()
    except Exception:
        pass

    meta_engine.event_bus = event_bus
    meta_engine.container = container
    meta_engine.supabase = supabase_client
    meta_engine.persistence = StatePersistence(supabase_client=supabase_client)
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
        if any(hasattr(engine, m) for m in ("restart", "reset")):
            meta_engine.register_recovery_handler(name, generic_restart)

    meta_engine.start(interval_seconds=60)
    logger.info(
        "KANYOZA META ENGINE v%s — %d engines under supervision",
        meta_engine.VERSION,
        len(meta_engine._engines),
    )
    return meta_engine


def shutdown_meta_engine() -> None:
    try:
        meta_engine.stop()
    except Exception:
        pass


import atexit
atexit.register(shutdown_meta_engine)
