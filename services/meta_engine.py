"""
KANYOZA META ENGINE — v2.0 "THE OVERSEER"
==========================================

Autonomous system-wide monitor, diagnostician, anomaly detector,
root-cause analyzer, dependency intelligence engine, and healer.

Designed to operate as a supervisory layer above Kanyoza's engines.

PIPELINE
--------

    OBSERVE
       ↓
    COLLECT METRICS
       ↓
    BUILD HEALTH MODEL
       ↓
    DETECT ANOMALIES
       ↓
    DETECT TRENDS
       ↓
    ANALYZE DEPENDENCIES
       ↓
    FIND ROOT CAUSE
       ↓
    CALCULATE RISK
       ↓
    GENERATE INCIDENT
       ↓
    AUTO-HEAL
       ↓
    VERIFY RECOVERY
       ↓
    ESCALATE
       ↓
    LEARN FROM HISTORY

No external dependencies beyond the Python standard library.

Compatible with the original v1 MetaEngine interface.
"""

from __future__ import annotations

import logging
import math
import threading
import time
import traceback
import uuid

from collections import defaultdict, deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from enum import Enum
from statistics import mean, median, pstdev
from typing import (
    Any,
    Callable,
    Deque,
    Dict,
    Iterable,
    List,
    Optional,
    Set,
    Tuple,
)


logger = logging.getLogger("kanyoza.meta_engine")


# ============================================================================
# ENUMS
# ============================================================================


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


# ============================================================================
# DATACLASSES
# ============================================================================


@dataclass
class EngineHealth:
    """Current health state of an engine."""

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

    checked_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


@dataclass
class SystemSnapshot:
    """Complete system state at a point in time."""

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
    """Root-cause diagnosis."""

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

    # §4: Risk score derived from the engine's health snapshot at diagnosis
    # time.  0.0 = safe; 100.0 = maximum risk.  Recovery is only attempted
    # for WARNING diagnoses with risk_score < 30.
    risk_score: float = 0.0

    detected_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    resolved_at: Optional[datetime] = None


@dataclass
class DependencyGraph:
    """Directed dependency graph."""

    edges: Dict[str, List[str]] = field(default_factory=dict)

    reverse: Dict[str, List[str]] = field(default_factory=dict)

    def add(
        self,
        engine: str,
        depends_on: Iterable[str],
    ) -> None:

        dependencies = list(dict.fromkeys(depends_on))

        self.edges[engine] = dependencies

        for dependency in dependencies:

            if dependency not in self.reverse:
                self.reverse[dependency] = []

            if engine not in self.reverse[dependency]:
                self.reverse[dependency].append(engine)

    def get_dependencies(
        self,
        engine: str,
    ) -> List[str]:

        return list(self.edges.get(engine, []))

    def get_dependents(
        self,
        engine: str,
    ) -> List[str]:

        return list(self.reverse.get(engine, []))

    def nodes(self) -> Set[str]:

        result = set(self.edges.keys())

        for dependencies in self.edges.values():
            result.update(dependencies)

        result.update(self.reverse.keys())

        return result


@dataclass
class MetricEvent:
    """Single metric event."""

    timestamp: datetime

    kind: str

    engine: str

    value: float = 0.0

    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Anomaly:
    """Detected statistical/system anomaly."""

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


@dataclass
class RecoveryAttempt:
    """Record of an auto-healing attempt."""

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
    """System incident."""

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
    """Circuit breaker state for an engine."""

    engine: str

    failures: int = 0

    state: str = "closed"

    opened_at: Optional[datetime] = None

    half_open_at: Optional[datetime] = None

    cooldown_seconds: int = 60


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: Optional[datetime]) -> Optional[str]:

    if value is None:
        return None

    return value.isoformat()


def clamp(
    value: float,
    minimum: float = 0.0,
    maximum: float = 100.0,
) -> float:

    return max(minimum, min(maximum, value))


def percentile(
    values: Iterable[float],
    p: float,
) -> float:

    data = sorted(float(v) for v in values)

    if not data:
        return 0.0

    if len(data) == 1:
        return data[0]

    p = clamp(p, 0.0, 100.0)

    index = (len(data) - 1) * (p / 100.0)

    lower = int(math.floor(index))
    upper = int(math.ceil(index))

    if lower == upper:
        return data[lower]

    weight = index - lower

    return data[lower] + (
        data[upper] - data[lower]
    ) * weight


def safe_float(
    value: Any,
    default: float = 0.0,
) -> float:

    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ============================================================================
# META ENGINE
# ============================================================================


class MetaEngine:
    """
    Kanyoza system-wide supervisory engine.

    The Meta Engine does not replace individual engines.

    Instead, it watches them and builds a higher-level model of:

        health
        performance
        dependencies
        failures
        anomalies
        incidents
        recovery
        risk
        trends

    Example:

        meta = MetaEngine(
            event_bus=event_bus,
            container=container,
        )

        meta.register_all_engines()

        meta.start()

    """

    # ------------------------------------------------------------------------
    # CONFIGURATION
    # ------------------------------------------------------------------------

    ERROR_DEGRADED_THRESHOLD = 0.10
    ERROR_CRITICAL_THRESHOLD = 0.50

    STALE_AFTER_SECONDS = 3600

    LATENCY_Z_THRESHOLD = 3.0

    ERROR_SPIKE_MULTIPLIER = 3.0

    MIN_SAMPLES_FOR_ANOMALY = 5

    RECOVERY_COOLDOWN_SECONDS = 300

    MAX_RECOVERY_ATTEMPTS_PER_HOUR = 3

    INCIDENT_DEDUP_SECONDS = 300

    # ------------------------------------------------------------------------
    # INIT
    # ------------------------------------------------------------------------

    def __init__(
        self,
        event_bus: Any = None,
        container: Any = None,
    ):

        self.event_bus = event_bus

        self.container = container

        # Registered engines.
        self._engines: Dict[str, Any] = {}

        self._engine_versions: Dict[str, str] = {}

        # Historical health.
        self._health_history: Dict[
            str,
            Deque[EngineHealth],
        ] = defaultdict(
            lambda: deque(maxlen=500)
        )

        # Snapshots.
        self._snapshots: Deque[
            SystemSnapshot
        ] = deque(maxlen=500)

        # Diagnoses.
        self._diagnoses: List[Diagnosis] = []

        # Anomalies.
        self._anomalies: List[Anomaly] = []

        # Incidents.
        self._incidents: Dict[str, Incident] = {}

        # Recovery history.
        self._recovery_history: List[
            RecoveryAttempt
        ] = []

        # Dependency graph.
        self._deps = DependencyGraph()

        # Recovery callbacks.
        self._recovery_handlers: Dict[
            str,
            Callable,
        ] = {}

        # Optional custom health handlers.
        self._health_handlers: Dict[
            str,
            Callable,
        ] = {}

        # Alert callback.
        self._alert_callback: Optional[
            Callable
        ] = None

        # Metric timestamps.
        self._error_counts: Dict[
            str,
            Deque[datetime],
        ] = defaultdict(
            lambda: deque(maxlen=5000)
        )

        self._success_counts: Dict[
            str,
            Deque[datetime],
        ] = defaultdict(
            lambda: deque(maxlen=5000)
        )

        # Latency samples are timestamped.
        self._latency_samples: Dict[
            str,
            Deque[Tuple[datetime, float]],
        ] = defaultdict(
            lambda: deque(maxlen=2000)
        )

        # Generic metrics.
        self._metrics: Dict[
            str,
            Dict[str, Deque[MetricEvent]],
        ] = defaultdict(
            lambda: defaultdict(
                lambda: deque(maxlen=2000)
            )
        )

        # Circuit breakers.
        self._circuits: Dict[
            str,
            CircuitState,
        ] = {}

        # Recovery timestamps.
        self._recovery_timestamps: Dict[
            str,
            Deque[datetime],
        ] = defaultdict(
            lambda: deque(maxlen=100)
        )

        # Thread state.
        self._running = False

        self._thread: Optional[
            threading.Thread
        ] = None

        self._lock = threading.RLock()

        self._start_time = utcnow()

        self._last_monitor_cycle: Optional[
            datetime
        ] = None

        self._cycle_count = 0

        # Build graph.
        self._build_dependency_graph()

        logger.info(
            "MetaEngine v2 initialized — "
            "autonomous supervisory layer ready"
        )

    # ========================================================================
    # DEPENDENCY GRAPH
    # ========================================================================

    def _build_dependency_graph(self) -> None:

        self._deps.add(
            "content_engine",
            [
                "ai_orchestrator",
                "prompt_builder",
                "topic_selector",
            ],
        )

        self._deps.add(
            "card_renderer",
            [],
        )

        self._deps.add(
            "condition_engine",
            [
                "event_bus",
                "engagement_tracker",
            ],
        )

        self._deps.add(
            "engagement_tracker",
            [
                "supabase",
                "facebook_plugin",
            ],
        )

        self._deps.add(
            "email_service",
            [],
        )

        self._deps.add(
            "guardian",
            [
                "github",
            ],
        )

        self._deps.add(
            "ai_orchestrator",
            [
                "gemini",
            ],
        )

        self._deps.add(
            "scheduler",
            [
                "job_service",
                "content_engine",
            ],
        )

        self._deps.add(
            "job_service",
            [
                "supabase",
            ],
        )

        self._deps.add(
            "health_monitor",
            [],
        )

        self._deps.add(
            "plugin_registry",
            [
                "facebook_plugin",
                "twitter_plugin",
            ],
        )

        self._deps.add(
            "event_bus",
            [],
        )

        self._deps.add(
            "render_queue",
            [
                "card_renderer",
                "supabase",
            ],
        )

        self._deps.add(
            "image_engine",
            [],
        )

        self._deps.add(
            "topic_selector",
            [
                "supabase",
            ],
        )

        self._deps.add(
            "prompt_builder",
            [],
        )

        self._deps.add(
            "meta_engine",
            [],
        )

    # ========================================================================
    # REGISTRATION
    # ========================================================================

    def register(
        self,
        name: str,
        engine: Any,
        version: str = "",
    ) -> None:

        if not name:
            raise ValueError(
                "Engine name cannot be empty"
            )

        with self._lock:

            self._engines[name] = engine

            self._engine_versions[name] = (
                version or
                getattr(engine, "__version__", "") or
                ""
            )

            if name not in self._circuits:
                self._circuits[name] = CircuitState(
                    engine=name
                )

        logger.info(
            "MetaEngine: registered %s (v%s)",
            name,
            version or "?",
        )

    def unregister(
        self,
        name: str,
    ) -> None:

        with self._lock:
            self._engines.pop(name, None)
            self._engine_versions.pop(name, None)

        logger.info(
            "MetaEngine: unregistered %s",
            name,
        )

    def register_all_engines(self) -> None:

        engines_to_try = [
            (
                "content_engine",
                "services.content_engine",
                "content_engine",
            ),
            (
                "card_renderer",
                "services.card_renderer",
                "card_renderer",
            ),
            (
                "condition_engine",
                "services.condition_engine",
                "autonomous_engine",
            ),
            (
                "guardian",
                "services.guardian_service",
                "guardian_service",
            ),
            (
                "email_service",
                "services.email_service",
                "email_service",
            ),
            (
                "engagement_tracker",
                "services.engagement_tracker",
                "engagement_tracker",
            ),
            (
                "health_monitor",
                "services.health_monitor_service",
                "health_monitor_service",
            ),
        ]

        for (
            name,
            module_path,
            attr_name,
        ) in engines_to_try:

            try:

                module = __import__(
                    module_path,
                    fromlist=[attr_name],
                )

                engine = getattr(
                    module,
                    attr_name,
                    None,
                )

                if engine is not None:
                    self.register(
                        name,
                        engine,
                        getattr(
                            engine,
                            "__version__",
                            "",
                        ),
                    )

            except ImportError:

                logger.debug(
                    "MetaEngine: %s not available",
                    name,
                )

            except Exception as exc:

                logger.debug(
                    "MetaEngine: %s import failed: %s",
                    name,
                    exc,
                )

        logger.info(
            "MetaEngine: %d engines registered",
            len(self._engines),
        )

    # ========================================================================
    # CALLBACKS
    # ========================================================================

    def set_alert_callback(
        self,
        callback: Callable,
    ) -> None:

        self._alert_callback = callback

    def register_recovery_handler(
        self,
        engine_name: str,
        handler: Callable,
    ) -> None:

        self._recovery_handlers[
            engine_name
        ] = handler

    def register_health_handler(
        self,
        engine_name: str,
        handler: Callable,
    ) -> None:

        self._health_handlers[
            engine_name
        ] = handler

    # ========================================================================
    # EVENT BUS
    # ========================================================================

    def _emit(
        self,
        event_name: str,
        payload: Dict[str, Any],
    ) -> None:

        if not self.event_bus:
            return

        try:

            if hasattr(
                self.event_bus,
                "emit",
            ):
                self.event_bus.emit(
                    event_name,
                    payload,
                )

            elif hasattr(
                self.event_bus,
                "publish",
            ):
                self.event_bus.publish(
                    event_name,
                    payload,
                )

        except Exception as exc:

            logger.debug(
                "MetaEngine event emission failed: %s",
                exc,
            )

    # ========================================================================
    # METRICS
    # ========================================================================

    def record_success(
        self,
        engine_name: str,
    ) -> None:

        now = utcnow()

        self._success_counts[
            engine_name
        ].append(now)

        self._record_metric(
            engine_name,
            "success",
            1.0,
        )

    def record_error(
        self,
        engine_name: str,
        error: str = "",
    ) -> None:

        now = utcnow()

        self._error_counts[
            engine_name
        ].append(now)

        self._record_metric(
            engine_name,
            "error",
            1.0,
            {
                "error": error,
            },
        )

        logger.debug(
            "MetaEngine: error in %s: %s",
            engine_name,
            error,
        )

    def record_latency(
        self,
        engine_name: str,
        ms: float,
    ) -> None:

        ms = max(0.0, safe_float(ms))

        now = utcnow()

        self._latency_samples[
            engine_name
        ].append(
            (
                now,
                ms,
            )
        )

        self._record_metric(
            engine_name,
            "latency_ms",
            ms,
        )

    def record_metric(
        self,
        engine_name: str,
        metric_name: str,
        value: float,
        metadata: Optional[
            Dict[str, Any]
        ] = None,
    ) -> None:

        self._record_metric(
            engine_name,
            metric_name,
            value,
            metadata,
        )

    def _record_metric(
        self,
        engine_name: str,
        metric_name: str,
        value: float,
        metadata: Optional[
            Dict[str, Any]
        ] = None,
    ) -> None:

        event = MetricEvent(
            timestamp=utcnow(),
            kind=metric_name,
            engine=engine_name,
            value=safe_float(value),
            metadata=metadata or {},
        )

        self._metrics[
            engine_name
        ][metric_name].append(event)

    # ========================================================================
    # RECENT COUNTS
    # ========================================================================

    def _count_recent(
        self,
        counts: Deque[datetime],
        seconds: float,
    ) -> int:

        cutoff = utcnow() - timedelta(
            seconds=seconds
        )

        return sum(
            1
            for ts in counts
            if ts >= cutoff
        )

    def _latencies_recent(
        self,
        engine_name: str,
        seconds: float,
    ) -> List[float]:

        cutoff = utcnow() - timedelta(
            seconds=seconds
        )

        return [
            value
            for timestamp, value
            in self._latency_samples[
                engine_name
            ]
            if timestamp >= cutoff
        ]

    # ========================================================================
    # ENGINE PING
    # ========================================================================

    def _ping_engine(
        self,
        engine: Any,
    ) -> bool:

        try:

            if hasattr(
                engine,
                "health_check",
            ):

                result = engine.health_check()

                if isinstance(
                    result,
                    dict,
                ):

                    status = str(
                        result.get(
                            "status",
                            "",
                        )
                    ).lower()

                    return status in {
                        "healthy",
                        "ok",
                        "nominal",
                        "ready",
                    }

                return bool(result)

            if hasattr(
                engine,
                "get_status",
            ):

                result = engine.get_status()

                return result is not None

            return True

        except Exception:

            return False

    # ========================================================================
    # LAST ACTIVITY
    # ========================================================================

    def _get_last_activity(
        self,
        name: str,
    ) -> Optional[datetime]:

        timestamps: List[
            datetime
        ] = []

        timestamps.extend(
            self._success_counts[name]
        )

        timestamps.extend(
            self._error_counts[name]
        )

        for (
            timestamp,
            _,
        ) in self._latency_samples[name]:

            timestamps.append(timestamp)

        if not timestamps:
            return None

        return max(timestamps)

    # ========================================================================
    # HEALTH SCORE
    # ========================================================================

    def _calculate_health_score(
        self,
        health: EngineHealth,
    ) -> float:

        score = 100.0

        if health.status == EngineStatus.OFFLINE:
            return 0.0

        if health.status == EngineStatus.STALE:
            score -= 35

        if health.status == EngineStatus.DEGRADED:
            score -= 25

        total = (
            health.error_count_1h +
            health.success_count_1h
        )

        if total:

            error_rate = (
                health.error_count_1h /
                total
            )

            score -= error_rate * 50

        if health.avg_latency_ms > 1000:

            score -= min(
                25,
                (
                    health.avg_latency_ms -
                    1000
                ) / 200,
            )

        if health.p95_latency_ms > 3000:

            score -= 10

        if health.anomaly_flags:

            score -= min(
                20,
                len(
                    health.anomaly_flags
                ) * 5,
            )

        return round(
            clamp(score),
            2,
        )

    # ========================================================================
    # RISK SCORE
    # ========================================================================

    def _calculate_risk_score(
        self,
        health: EngineHealth,
    ) -> float:

        risk = 0.0

        if health.status == EngineStatus.OFFLINE:
            risk += 80

        elif health.status == EngineStatus.DEGRADED:
            risk += 40

        elif health.status == EngineStatus.STALE:
            risk += 30

        total = (
            health.error_count_1h +
            health.success_count_1h
        )

        if total:

            error_rate = (
                health.error_count_1h /
                total
            )

            risk += error_rate * 40

        if health.p95_latency_ms > 2000:
            risk += 10

        if health.p95_latency_ms > 5000:
            risk += 10

        risk += min(
            30,
            len(
                health.anomaly_flags
            ) * 7,
        )

        # Dependency pressure.
        for dependency in health.dependencies:

            dep_health = self.check_engine_health(
                dependency,
                record_history=False,
            )

            if dep_health.status == EngineStatus.OFFLINE:
                risk += 20

            elif dep_health.status == EngineStatus.DEGRADED:
                risk += 10

        return round(
            clamp(risk),
            2,
        )

    # ========================================================================
    # HEALTH CHECK
    # ========================================================================

    def check_engine_health(
        self,
        name: str,
        record_history: bool = True,
    ) -> EngineHealth:

        with self._lock:

            engine = self._engines.get(name)

            health = EngineHealth(
                name=name,
                version=self._engine_versions.get(
                    name,
                    "",
                ),
            )

            if engine is None:

                health.status = EngineStatus.OFFLINE

                health.health_score = 0.0

                health.risk_score = 100.0

                return health

            # Custom health handler.
            custom_handler = (
                self._health_handlers.get(name)
            )

            if custom_handler:

                try:

                    custom = custom_handler(
                        engine
                    )

                    if isinstance(
                        custom,
                        EngineHealth,
                    ):
                        health = custom

                    elif isinstance(
                        custom,
                        dict,
                    ):

                        for key, value in custom.items():

                            if hasattr(
                                health,
                                key,
                            ):
                                setattr(
                                    health,
                                    key,
                                    value,
                                )

                except Exception as exc:

                    logger.debug(
                        "Custom health handler failed "
                        "for %s: %s",
                        name,
                        exc,
                    )

            alive = self._ping_engine(
                engine
            )

            if not alive:

                health.status = (
                    EngineStatus.OFFLINE
                )

                health.health_score = 0.0

                health.risk_score = 100.0

                health.dependencies = (
                    self._deps.get_dependencies(
                        name
                    )
                )

                health.dependents = (
                    self._deps.get_dependents(
                        name
                    )
                )

                if record_history:
                    self._health_history[
                        name
                    ].append(health)

                return health

            # Metrics.
            health.error_count_1h = (
                self._count_recent(
                    self._error_counts[name],
                    3600,
                )
            )

            health.error_count_24h = (
                self._count_recent(
                    self._error_counts[name],
                    86400,
                )
            )

            health.success_count_1h = (
                self._count_recent(
                    self._success_counts[name],
                    3600,
                )
            )

            health.success_count_24h = (
                self._count_recent(
                    self._success_counts[name],
                    86400,
                )
            )

            health.throughput_1h = (
                health.success_count_1h /
                3600.0
            )

            health.throughput_5m = (
                self._count_recent(
                    self._success_counts[name],
                    300,
                ) /
                300.0
            )

            latencies = (
                self._latencies_recent(
                    name,
                    3600,
                )
            )

            if latencies:

                health.avg_latency_ms = (
                    mean(latencies)
                )

                health.median_latency_ms = (
                    median(latencies)
                )

                health.p95_latency_ms = (
                    percentile(
                        latencies,
                        95,
                    )
                )

            health.dependencies = (
                self._deps.get_dependencies(
                    name
                )
            )

            health.dependents = (
                self._deps.get_dependents(
                    name
                )
            )

            health.restart_count = (
                self._get_restart_count(name)
            )

            health.last_restart = (
                self._get_last_restart(name)
            )

            health.last_active = (
                self._get_last_activity(name)
            )

            total = (
                health.error_count_1h +
                health.success_count_1h
            )

            error_rate = (
                health.error_count_1h /
                max(total, 1)
            )

            # Determine status.
            if (
                error_rate >=
                self.ERROR_CRITICAL_THRESHOLD
            ):

                health.status = (
                    EngineStatus.DEGRADED
                )

            elif (
                error_rate >=
                self.ERROR_DEGRADED_THRESHOLD
            ):

                health.status = (
                    EngineStatus.DEGRADED
                )

            elif (
                health.success_count_1h == 0 and
                health.error_count_1h == 0
            ):

                last = health.last_active

                if (
                    last and
                    (
                        utcnow() - last
                    ).total_seconds()
                    > self.STALE_AFTER_SECONDS
                ):

                    health.status = (
                        EngineStatus.STALE
                    )

                else:

                    health.status = (
                        EngineStatus.NOMINAL
                    )

            else:

                health.status = (
                    EngineStatus.NOMINAL
                )

            # Anomaly flags.
            health.anomaly_flags = [
                anomaly.anomaly_type.value
                for anomaly in self._anomalies
                if (
                    anomaly.engine == name and
                    not anomaly.resolved
                )
            ][-10:]

            health.health_score = (
                self._calculate_health_score(
                    health
                )
            )

            # Avoid recursive dependency risk calculation.
            health.risk_score = 0.0

            if record_history:

                self._health_history[
                    name
                ].append(health)

            return health

    # ========================================================================
    # RESTART HISTORY
    # ========================================================================

    def _get_restart_count(
        self,
        engine_name: str,
    ) -> int:

        return sum(
            1
            for attempt in self._recovery_history
            if (
                attempt.engine ==
                engine_name and
                attempt.status in (
                    RecoveryStatus.SUCCESS,
                    RecoveryStatus.VERIFIED,
                )
            )
        )

    def _get_last_restart(
        self,
        engine_name: str,
    ) -> Optional[datetime]:

        timestamps = [
            attempt.finished_at
            for attempt in self._recovery_history
            if (
                attempt.engine ==
                engine_name and
                attempt.status in (
                    RecoveryStatus.SUCCESS,
                    RecoveryStatus.VERIFIED,
                ) and
                attempt.finished_at
            )
        ]

        return max(timestamps) if timestamps else None

    # ========================================================================
    # ANOMALY DETECTION
    # ========================================================================

    def _detect_anomalies_for_engine(
        self,
        name: str,
    ) -> List[Anomaly]:

        anomalies: List[
            Anomaly
        ] = []

        # --------------------------------------------------------------
        # LATENCY
        # --------------------------------------------------------------

        latency_events = list(
            self._latency_samples[name]
        )

        latency_values = [
            value
            for _, value
            in latency_events
        ]

        if len(latency_values) >= (
            self.MIN_SAMPLES_FOR_ANOMALY
        ):

            avg = mean(
                latency_values
            )

            std = pstdev(
                latency_values
            )

            recent = (
                self._latencies_recent(
                    name,
                    300,
                )
            )

            if (
                recent and
                std > 0
            ):

                recent_avg = mean(
                    recent
                )

                z = (
                    recent_avg - avg
                ) / std

                if z >= self.LATENCY_Z_THRESHOLD:

                    anomalies.append(
                        Anomaly(
                            id=(
                                "anomaly_" +
                                uuid.uuid4().hex[:12]
                            ),
                            engine=name,
                            anomaly_type=(
                                AnomalyType.LATENCY_SPIKE
                            ),
                            description=(
                                "Recent latency is "
                                f"{z:.1f} standard deviations "
                                "above baseline"
                            ),
                            severity=(
                                AlertLevel.WARNING
                            ),
                            observed_value=(
                                recent_avg
                            ),
                            expected_value=avg,
                            deviation=z,
                            confidence=min(
                                1.0,
                                z / 6.0,
                            ),
                        )
                    )

        # --------------------------------------------------------------
        # ERROR SPIKE
        # --------------------------------------------------------------

        errors_5m = self._count_recent(
            self._error_counts[name],
            300,
        )

        errors_1h = self._count_recent(
            self._error_counts[name],
            3600,
        )

        baseline_5m = (
            errors_1h / 12.0
            if errors_1h
            else 0.0
        )

        if (
            errors_5m >= 3 and
            (
                baseline_5m == 0 or
                errors_5m >= (
                    baseline_5m *
                    self.ERROR_SPIKE_MULTIPLIER
                )
            )
        ):

            anomalies.append(
                Anomaly(
                    id=(
                        "anomaly_" +
                        uuid.uuid4().hex[:12]
                    ),
                    engine=name,
                    anomaly_type=(
                        AnomalyType.ERROR_SPIKE
                    ),
                    description=(
                        f"{errors_5m} errors "
                        "detected in the last 5 minutes"
                    ),
                    severity=(
                        AlertLevel.CRITICAL
                        if errors_5m >= 10
                        else AlertLevel.WARNING
                    ),
                    observed_value=errors_5m,
                    expected_value=baseline_5m,
                    deviation=(
                        errors_5m /
                        max(
                            baseline_5m,
                            1.0,
                        )
                    ),
                    confidence=0.9,
                )
            )

        # --------------------------------------------------------------
        # THROUGHPUT DROP
        # --------------------------------------------------------------

        success_5m = self._count_recent(
            self._success_counts[name],
            300,
        )

        success_1h = self._count_recent(
            self._success_counts[name],
            3600,
        )

        if success_1h >= 10:

            expected_5m = (
                success_1h / 12.0
            )

            if (
                expected_5m > 0 and
                success_5m <
                expected_5m * 0.30
            ):

                anomalies.append(
                    Anomaly(
                        id=(
                            "anomaly_" +
                            uuid.uuid4().hex[:12]
                        ),
                        engine=name,
                        anomaly_type=(
                            AnomalyType.THROUGHPUT_DROP
                        ),
                        description=(
                            "Throughput dropped "
                            "significantly below baseline"
                        ),
                        severity=(
                            AlertLevel.WARNING
                        ),
                        observed_value=(
                            success_5m
                        ),
                        expected_value=(
                            expected_5m
                        ),
                        deviation=(
                            1 -
                            (
                                success_5m /
                                expected_5m
                            )
                        ),
                        confidence=0.85,
                    )
                )

        return anomalies

    def detect_anomalies(self) -> List[Anomaly]:

        detected: List[
            Anomaly
        ] = []

        for name in list(
            self._engines.keys()
        ):

            try:

                anomalies = (
                    self._detect_anomalies_for_engine(
                        name
                    )
                )

                detected.extend(
                    anomalies
                )

            except Exception as exc:

                logger.debug(
                    "Anomaly detection failed "
                    "for %s: %s",
                    name,
                    exc,
                )

        # Dependency anomalies.
        detected.extend(
            self._detect_dependency_anomalies()
        )

        # §7: Deduplicate anomalies by fingerprint before storing.
        # Skip any anomaly whose (engine, anomaly_type) pair already exists
        # in self._anomalies as an unresolved entry within the last 5 minutes.
        from datetime import timedelta

        def _utcnow():
            return datetime.now(timezone.utc)

        deduplicated: list = []
        for anomaly in detected:
            fingerprint = (
                f"{anomaly.engine}:{anomaly.anomaly_type.value}"
            )
            recent_duplicate = any(
                f"{a.engine}:{a.anomaly_type.value}" == fingerprint
                and not getattr(a, "resolved", False)
                and (_utcnow() - a.detected_at).total_seconds() < 300
                for a in self._anomalies
            )
            if not recent_duplicate:
                deduplicated.append(anomaly)

        self._anomalies.extend(
            deduplicated
        )

        # Keep memory bounded.
        if len(self._anomalies) > 2000:

            self._anomalies = (
                self._anomalies[-2000:]
            )

        for anomaly in deduplicated:

            self._emit(
                "meta_engine_anomaly",
                self._serialize_anomaly(
                    anomaly
                ),
            )

        return deduplicated

    def _detect_dependency_anomalies(
        self,
    ) -> List[Anomaly]:

        anomalies = []

        for engine_name in self._engines:

            dependencies = (
                self._deps.get_dependencies(
                    engine_name
                )
            )

            failed = []

            for dependency in dependencies:

                if dependency not in self._engines:
                    continue

                health = (
                    self.check_engine_health(
                        dependency,
                        record_history=False,
                    )
                )

                if health.status in (
                    EngineStatus.OFFLINE,
                    EngineStatus.DEGRADED,
                ):

                    failed.append(
                        dependency
                    )

            if failed:

                severity = (
                    AlertLevel.CRITICAL
                    if any(
                        self.check_engine_health(
                            dep,
                            record_history=False,
                        ).status ==
                        EngineStatus.OFFLINE
                        for dep in failed
                    )
                    else AlertLevel.WARNING
                )

                anomalies.append(
                    Anomaly(
                        id=(
                            "anomaly_" +
                            uuid.uuid4().hex[:12]
                        ),
                        engine=engine_name,
                        anomaly_type=(
                            AnomalyType.DEPENDENCY_FAILURE
                        ),
                        description=(
                            "Dependencies unhealthy: " +
                            ", ".join(failed)
                        ),
                        severity=severity,
                        confidence=0.95,
                    )
                )

        return anomalies

    # ========================================================================
    # ROOT CAUSE ANALYSIS
    # ========================================================================

    def _find_root_cause(
        self,
        engine_name: str,
        snapshot: SystemSnapshot,
    ) -> Tuple[
        str,
        List[str],
        float,
    ]:

        health = snapshot.engines.get(
            engine_name
        )

        if not health:

            return (
                "Engine not present in snapshot",
                [],
                0.0,
            )

        chain: List[str] = [
            engine_name
        ]

        visited: Set[str] = set()

        current = engine_name

        while current not in visited:

            visited.add(current)

            dependencies = (
                self._deps.get_dependencies(
                    current
                )
            )

            unhealthy = []

            for dependency in dependencies:

                dep_health = (
                    snapshot.engines.get(
                        dependency
                    )
                )

                if dep_health and dep_health.status in (
                    EngineStatus.OFFLINE,
                    EngineStatus.DEGRADED,
                ):

                    unhealthy.append(
                        dependency
                    )

            if not unhealthy:
                break

            # Prefer offline dependencies.
            offline = [
                dep
                for dep in unhealthy
                if (
                    snapshot.engines.get(
                        dep
                    ).status ==
                    EngineStatus.OFFLINE
                )
            ]

            current = (
                offline[0]
                if offline
                else unhealthy[0]
            )

            chain.append(
                current
            )

        if len(chain) > 1:

            root = chain[-1]

            return (
                f"Dependency chain failure: "
                f"{' → '.join(chain)}",
                chain,
                0.92,
            )

        if health.error_count_1h > 10:

            return (
                "High error rate suggests "
                "internal failure, resource pressure, "
                "or external provider instability",
                chain,
                0.70,
            )

        if health.avg_latency_ms > 5000:

            return (
                "Severe latency degradation suggests "
                "provider, database, network, or "
                "resource contention",
                chain,
                0.75,
            )

        if health.status == EngineStatus.STALE:

            return (
                "Engine appears inactive; "
                "possible scheduler interruption "
                "or stalled worker",
                chain,
                0.65,
            )

        return (
            "No definitive root cause detected",
            chain,
            0.35,
        )

    # ========================================================================
    # DIAGNOSIS
    # ========================================================================

    def diagnose(self) -> List[Diagnosis]:

        snapshot = self.take_snapshot()

        diagnoses: List[
            Diagnosis
        ] = []

        anomalies = (
            self.detect_anomalies()
        )

        for name, health in (
            snapshot.engines.items()
        ):

            if health.status == (
                EngineStatus.OFFLINE
            ):

                (
                    root,
                    chain,
                    confidence,
                ) = self._find_root_cause(
                    name,
                    snapshot,
                )

                diagnoses.append(
                    Diagnosis(
                        id=(
                            "diag_" +
                            uuid.uuid4().hex[:12]
                        ),
                        engine=name,
                        symptom=(
                            f"{name} is offline"
                        ),
                        root_cause=root,
                        severity=(
                            AlertLevel.CRITICAL
                        ),
                        affected_engines=(
                            self._deps.get_dependents(
                                name
                            )
                        ),
                        dependency_chain=chain,
                        recommended_action=(
                            self._recommend_fix(
                                name,
                                "offline",
                            )
                        ),
                        auto_fixable=(
                            self._can_auto_fix(
                                name,
                                "offline",
                            )
                        ),
                        confidence=confidence,
                        risk_score=health.risk_score,
                    )
                )

            elif health.status == (
                EngineStatus.DEGRADED
            ):

                total = (
                    health.error_count_1h +
                    health.success_count_1h
                )

                error_rate = (
                    health.error_count_1h /
                    max(total, 1)
                )

                (
                    root,
                    chain,
                    confidence,
                ) = self._find_root_cause(
                    name,
                    snapshot,
                )

                diagnoses.append(
                    Diagnosis(
                        id=(
                            "diag_" +
                            uuid.uuid4().hex[:12]
                        ),
                        engine=name,
                        symptom=(
                            f"{name} degraded "
                            f"({error_rate:.0%} error rate)"
                        ),
                        root_cause=root,
                        severity=(
                            AlertLevel.CRITICAL
                            if error_rate >=
                            self.ERROR_CRITICAL_THRESHOLD
                            else AlertLevel.WARNING
                        ),
                        affected_engines=(
                            self._deps.get_dependents(
                                name
                            )
                        ),
                        dependency_chain=chain,
                        recommended_action=(
                            self._recommend_fix(
                                name,
                                "degraded",
                            )
                        ),
                        auto_fixable=(
                            self._can_auto_fix(
                                name,
                                "degraded",
                            )
                        ),
                        confidence=confidence,
                        risk_score=health.risk_score,
                    )
                )

            elif health.status == (
                EngineStatus.STALE
            ):

                diagnoses.append(
                    Diagnosis(
                        id=(
                            "diag_" +
                            uuid.uuid4().hex[:12]
                        ),
                        engine=name,
                        symptom=(
                            f"{name} inactive "
                            f"for more than "
                            f"{self.STALE_AFTER_SECONDS // 60} "
                            "minutes"
                        ),
                        root_cause=(
                            "Possible scheduler interruption, "
                            "worker stall, or expected inactivity"
                        ),
                        severity=(
                            AlertLevel.WARNING
                        ),
                        affected_engines=(
                            self._deps.get_dependents(
                                name
                            )
                        ),
                        recommended_action=(
                            self._recommend_fix(
                                name,
                                "stale",
                            )
                        ),
                        auto_fixable=(
                            self._can_auto_fix(
                                name,
                                "stale",
                            )
                        ),
                        confidence=0.60,
                        risk_score=health.risk_score,
                    )
                )

        # Convert anomalies into diagnoses.
        for anomaly in anomalies:

            if anomaly.anomaly_type in (
                AnomalyType.ERROR_SPIKE,
                AnomalyType.LATENCY_SPIKE,
                AnomalyType.THROUGHPUT_DROP,
                AnomalyType.CASCADING_FAILURE,
            ):
                # Derive risk_score from the engine's health snapshot so the
                # recovery gate in auto_heal() has a reliable value to check.
                _anomaly_health = snapshot.engines.get(
                    anomaly.engine
                )
                _anomaly_risk = (
                    _anomaly_health.risk_score
                    if _anomaly_health is not None
                    else anomaly.risk_score
                )

                diagnoses.append(
                    Diagnosis(
                        id=(
                            "diag_anomaly_" +
                            uuid.uuid4().hex[:12]
                        ),
                        engine=anomaly.engine,
                        symptom=anomaly.description,
                        root_cause=(
                            "Statistical anomaly "
                            "detected against recent baseline"
                        ),
                        severity=anomaly.severity,
                        affected_engines=(
                            self._deps.get_dependents(
                                anomaly.engine
                            )
                        ),
                        recommended_action=(
                            f"Investigate {anomaly.engine} "
                            "and evaluate recovery policy"
                        ),
                        auto_fixable=(
                            anomaly.anomaly_type ==
                            AnomalyType.ERROR_SPIKE
                            and
                            anomaly.severity ==
                            AlertLevel.CRITICAL
                            and
                            self._can_auto_fix(
                                anomaly.engine,
                                "degraded",
                            )
                        ),
                        confidence=(
                            anomaly.confidence
                        ),
                        risk_score=_anomaly_risk,
                    )
                )

        self._diagnoses.extend(
            diagnoses
        )

        if len(self._diagnoses) > 2000:

            self._diagnoses = (
                self._diagnoses[-2000:]
            )

        return diagnoses

    # ========================================================================
    # CASCADING FAILURE ANALYSIS
    # ========================================================================

    def detect_cascading_failures(
        self,
        snapshot: Optional[
            SystemSnapshot
        ] = None,
    ) -> List[Diagnosis]:

        if snapshot is None:
            snapshot = self.take_snapshot()

        results = []

        for root in self._deps.nodes():

            root_health = (
                snapshot.engines.get(root)
            )

            if not root_health:
                continue

            if root_health.status not in (
                EngineStatus.OFFLINE,
                EngineStatus.DEGRADED,
            ):
                continue

            dependents = self._collect_dependents(
                root
            )

            affected = [
                dependent
                for dependent in dependents
                if (
                    snapshot.engines.get(
                        dependent
                    ) and
                    snapshot.engines.get(
                        dependent
                    ).status in (
                        EngineStatus.OFFLINE,
                        EngineStatus.DEGRADED,
                    )
                )
            ]

            if len(affected) >= 2:

                diagnosis = Diagnosis(
                    id=(
                        "cascade_" +
                        uuid.uuid4().hex[:12]
                    ),
                    engine=root,
                    symptom=(
                        f"{len(affected)} dependent "
                        "engines are degraded/offline"
                    ),
                    root_cause=(
                        f"Possible cascading failure "
                        f"originating from {root}"
                    ),
                    severity=AlertLevel.CRITICAL,
                    affected_engines=affected,
                    dependency_chain=[
                        root
                    ] + affected,
                    recommended_action=(
                        f"Stabilize {root} first "
                        "before recovering dependents"
                    ),
                    auto_fixable=(
                        self._can_auto_fix(
                            root,
                            "degraded",
                        )
                    ),
                    confidence=0.88,
                )

                results.append(
                    diagnosis
                )

        return results

    def _collect_dependents(
        self,
        engine: str,
    ) -> List[str]:

        result: List[str] = []

        visited: Set[str] = set()

        queue = deque(
            self._deps.get_dependents(
                engine
            )
        )

        while queue:

            current = queue.popleft()

            if current in visited:
                continue

            visited.add(current)

            result.append(current)

            queue.extend(
                self._deps.get_dependents(
                    current
                )
            )

        return result

    # ========================================================================
    # FIX POLICY
    # ========================================================================

    def _recommend_fix(
        self,
        engine_name: str,
        problem: str,
    ) -> str:

        fixes = {

            "offline": (
                f"Attempt controlled recovery of "
                f"{engine_name}, verify health, "
                "then inspect dependencies."
            ),

            "degraded": (
                f"Inspect {engine_name} error rate "
                "and latency; use controlled recovery "
                "if thresholds remain exceeded."
            ),

            "stale": (
                f"Verify activity expectations for "
                f"{engine_name}; restart worker/scheduler "
                "only if inactivity is abnormal."
            ),
        }

        return fixes.get(
            problem,
            f"Investigate {engine_name} manually.",
        )

    def _can_auto_fix(
        self,
        engine_name: str,
        problem: str,
    ) -> bool:

        if (
            engine_name in
            self._recovery_handlers
        ):
            return (
                self._recovery_allowed(
                    engine_name
                )
            )

        # Do not pretend to restart arbitrary
        # engines without a handler.
        return False

    # ========================================================================
    # CIRCUIT BREAKER
    # ========================================================================

    def _recovery_allowed(
        self,
        engine_name: str,
    ) -> bool:

        now = utcnow()

        timestamps = (
            self._recovery_timestamps[
                engine_name
            ]
        )

        cutoff = (
            now -
            timedelta(hours=1)
        )

        recent = [
            ts
            for ts in timestamps
            if ts >= cutoff
        ]

        self._recovery_timestamps[
            engine_name
        ] = deque(
            recent,
            maxlen=100,
        )

        if len(recent) >= (
            self.MAX_RECOVERY_ATTEMPTS_PER_HOUR
        ):
            return False

        circuit = self._circuits[
            engine_name
        ]

        if circuit.state == "open":

            if (
                circuit.opened_at and
                (
                    now -
                    circuit.opened_at
                ).total_seconds()
                < circuit.cooldown_seconds
            ):
                return False

            circuit.state = "half_open"

            circuit.half_open_at = now

        return True

    # ========================================================================
    # AUTO HEAL
    # ========================================================================

    def auto_heal(
        self,
    ) -> List[str]:

        diagnoses = self.diagnose()

        actions: List[str] = []

        # First handle cascading failures.
        try:

            cascade_diagnoses = (
                self.detect_cascading_failures()
            )

            diagnoses.extend(
                cascade_diagnoses
            )

        except Exception as exc:

            logger.debug(
                "Cascade detection failed: %s",
                exc,
            )

        # Sort most severe first.
        severity_order = {
            AlertLevel.CRITICAL: 0,
            AlertLevel.WARNING: 1,
            AlertLevel.INFO: 2,
        }

        diagnoses.sort(
            key=lambda diagnosis:
            severity_order[
                diagnosis.severity
            ]
        )

        processed_engines: Set[
            str
        ] = set()

        for diagnosis in diagnoses:

            # §4: CRITICAL diagnoses are never auto-healed — alert only.
            # This applies whether the diagnosis is auto_fixable or not;
            # a critical failure requires human review, not automated recovery.
            if (
                diagnosis.severity ==
                AlertLevel.CRITICAL
            ):
                self._send_alert(diagnosis)
                continue

            # §4: INFO diagnoses are observe-only — no recovery attempt.
            if (
                diagnosis.severity ==
                AlertLevel.INFO
            ):
                continue

            # §4: WARNING diagnoses — only recover when all three gates pass:
            #   1. auto_fixable is True
            #   2. risk_score is below the low-risk threshold (< 30)
            #   3. no duplicate engine already handled this cycle
            #   4. circuit breaker allows recovery for this engine
            if not diagnosis.auto_fixable:
                continue

            if (
                diagnosis.risk_score >= 30
            ):
                logger.info(
                    "MetaEngine: recovery skipped for %s "
                    "(risk_score=%.1f >= 30 — not low-risk enough for auto-heal)",
                    diagnosis.engine,
                    diagnosis.risk_score,
                )
                continue

            if (
                diagnosis.engine
                in processed_engines
            ):
                continue

            if not self._recovery_allowed(
                diagnosis.engine
            ):
                logger.warning(
                    "MetaEngine: recovery suppressed "
                    "for %s due to circuit policy",
                    diagnosis.engine,
                )
                continue

            processed_engines.add(
                diagnosis.engine
            )

            attempt = self._perform_recovery(
                diagnosis
            )

            self._recovery_history.append(
                attempt
            )

            if len(
                self._recovery_history
            ) > 2000:

                self._recovery_history = (
                    self._recovery_history[
                        -2000:
                    ]
                )

            if (
                attempt.status in (
                    RecoveryStatus.SUCCESS,
                    RecoveryStatus.VERIFIED,
                )
            ):

                actions.append(
                    (
                        f"AUTO-HEAL: "
                        f"{diagnosis.engine} — "
                        f"{attempt.action}"
                    )
                )

            else:

                actions.append(
                    (
                        f"AUTO-HEAL FAILED: "
                        f"{diagnosis.engine} — "
                        f"{attempt.error or 'unknown error'}"
                    )
                )

        return actions

    def _perform_recovery(
        self,
        diagnosis: Diagnosis,
    ) -> RecoveryAttempt:

        started = utcnow()

        attempt = RecoveryAttempt(
            id=(
                "recovery_" +
                uuid.uuid4().hex[:12]
            ),
            engine=diagnosis.engine,
            reason=diagnosis.symptom,
            started_at=started,
            action=diagnosis.recommended_action,
        )

        self._recovery_timestamps[
            diagnosis.engine
        ].append(started)

        circuit = self._circuits[
            diagnosis.engine
        ]

        circuit.failures += 1

        circuit.state = "open"

        circuit.opened_at = started

        try:

            handler = (
                self._recovery_handlers.get(
                    diagnosis.engine
                )
            )

            if not handler:

                raise RuntimeError(
                    "No recovery handler registered"
                )

            handler(diagnosis)

            attempt.status = (
                RecoveryStatus.SUCCESS
            )

            attempt.finished_at = utcnow()

            # Verify.
            time.sleep(0.2)

            health = (
                self.check_engine_health(
                    diagnosis.engine,
                    record_history=False,
                )
            )

            if health.status in (
                EngineStatus.NOMINAL,
            ):

                attempt.status = (
                    RecoveryStatus.VERIFIED
                )

                attempt.verification_passed = True

                circuit.failures = 0

                circuit.state = "closed"

            else:

                circuit.state = "open"

            diagnosis.resolved_at = (
                utcnow()
            )

            self._emit(
                "meta_engine_recovery",
                {
                    "engine": diagnosis.engine,
                    "status": attempt.status.value,
                    "diagnosis": diagnosis.id,
                },
            )

            logger.info(
                "MetaEngine: recovery %s for %s",
                attempt.status.value,
                diagnosis.engine,
            )

        except Exception as exc:

            attempt.status = (
                RecoveryStatus.FAILED
            )

            attempt.finished_at = utcnow()

            attempt.error = (
                f"{type(exc).__name__}: {exc}"
            )

            circuit.state = "open"

            logger.error(
                "MetaEngine: recovery failed "
                "for %s: %s",
                diagnosis.engine,
                exc,
            )

            logger.debug(
                traceback.format_exc()
            )

        return attempt

    # ========================================================================
    # SNAPSHOT
    # ========================================================================

    def take_snapshot(
        self,
    ) -> SystemSnapshot:

        snapshot = SystemSnapshot()

        snapshot.uptime_seconds = (
            utcnow() -
            self._start_time
        ).total_seconds()

        health_scores = []

        risk_scores = []

        for name in list(
            self._engines.keys()
        ):

            try:

                health = (
                    self.check_engine_health(
                        name
                    )
                )

                # Calculate risk without recursive
                # dependency recursion.
                total = (
                    health.error_count_1h +
                    health.success_count_1h
                )

                error_rate = (
                    health.error_count_1h /
                    max(total, 1)
                )

                risk = 0.0

                if (
                    health.status ==
                    EngineStatus.OFFLINE
                ):
                    risk += 80

                elif (
                    health.status ==
                    EngineStatus.DEGRADED
                ):
                    risk += 40

                elif (
                    health.status ==
                    EngineStatus.STALE
                ):
                    risk += 30

                risk += (
                    error_rate * 40
                )

                risk += min(
                    30,
                    len(
                        health.anomaly_flags
                    ) * 7,
                )

                health.risk_score = round(
                    clamp(risk),
                    2,
                )

                snapshot.engines[
                    name
                ] = health

                health_scores.append(
                    health.health_score
                )

                risk_scores.append(
                    health.risk_score
                )

                if (
                    health.status ==
                    EngineStatus.OFFLINE
                ):

                    snapshot.critical_alerts += 1

                elif (
                    health.status ==
                    EngineStatus.DEGRADED
                ):

                    snapshot.active_alerts += 1

                elif (
                    health.status ==
                    EngineStatus.STALE
                ):

                    snapshot.active_alerts += 1

            except Exception as exc:

                logger.error(
                    "MetaEngine: failed health "
                    "check for %s: %s",
                    name,
                    exc,
                )

                snapshot.critical_alerts += 1

        if snapshot.critical_alerts:

            snapshot.overall_status = (
                EngineStatus.DEGRADED
            )

        elif snapshot.active_alerts:

            snapshot.overall_status = (
                EngineStatus.DEGRADED
            )

        else:

            snapshot.overall_status = (
                EngineStatus.NOMINAL
            )

        snapshot.health_score = round(
            mean(health_scores)
            if health_scores
            else 0.0,
            2,
        )

        snapshot.risk_score = round(
            mean(risk_scores)
            if risk_scores
            else 0.0,
            2,
        )

        snapshot.active_incidents = sum(
            1
            for incident in
            self._incidents.values()
            if incident.status not in (
                IncidentStatus.RESOLVED,
            )
        )

        self._snapshots.append(
            snapshot
        )

        self._last_monitor_cycle = (
            snapshot.timestamp
        )

        return snapshot

    # ========================================================================
    # INCIDENT MANAGEMENT
    # ========================================================================

    def _find_existing_incident(
        self,
        engine: str,
    ) -> Optional[Incident]:

        cutoff = (
            utcnow() -
            timedelta(
                seconds=self.INCIDENT_DEDUP_SECONDS
            )
        )

        for incident in (
            self._incidents.values()
        ):

            if (
                incident.primary_engine ==
                engine and
                incident.status !=
                IncidentStatus.RESOLVED and
                incident.created_at >= cutoff
            ):

                return incident

        return None

    def _create_incidents(
        self,
        diagnoses: List[Diagnosis],
    ) -> List[Incident]:

        created = []

        for diagnosis in diagnoses:

            existing = (
                self._find_existing_incident(
                    diagnosis.engine
                )
            )

            if existing:

                if (
                    diagnosis.id not in
                    existing.diagnoses
                ):

                    existing.diagnoses.append(
                        diagnosis.id
                    )

                existing.updated_at = utcnow()

                continue

            incident = Incident(
                id=(
                    "incident_" +
                    uuid.uuid4().hex[:12]
                ),
                title=(
                    diagnosis.symptom
                ),
                severity=(
                    diagnosis.severity
                ),
                status=(
                    IncidentStatus.OPEN
                ),
                primary_engine=(
                    diagnosis.engine
                ),
                affected_engines=(
                    diagnosis.affected_engines
                ),
                diagnoses=[
                    diagnosis.id
                ],
                root_cause=(
                    diagnosis.root_cause
                ),
                confidence=(
                    diagnosis.confidence
                ),
            )

            self._incidents[
                incident.id
            ] = incident

            created.append(
                incident
            )

            self._emit(
                "meta_engine_incident",
                self._serialize_incident(
                    incident
                ),
            )

        return created

    # ========================================================================
    # REPORTING
    # ========================================================================

    def get_status(
        self,
    ) -> Dict[str, Any]:

        snapshot = self.take_snapshot()

        return {
            "timestamp": iso(
                snapshot.timestamp
            ),

            "overall": (
                snapshot.overall_status.value
            ),

            "health_score": (
                snapshot.health_score
            ),

            "risk_score": (
                snapshot.risk_score
            ),

            "uptime_hours": round(
                snapshot.uptime_seconds /
                3600,
                2,
            ),

            "engine_count": len(
                snapshot.engines
            ),

            "engines": {

                name: {
                    "status": (
                        health.status.value
                    ),

                    "health_score": (
                        health.health_score
                    ),

                    "risk_score": (
                        health.risk_score
                    ),

                    "error_count_1h": (
                        health.error_count_1h
                    ),

                    "error_count_24h": (
                        health.error_count_24h
                    ),

                    "success_count_1h": (
                        health.success_count_1h
                    ),

                    "throughput_1h": round(
                        health.throughput_1h,
                        4,
                    ),

                    "throughput_5m": round(
                        health.throughput_5m,
                        4,
                    ),

                    "avg_latency_ms": round(
                        health.avg_latency_ms,
                        2,
                    ),

                    "p95_latency_ms": round(
                        health.p95_latency_ms,
                        2,
                    ),

                    "dependents": (
                        health.dependents
                    ),

                    "dependencies": (
                        health.dependencies
                    ),

                    "anomalies": (
                        health.anomaly_flags
                    ),
                }

                for name, health
                in snapshot.engines.items()
            },

            "active_diagnoses": len(
                [
                    d
                    for d in self._diagnoses
                    if d.resolved_at is None
                ]
            ),

            "active_incidents": len(
                [
                    i
                    for i in self._incidents.values()
                    if i.status !=
                    IncidentStatus.RESOLVED
                ]
            ),

            "critical_alerts": (
                snapshot.critical_alerts
            ),

            "monitoring": {
                "running": self._running,
                "cycle_count": self._cycle_count,
                "last_cycle": iso(
                    self._last_monitor_cycle
                ),
            },
        }

    def get_engine_report(
        self,
        engine_name: str,
    ) -> Dict[str, Any]:

        health = (
            self.check_engine_health(
                engine_name
            )
        )

        history = list(
            self._health_history[
                engine_name
            ]
        )[-30:]

        anomalies = [
            self._serialize_anomaly(
                anomaly
            )
            for anomaly in self._anomalies
            if (
                anomaly.engine ==
                engine_name
            )
        ][-20:]

        diagnoses = [
            self._serialize_diagnosis(
                diagnosis
            )
            for diagnosis in self._diagnoses
            if (
                diagnosis.engine ==
                engine_name
            )
        ][-20:]

        recovery = [
            self._serialize_recovery(
                attempt
            )
            for attempt in self._recovery_history
            if (
                attempt.engine ==
                engine_name
            )
        ][-20:]

        return {
            "engine": engine_name,

            "current": {
                "status": (
                    health.status.value
                ),
                "health_score": (
                    health.health_score
                ),
                "risk_score": (
                    health.risk_score
                ),
                "error_count_1h": (
                    health.error_count_1h
                ),
                "error_count_24h": (
                    health.error_count_24h
                ),
                "success_count_1h": (
                    health.success_count_1h
                ),
                "success_count_24h": (
                    health.success_count_24h
                ),
                "throughput_1h": (
                    health.throughput_1h
                ),
                "throughput_5m": (
                    health.throughput_5m
                ),
                "avg_latency_ms": (
                    health.avg_latency_ms
                ),
                "median_latency_ms": (
                    health.median_latency_ms
                ),
                "p95_latency_ms": (
                    health.p95_latency_ms
                ),
                "last_active": iso(
                    health.last_active
                ),
                "restart_count": (
                    health.restart_count
                ),
                "last_restart": iso(
                    health.last_restart
                ),
            },

            "dependencies": (
                health.dependencies
            ),

            "dependents": (
                health.dependents
            ),

            "history": [

                {
                    "status": (
                        item.status.value
                    ),
                    "health_score": (
                        item.health_score
                    ),
                    "risk_score": (
                        item.risk_score
                    ),
                    "errors": (
                        item.error_count_1h
                    ),
                    "latency_ms": (
                        item.avg_latency_ms
                    ),
                    "timestamp": iso(
                        item.checked_at
                    ),
                }

                for item in history
            ],

            "anomalies": anomalies,

            "diagnoses": diagnoses,

            "recovery": recovery,
        }

    # ========================================================================
    # TREND ANALYSIS
    # ========================================================================

    def get_trends(
        self,
        engine_name: Optional[str] = None,
    ) -> Dict[str, Any]:

        engines = (
            [engine_name]
            if engine_name
            else list(self._engines.keys())
        )

        result = {}

        for name in engines:

            history = list(
                self._health_history[
                    name
                ]
            )

            if len(history) < 2:

                result[name] = {
                    "trend": "insufficient_data",
                    "health_delta": 0.0,
                    "risk_delta": 0.0,
                }

                continue

            recent = history[-5:]

            older = history[
                max(
                    0,
                    len(history) - 10
                ):
                max(
                    0,
                    len(history) - 5
                )
            ]

            recent_health = mean(
                [
                    h.health_score
                    for h in recent
                ]
            )

            older_health = mean(
                [
                    h.health_score
                    for h in older
                ]
            ) if older else recent_health

            recent_risk = mean(
                [
                    h.risk_score
                    for h in recent
                ]
            )

            older_risk = mean(
                [
                    h.risk_score
                    for h in older
                ]
            ) if older else recent_risk

            health_delta = (
                recent_health -
                older_health
            )

            risk_delta = (
                recent_risk -
                older_risk
            )

            if health_delta <= -15:

                trend = "rapidly_declining"

            elif health_delta <= -5:

                trend = "declining"

            elif health_delta >= 15:

                trend = "rapidly_improving"

            elif health_delta >= 5:

                trend = "improving"

            else:

                trend = "stable"

            result[name] = {
                "trend": trend,
                "health_delta": round(
                    health_delta,
                    2,
                ),
                "risk_delta": round(
                    risk_delta,
                    2,
                ),
            }

        return result

    # ========================================================================
    # PREDICTIVE RISK
    # ========================================================================

    def predict_risk(
        self,
        engine_name: str,
    ) -> Dict[str, Any]:

        history = list(
            self._health_history[
                engine_name
            ]
        )

        if len(history) < 3:

            return {
                "engine": engine_name,
                "prediction": "insufficient_data",
                "confidence": 0.0,
            }

        scores = [
            h.health_score
            for h in history[-10:]
        ]

        delta = (
            scores[-1] -
            scores[0]
        ) / max(
            len(scores) - 1,
            1,
        )

        current = scores[-1]

        projected = clamp(
            current +
            delta * 5
        )

        if projected < 30:

            prediction = (
                "high_failure_risk"
            )

        elif projected < 60:

            prediction = (
                "moderate_risk"
            )

        else:

            prediction = (
                "stable"
            )

        confidence = min(
            1.0,
            len(scores) / 10.0,
        )

        return {
            "engine": engine_name,
            "current_health": round(
                current,
                2,
            ),
            "trend_per_cycle": round(
                delta,
                2,
            ),
            "projected_health": round(
                projected,
                2,
            ),
            "prediction": prediction,
            "confidence": round(
                confidence,
                2,
            ),
        }

    # ========================================================================
    # DEPENDENCY GRAPH REPORT
    # ========================================================================

    def get_dependency_graph(
        self,
    ) -> Dict[str, Any]:

        nodes = sorted(
            self._deps.nodes()
        )

        return {
            "nodes": nodes,

            "edges": dict(
                self._deps.edges
            ),

            "reverse": dict(
                self._deps.reverse
            ),

            "leaf_nodes": [
                name
                for name, deps
                in self._deps.edges.items()
                if not deps
            ],

            "root_nodes": [
                name
                for name in self._deps.edges
                if (
                    name not in
                    self._deps.reverse or
                    not self._deps.reverse[
                        name
                    ]
                )
            ],
        }

    # ========================================================================
    # ALERTING
    # ========================================================================

    def _send_alert(
        self,
        diagnosis: Diagnosis,
    ) -> None:

        payload = (
            self._serialize_diagnosis(
                diagnosis
            )
        )

        self._emit(
            "meta_engine_alert",
            payload,
        )

        if not self._alert_callback:
            return

        try:

            self._alert_callback(
                diagnosis
            )

        except Exception as exc:

            logger.error(
                "MetaEngine alert callback failed: %s",
                exc,
            )

    # ========================================================================
    # SERIALIZATION
    # ========================================================================

    def _serialize_anomaly(
        self,
        anomaly: Anomaly,
    ) -> Dict[str, Any]:

        return {
            "id": anomaly.id,
            "engine": anomaly.engine,
            "type": anomaly.anomaly_type.value,
            "description": anomaly.description,
            "severity": anomaly.severity.value,
            "observed_value": anomaly.observed_value,
            "expected_value": anomaly.expected_value,
            "deviation": anomaly.deviation,
            "confidence": anomaly.confidence,
            "detected_at": iso(
                anomaly.detected_at
            ),
            "resolved": anomaly.resolved,
        }

    def _serialize_diagnosis(
        self,
        diagnosis: Diagnosis,
    ) -> Dict[str, Any]:

        return {
            "id": diagnosis.id,
            "engine": diagnosis.engine,
            "symptom": diagnosis.symptom,
            "root_cause": diagnosis.root_cause,
            "severity": diagnosis.severity.value,
            "affected_engines": (
                diagnosis.affected_engines
            ),
            "dependency_chain": (
                diagnosis.dependency_chain
            ),
            "recommended_action": (
                diagnosis.recommended_action
            ),
            "auto_fixable": (
                diagnosis.auto_fixable
            ),
            "confidence": (
                diagnosis.confidence
            ),
            "detected_at": iso(
                diagnosis.detected_at
            ),
            "resolved_at": iso(
                diagnosis.resolved_at
            ),
        }

    def _serialize_recovery(
        self,
        attempt: RecoveryAttempt,
    ) -> Dict[str, Any]:

        return {
            "id": attempt.id,
            "engine": attempt.engine,
            "reason": attempt.reason,
            "started_at": iso(
                attempt.started_at
            ),
            "finished_at": iso(
                attempt.finished_at
            ),
            "status": attempt.status.value,
            "action": attempt.action,
            "error": attempt.error,
            "verification_passed": (
                attempt.verification_passed
            ),
        }

    def _serialize_incident(
        self,
        incident: Incident,
    ) -> Dict[str, Any]:

        return {
            "id": incident.id,
            "title": incident.title,
            "severity": incident.severity.value,
            "status": incident.status.value,
            "primary_engine": (
                incident.primary_engine
            ),
            "affected_engines": (
                incident.affected_engines
            ),
            "diagnoses": (
                incident.diagnoses
            ),
            "anomalies": (
                incident.anomalies
            ),
            "root_cause": (
                incident.root_cause
            ),
            "confidence": (
                incident.confidence
            ),
            "created_at": iso(
                incident.created_at
            ),
            "updated_at": iso(
                incident.updated_at
            ),
            "resolved_at": iso(
                incident.resolved_at
            ),
            "recovery_attempts": (
                incident.recovery_attempts
            ),
        }

    # ========================================================================
    # INCIDENT API
    # ========================================================================

    def get_incidents(
        self,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:

        incidents = sorted(
            self._incidents.values(),
            key=lambda incident:
            incident.created_at,
            reverse=True,
        )

        return [
            self._serialize_incident(
                incident
            )
            for incident in incidents[:limit]
        ]

    def resolve_incident(
        self,
        incident_id: str,
    ) -> bool:

        incident = (
            self._incidents.get(
                incident_id
            )
        )

        if not incident:
            return False

        incident.status = (
            IncidentStatus.RESOLVED
        )

        incident.resolved_at = utcnow()

        incident.updated_at = utcnow()

        self._emit(
            "meta_engine_incident_resolved",
            self._serialize_incident(
                incident
            ),
        )

        return True

    # ========================================================================
    # FULL DIAGNOSTIC REPORT
    # ========================================================================

    def get_diagnostic_report(
        self,
    ) -> Dict[str, Any]:

        snapshot = self.take_snapshot()

        diagnoses = self.diagnose()

        incidents = (
            self._create_incidents(
                diagnoses
            )
        )

        return {
            "generated_at": iso(
                utcnow()
            ),

            "system": {
                "status": (
                    snapshot.overall_status.value
                ),
                "health_score": (
                    snapshot.health_score
                ),
                "risk_score": (
                    snapshot.risk_score
                ),
                "uptime_seconds": (
                    snapshot.uptime_seconds
                ),
            },

            "engines": {
                name: {
                    "status": (
                        health.status.value
                    ),
                    "health_score": (
                        health.health_score
                    ),
                    "risk_score": (
                        health.risk_score
                    ),
                }
                for name, health
                in snapshot.engines.items()
            },

            "diagnoses": [
                self._serialize_diagnosis(
                    diagnosis
                )
                for diagnosis in diagnoses
            ],

            "new_incidents": [
                self._serialize_incident(
                    incident
                )
                for incident in incidents
            ],

            "trends": self.get_trends(),

            "predictions": {
                name: self.predict_risk(name)
                for name in self._engines
            },

            "dependency_graph": (
                self.get_dependency_graph()
            ),
        }

    # ========================================================================
    # BACKGROUND MONITOR
    # ========================================================================

    def start(
        self,
        interval_seconds: int = 60,
    ) -> None:

        if self._running:
            return

        interval_seconds = max(
            5,
            int(interval_seconds),
        )

        self._running = True

        self._thread = threading.Thread(
            target=self._monitoring_loop,
            args=(interval_seconds,),
            daemon=True,
            name="kanyoza-meta-engine",
        )

        self._thread.start()

        logger.info(
            "MetaEngine: monitoring started "
            "(every %ds)",
            interval_seconds,
        )

    def stop(
        self,
    ) -> None:

        self._running = False

        if (
            self._thread and
            self._thread.is_alive()
        ):

            self._thread.join(
                timeout=5
            )

        self._thread = None

        logger.info(
            "MetaEngine: monitoring stopped"
        )

    def _monitoring_loop(
        self,
        interval_seconds: int,
    ) -> None:

        while self._running:

            cycle_started = time.monotonic()

            try:

                self._cycle_count += 1

                snapshot = (
                    self.take_snapshot()
                )

                anomalies = (
                    self.detect_anomalies()
                )

                diagnoses = (
                    self.diagnose()
                )

                self._create_incidents(
                    diagnoses
                )

                if (
                    snapshot.overall_status !=
                    EngineStatus.NOMINAL
                ):

                    logger.warning(
                        "MetaEngine: system %s — "
                        "%d critical, %d warnings, "
                        "health=%.1f risk=%.1f",
                        snapshot.overall_status.value,
                        snapshot.critical_alerts,
                        snapshot.active_alerts,
                        snapshot.health_score,
                        snapshot.risk_score,
                    )

                if anomalies:

                    logger.warning(
                        "MetaEngine: detected %d anomalies",
                        len(anomalies),
                    )

                # §4: Delegate to auto_heal() which now enforces the full
                # risk-gated policy:
                #   CRITICAL  → alert only, no recovery
                #   WARNING + auto_fixable + risk_score < 30 → recover
                #   INFO      → observe only
                actions = (
                    self.auto_heal()
                )

                if actions:

                    logger.info(
                        "MetaEngine: "
                        "%d recovery actions",
                        len(actions),
                    )

            except Exception as exc:

                logger.error(
                    "MetaEngine monitoring cycle failed: %s",
                    exc,
                )

                logger.debug(
                    traceback.format_exc()
                )

            elapsed = (
                time.monotonic() -
                cycle_started
            )

            sleep_for = max(
                0.1,
                interval_seconds -
                elapsed,
            )

            # Interruptible-ish sleep.
            end = (
                time.monotonic() +
                sleep_for
            )

            while (
                self._running and
                time.monotonic() < end
            ):

                time.sleep(
                    min(
                        1.0,
                        max(
                            0.01,
                            end -
                            time.monotonic(),
                        ),
                    )
                )

    # ========================================================================
    # MANUAL CONTROL
    # ========================================================================

    def run_cycle(
        self,
    ) -> Dict[str, Any]:

        snapshot = (
            self.take_snapshot()
        )

        anomalies = (
            self.detect_anomalies()
        )

        diagnoses = (
            self.diagnose()
        )

        incidents = (
            self._create_incidents(
                diagnoses
            )
        )

        return {
            "snapshot": self.get_status(),
            "anomalies": [
                self._serialize_anomaly(
                    anomaly
                )
                for anomaly in anomalies
            ],
            "diagnoses": [
                self._serialize_diagnosis(
                    diagnosis
                )
                for diagnosis in diagnoses
            ],
            "incidents": [
                self._serialize_incident(
                    incident
                )
                for incident in incidents
            ],
        }

    def force_recovery(
        self,
        engine_name: str,
        reason: str = "manual recovery",
    ) -> Dict[str, Any]:

        diagnosis = Diagnosis(
            id=(
                "manual_" +
                uuid.uuid4().hex[:12]
            ),
            engine=engine_name,
            symptom=reason,
            root_cause="Manual recovery request",
            severity=AlertLevel.WARNING,
            recommended_action=(
                f"Manual recovery of {engine_name}"
            ),
            auto_fixable=True,
        )

        attempt = self._perform_recovery(
            diagnosis
        )

        self._recovery_history.append(
            attempt
        )

        return self._serialize_recovery(
            attempt
        )

    # ========================================================================
    # CLEANUP
    # ========================================================================

    def cleanup_history(
        self,
        days: int = 7,
    ) -> None:

        cutoff = (
            utcnow() -
            timedelta(days=days)
        )

        self._diagnoses = [
            diagnosis
            for diagnosis in self._diagnoses
            if (
                diagnosis.detected_at >=
                cutoff or
                diagnosis.resolved_at is None
            )
        ]

        self._anomalies = [
            anomaly
            for anomaly in self._anomalies
            if (
                anomaly.detected_at >=
                cutoff
            )
        ]

        self._recovery_history = [
            attempt
            for attempt in self._recovery_history
            if (
                attempt.started_at >=
                cutoff
            )
        ]

        logger.info(
            "MetaEngine: historical data cleanup complete"
        )


# ============================================================================
# MODULE SINGLETON
# ============================================================================


meta_engine = MetaEngine()


# ============================================================================
# INITIALIZER
# ============================================================================


def init_meta_engine(
    event_bus: Any = None,
    container: Any = None,
) -> MetaEngine:

    """
    Initialize and start the global Meta Engine.

    Existing integrations can continue calling:

        init_meta_engine(
            event_bus=event_bus,
            container=container,
        )
    """

    global meta_engine

    # Stop previous instance if necessary.
    try:

        meta_engine.stop()

    except Exception:

        pass

    meta_engine = MetaEngine(
        event_bus=event_bus,
        container=container,
    )

    meta_engine.register_all_engines()

    # ------------------------------------------------------------------------
    # Default recovery handler: Card Renderer
    # ------------------------------------------------------------------------

    def restart_card_renderer(
        diagnosis: Diagnosis,
    ) -> None:

        logger.warning(
            "MetaEngine: attempting Card Renderer recovery"
        )

        engine = (
            meta_engine._engines.get(
                "card_renderer"
            )
        )

        if engine is None:

            raise RuntimeError(
                "Card Renderer is not registered"
            )

        # Try common lifecycle methods.
        if hasattr(
            engine,
            "restart",
        ):

            engine.restart()

        elif hasattr(
            engine,
            "reset",
        ):

            engine.reset()

        elif hasattr(
            engine,
            "start",
        ):

            # Only start if not already running.
            engine.start()

        else:

            # No destructive operation available.
            # Report the condition instead.
            logger.warning(
                "MetaEngine: Card Renderer has "
                "no restart/reset/start method"
            )

    meta_engine.register_recovery_handler(
        "card_renderer",
        restart_card_renderer,
    )

    # ------------------------------------------------------------------------
    # Generic recovery handlers
    # ------------------------------------------------------------------------

    def generic_restart_handler(
        diagnosis: Diagnosis,
    ) -> None:

        engine_name = diagnosis.engine

        engine = (
            meta_engine._engines.get(
                engine_name
            )
        )

        if engine is None:

            raise RuntimeError(
                f"Engine not registered: {engine_name}"
            )

        # Prefer explicit restart.
        if hasattr(
            engine,
            "restart",
        ):

            engine.restart()
            return

        # Then reset.
        if hasattr(
            engine,
            "reset",
        ):

            engine.reset()
            return

        # Then stop/start.
        if (
            hasattr(engine, "stop") and
            hasattr(engine, "start")
        ):

            engine.stop()

            time.sleep(0.1)

            engine.start()

            return

        raise RuntimeError(
            f"No safe recovery lifecycle available "
            f"for {engine_name}"
        )

    # Register generic recovery only for engines
    # that actually expose safe lifecycle methods.
    for engine_name, engine in list(
        meta_engine._engines.items()
    ):

        if engine_name == "card_renderer":
            continue

        if any(
            hasattr(
                engine,
                method,
            )
            for method in (
                "restart",
                "reset",
            )
        ):

            meta_engine.register_recovery_handler(
                engine_name,
                generic_restart_handler,
            )

    # Start.
    meta_engine.start(
        interval_seconds=60
    )

    logger.info(
        "KANYOZA META ENGINE v2.0 initialized"
    )

    return meta_engine


# ============================================================================
# SHUTDOWN HELPER
# ============================================================================


def shutdown_meta_engine() -> None:

    global meta_engine

    try:

        meta_engine.stop()

    except Exception as exc:

        logger.debug(
            "MetaEngine shutdown error: %s",
            exc,
        )


# ============================================================================
# OPTIONAL ATEXIT REGISTRATION
# ============================================================================


try:

    import atexit

    atexit.register(
        shutdown_meta_engine
    )

except Exception:

    pass
