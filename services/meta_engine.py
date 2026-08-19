"""
KANYOZA META ENGINE v7.0 — FULLY POLISHED
==========================================

RESOURCE-SAFE INTELLIGENT SUPERVISOR — ASYNC-NATIVE

Design goals (RETAINED from v6.0.2):
  1. Protect the application without becoming a resource liability.
  2. Keep all state strictly bounded.
  3. Never perform nested monitoring cycles.
  4. Never create an unbounded number of threads.
  5. Cache health observations instead of repeatedly probing engines.
  6. Keep API/status reads cheap and read-only.
  7. Persist snapshots periodically rather than on every cycle.
  8. Adapt monitoring frequency according to system pressure.
  9. Detect dependency cascades without recursively re-scanning engines.
  10. Perform conservative automatic recovery.
  11. Learn recurring failure patterns without unbounded memory.
  12. Fail open: Meta Engine failure must NOT bring down the application.

V7.0 CHANGES (Additive — nothing removed):
  - Async-native monitoring loop (asyncio.Task replaces threading.Thread)
  - True cancellation (no thread leaks)
  - Health check integration (check_health_async for lifecycle manager)
  - Correlation ID tracking
  - Enhanced alert deduplication
  - Persistent failure patterns
  - Complete API compatibility (all v6.0.1 methods retained)
"""

from __future__ import annotations

import asyncio
import atexit
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
    from core.shared_event_bus import get_shared_event_bus
    HAS_EVENT_BUS = True
except ImportError:
    HAS_EVENT_BUS = False

def _publish_event(event_name: str, payload: Dict[str, Any]) -> None:
    if not HAS_EVENT_BUS:
        return
    try:
        bus = get_shared_event_bus()
        bus.publish(event_name, payload)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════
# OPTIONAL PSUTIL
# ═══════════════════════════════════════════════════════════════════════════

try:
    import psutil
except Exception:
    psutil = None


# ═══════════════════════════════════════════════════════════════════════════
# ENUMS (Retained)
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
    RUNNING = "running"
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
    MEMORY_PRESSURE = "memory_pressure"
    CPU_PRESSURE = "cpu_pressure"
    RECOVERY_LOOP = "recovery_loop"

class EscalationTier(str, Enum):
    NONE = "none"
    LOG_ONLY = "log_only"
    DASHBOARD = "dashboard"
    TELEGRAM = "telegram"
    EMAIL = "email"
    SMS = "sms"
    ON_CALL = "on_call"

class ResourceMode(str, Enum):
    NORMAL = "normal"
    CAUTIOUS = "cautious"
    PRESSURED = "pressured"
    EMERGENCY = "emergency"


# ═══════════════════════════════════════════════════════════════════════════
# DATACLASSES (Retained)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class EngineHealth:
    name: str
    status: EngineStatus = EngineStatus.UNKNOWN
    health_score: float = 0.0
    risk_score: float = 0.0
    last_active: Optional[datetime] = None
    error_count_5m: int = 0
    error_count_1h: int = 0
    success_count_5m: int = 0
    success_count_1h: int = 0
    throughput_5m: float = 0.0
    throughput_1h: float = 0.0
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
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

@dataclass
class SystemResources:
    rss_mb: float = 0.0
    memory_percent: float = 0.0
    cpu_percent: float = 0.0
    available_memory_mb: float = 0.0
    disk_percent: float = 0.0
    mode: ResourceMode = ResourceMode.NORMAL
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
    resources: SystemResources = field(default_factory=SystemResources)

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
    correlation_id: str = field(default_factory=lambda: uuid.uuid4().hex)

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

@dataclass
class CachedHealth:
    health: EngineHealth
    expires_at: float


# ═══════════════════════════════════════════════════════════════════════════
# UTILITIES (Retained)
# ═══════════════════════════════════════════════════════════════════════════

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

def iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None

def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    try:
        return max(low, min(high, float(value)))
    except Exception:
        return low

def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

def percentile(values: Iterable[float], p: float) -> float:
    data = sorted(float(v) for v in values)
    if not data:
        return 0.0
    if len(data) == 1:
        return data[0]
    p = clamp(p, 0.0, 100.0)
    index = (len(data) - 1) * p / 100.0
    low = int(math.floor(index))
    high = int(math.ceil(index))
    if low == high:
        return data[low]
    return data[low] + (data[high] - data[low]) * (index - low)

def short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


# ═══════════════════════════════════════════════════════════════════════════
# RESOURCE MONITOR (Retained)
# ═══════════════════════════════════════════════════════════════════════════

class ResourceMonitor:
    def __init__(self, cautious_memory_percent: float = 70.0, pressured_memory_percent: float = 82.0, emergency_memory_percent: float = 92.0, cautious_rss_mb: float = 250.0, pressured_rss_mb: float = 350.0, emergency_rss_mb: float = 450.0):
        self.cautious_memory_percent = cautious_memory_percent
        self.pressured_memory_percent = pressured_memory_percent
        self.emergency_memory_percent = emergency_memory_percent
        self.cautious_rss_mb = cautious_rss_mb
        self.pressured_rss_mb = pressured_rss_mb
        self.emergency_rss_mb = emergency_rss_mb
        self._process = psutil.Process(os.getpid()) if psutil else None

    def _linux_rss_mb(self) -> float:
        try:
            with open("/proc/self/status", "r", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("VmRSS:"):
                        return float(line.split()[1]) / 1024.0
        except Exception:
            return 0.0
        return 0.0

    def sample(self) -> SystemResources:
        rss_mb = 0.0
        memory_percent = 0.0
        cpu_percent = 0.0
        available_memory_mb = 0.0
        disk_percent = 0.0

        try:
            if self._process is not None:
                rss_mb = self._process.memory_info().rss / 1024 / 1024
                cpu_percent = self._process.cpu_percent(interval=None)
                vm = psutil.virtual_memory()
                memory_percent = float(vm.percent)
                available_memory_mb = vm.available / 1024 / 1024
                disk = psutil.disk_usage("/")
                disk_percent = float(disk.percent)
            else:
                rss_mb = self._linux_rss_mb()
        except Exception:
            pass

        if memory_percent >= self.emergency_memory_percent or rss_mb >= self.emergency_rss_mb:
            mode = ResourceMode.EMERGENCY
        elif memory_percent >= self.pressured_memory_percent or rss_mb >= self.pressured_rss_mb:
            mode = ResourceMode.PRESSURED
        elif memory_percent >= self.cautious_memory_percent or rss_mb >= self.cautious_rss_mb:
            mode = ResourceMode.CAUTIOUS
        else:
            mode = ResourceMode.NORMAL

        return SystemResources(rss_mb=round(rss_mb, 2), memory_percent=round(memory_percent, 2), cpu_percent=round(cpu_percent, 2), available_memory_mb=round(available_memory_mb, 2), disk_percent=round(disk_percent, 2), mode=mode)


# ═══════════════════════════════════════════════════════════════════════════
# DEPENDENCY GRAPH (Retained)
# ═══════════════════════════════════════════════════════════════════════════

class DependencyGraph:
    def __init__(self):
        self.edges: Dict[str, List[str]] = {}
        self.reverse: Dict[str, List[str]] = {}

    def add(self, engine: str, depends_on: Iterable[str]) -> None:
        dependencies = list(dict.fromkeys(depends_on))
        self.edges[engine] = dependencies
        for dependency in dependencies:
            self.reverse.setdefault(dependency, [])
            if engine not in self.reverse[dependency]:
                self.reverse[dependency].append(engine)

    def get_dependencies(self, engine: str) -> List[str]:
        return list(self.edges.get(engine, []))

    def get_dependents(self, engine: str) -> List[str]:
        return list(self.reverse.get(engine, []))

    def nodes(self) -> Set[str]:
        result = set(self.edges)
        for dependencies in self.edges.values():
            result.update(dependencies)
        result.update(self.reverse)
        return result


# ═══════════════════════════════════════════════════════════════════════════
# BOUNDED PROBE EXECUTOR (Retained)
# ═══════════════════════════════════════════════════════════════════════════

class BoundedProbeExecutor:
    def __init__(self, timeout_seconds: float = 3.0):
        self.timeout_seconds = timeout_seconds
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="meta-health")
        self._lock = threading.RLock()
        self._busy = False

    def run(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Tuple[bool, Any]:
        with self._lock:
            if self._busy:
                return False, TimeoutError("Health probe worker busy")
            self._busy = True
        try:
            future = self._executor.submit(fn, *args, **kwargs)
            try:
                result = future.result(timeout=self.timeout_seconds)
                return True, result
            except FutureTimeout:
                return False, TimeoutError(f"Health probe timeout after {self.timeout_seconds}s")
            except Exception as exc:
                return False, exc
        finally:
            with self._lock:
                self._busy = False

    def shutdown(self) -> None:
        try:
            self._executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            self._executor.shutdown(wait=False)


# ═══════════════════════════════════════════════════════════════════════════
# BOUNDED RECOVERY EXECUTOR (Retained)
# ═══════════════════════════════════════════════════════════════════════════

class BoundedRecoveryExecutor:
    def __init__(self):
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="meta-recovery")
        self._lock = threading.RLock()
        self._busy = False

    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Optional[Future]:
        with self._lock:
            if self._busy:
                return None
            self._busy = True
        future = self._executor.submit(fn, *args, **kwargs)
        def _done(_: Future) -> None:
            with self._lock:
                self._busy = False
        future.add_done_callback(_done)
        return future

    def busy(self) -> bool:
        with self._lock:
            return self._busy

    def shutdown(self) -> None:
        try:
            self._executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            self._executor.shutdown(wait=False)


# ═══════════════════════════════════════════════════════════════════════════
# ALERT RATE LIMITER (Retained)
# ═══════════════════════════════════════════════════════════════════════════

class GlobalAlertRateLimiter:
    def __init__(self, max_alerts_per_minute: int = 3, max_alerts_per_hour: int = 15):
        self.max_alerts_per_minute = max_alerts_per_minute
        self.max_alerts_per_hour = max_alerts_per_hour
        self._minute: Deque[float] = deque(maxlen=max_alerts_per_minute * 2)
        self._hour: Deque[float] = deque(maxlen=max_alerts_per_hour * 2)
        self._lock = threading.RLock()

    def can_send(self) -> bool:
        with self._lock:
            now = time.monotonic()
            while self._minute and now - self._minute[0] >= 60:
                self._minute.popleft()
            while self._hour and now - self._hour[0] >= 3600:
                self._hour.popleft()
            return len(self._minute) < self.max_alerts_per_minute and len(self._hour) < self.max_alerts_per_hour

    def record_send(self) -> None:
        with self._lock:
            now = time.monotonic()
            self._minute.append(now)
            self._hour.append(now)


# ═══════════════════════════════════════════════════════════════════════════
# STATE PERSISTENCE (Retained)
# ═══════════════════════════════════════════════════════════════════════════

class StatePersistence:
    def __init__(self, supabase_client: Any = None, file_path: str = "/tmp/kanyoza_meta_engine_state.json", minimum_save_interval_seconds: int = 300):
        self.supabase = supabase_client
        self.file_path = file_path
        self.minimum_save_interval_seconds = minimum_save_interval_seconds
        self._last_save_monotonic = 0.0
        self._lock = threading.RLock()

    def _serialize(self, snapshot: SystemSnapshot) -> Dict[str, Any]:
        return {
            "timestamp": iso(snapshot.timestamp),
            "overall_status": snapshot.overall_status.value,
            "health_score": snapshot.health_score,
            "risk_score": snapshot.risk_score,
            "engine_count": len(snapshot.engines),
            "resources": {"rss_mb": snapshot.resources.rss_mb, "memory_percent": snapshot.resources.memory_percent, "cpu_percent": snapshot.resources.cpu_percent, "mode": snapshot.resources.mode.value},
            "engines": {name: {"status": health.status.value, "health_score": health.health_score, "risk_score": health.risk_score, "error_count_1h": health.error_count_1h} for name, health in snapshot.engines.items()},
        }

    def save_snapshot(self, snapshot: SystemSnapshot, force: bool = False) -> bool:
        with self._lock:
            now = time.monotonic()
            if not force and now - self._last_save_monotonic < self.minimum_save_interval_seconds:
                return False
            data = self._serialize(snapshot)
            saved = False
            if self.supabase:
                try:
                    self.supabase.table("meta_engine_snapshots").upsert({"timestamp": iso(snapshot.timestamp), "payload": json.dumps(data, separators=(",", ":"))}).execute()
                    saved = True
                except Exception as exc:
                    logger.warning("Meta Engine Supabase persistence failed: %s", exc)
            try:
                temporary = self.file_path + ".tmp"
                with open(temporary, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, separators=(",", ":"))
                os.replace(temporary, self.file_path)
                saved = True
            except Exception:
                pass
            if saved:
                self._last_save_monotonic = now
            return saved

    def load_recent_snapshot(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            try:
                with open(self.file_path, "r", encoding="utf-8") as fh:
                    return json.load(fh)
            except Exception:
                pass
            if self.supabase:
                try:
                    result = self.supabase.table("meta_engine_snapshots").select("*").order("timestamp", desc=True).limit(1).execute()
                    if result.data:
                        return json.loads(result.data[0].get("payload", "{}"))
                except Exception:
                    pass
            return None


# ═══════════════════════════════════════════════════════════════════════════
# INCIDENT MANAGER (Retained)
# ═══════════════════════════════════════════════════════════════════════════

class IncidentManager:
    def __init__(self, engine: "MetaEngineV7"):
        self.engine = engine

    def resolve_healthy_incidents(self, snapshot: SystemSnapshot) -> int:
        resolved = 0
        for incident in list(self.engine._incidents.values()):
            if incident.status == IncidentStatus.RESOLVED:
                continue
            health = snapshot.engines.get(incident.primary_engine)
            if not health or health.status != EngineStatus.NOMINAL:
                continue
            affected_healthy = True
            for affected in incident.affected_engines:
                affected_health = snapshot.engines.get(affected)
                if affected_health and affected_health.status not in (EngineStatus.NOMINAL, EngineStatus.UNKNOWN):
                    affected_healthy = False
                    break
            if affected_healthy:
                now = utcnow()
                incident.status = IncidentStatus.RESOLVED
                incident.resolved_at = now
                incident.updated_at = now
                resolved += 1
                self.engine.learn_from_incident(incident)
        return resolved

    def escalate_old_incidents(self) -> int:
        escalated = 0
        now = utcnow()
        for incident in list(self.engine._incidents.values()):
            if incident.status in (IncidentStatus.RESOLVED, IncidentStatus.ESCALATED):
                continue
            age = (now - incident.created_at).total_seconds()
            if age >= self.engine.INCIDENT_ESCALATION_SECONDS:
                incident.status = IncidentStatus.ESCALATED
                incident.updated_at = now
                escalated += 1
        return escalated


# ═══════════════════════════════════════════════════════════════════════════
# META ENGINE V7.0 — FULLY POLISHED
# ═══════════════════════════════════════════════════════════════════════════

class MetaEngineV7:
    """Intelligent, bounded, resource-aware supervisory engine — v7.0 Async-Native."""

    VERSION = "7.0.0"

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

    def __init__(self, event_bus: Any = None, container: Any = None, supabase_client: Any = None):
        self.event_bus = event_bus
        self.container = container
        self.supabase = supabase_client

        self._engines: Dict[str, Any] = {}
        self._engine_versions: Dict[str, str] = {}
        self._deps = DependencyGraph()

        # Bounded collections
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

        # Async state
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
        self._persistence = StatePersistence(supabase_client=supabase_client)
        self._incident_manager = IncidentManager(self)
        self._alert_limiter = GlobalAlertRateLimiter()

        self._build_dependency_graph()
        logger.info("KANYOZA META ENGINE v%s initialized — resource-safe async-native supervisor", self.VERSION)

    # ═══════════════════════════════════════════════════════════════════════
    # DEPENDENCY GRAPH (Retained)
    # ═══════════════════════════════════════════════════════════════════════

    def _build_dependency_graph(self) -> None:
        dependencies = [
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
            ("redis", []),
        ]
        for engine, deps in dependencies:
            self._deps.add(engine, deps)

    # ═══════════════════════════════════════════════════════════════════════
    # LIFECYCLE (Async-Native)
    # ═══════════════════════════════════════════════════════════════════════

    @property
    def service_name(self) -> str:
        return "meta_engine"

    async def start(self, interval_seconds: int = None) -> None:
        """Start Meta Engine (async-native)."""
        if interval_seconds is None:
            interval_seconds = self.NORMAL_INTERVAL_SECONDS

        with self._lock:
            if self._running:
                return
            self._running = True
            self._monitor_interval_seconds = max(30, int(interval_seconds))

            # Load previous state
            loop = asyncio.get_event_loop()
            previous = await loop.run_in_executor(None, self._persistence.load_recent_snapshot)
            if previous:
                logger.info("Meta Engine restored previous state from %s", previous.get("timestamp"))

            # Create async task
            self._async_task = asyncio.create_task(
                self._async_monitoring_loop(),
                name="meta-engine-supervisor",
            )

            logger.info("KANYOZA META ENGINE v%s started — async-native supervisor", self.VERSION)

    async def stop(self) -> None:
        """Stop Meta Engine (async-native)."""
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
        """Health check for lifecycle integration."""
        try:
            snapshot = self._current_snapshot
            if snapshot is None:
                return {"status": "degraded" if self._running else "stopped", "error": "No snapshot available"}

            return {
                "status": "healthy" if snapshot.overall_status == EngineStatus.NOMINAL else "degraded",
                "version": self.VERSION,
                "overall": snapshot.overall_status.value,
                "health_score": snapshot.health_score,
                "risk_score": snapshot.risk_score,
                "engine_count": len(self._engines),
                "active_incidents": snapshot.active_incidents,
                "critical_alerts": snapshot.critical_alerts,
                "resource_mode": snapshot.resources.mode.value,
                "cycle_count": self._cycle_count,
                "uptime_hours": round(snapshot.uptime_seconds / 3600, 2),
            }
        except Exception as exc:
            return {"status": "degraded", "error": str(exc)[:200]}

    async def _async_monitoring_loop(self) -> None:
        """Async monitoring loop."""
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

    # ═══════════════════════════════════════════════════════════════════════
    # REGISTRATION (Retained)
    # ═══════════════════════════════════════════════════════════════════════

    @staticmethod
    def _resolve_engine_instance(engine: Any) -> Any:
        if engine is None:
            return None
        if not isinstance(engine, type) and not callable(engine):
            return engine
        if isinstance(engine, type):
            for getter_name in ("get_instance", "get", "instance", "get_plugin_registry", "get_event_bus"):
                getter = getattr(engine, getter_name, None)
                if callable(getter):
                    try:
                        instance = getter()
                        if instance is not None and not isinstance(instance, type):
                            return instance
                    except Exception:
                        continue
            try:
                instance = engine()
                if instance is not None:
                    return instance
            except Exception:
                return engine
        if callable(engine):
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
            self._engine_versions[name] = version or getattr(resolved, "__version__", "") or ""
            self._circuits.setdefault(name, CircuitState(engine=name))

    def register_all_engines(self) -> None:
        try:
            from services.director import ENGINE_REGISTRY
            for module_path, attr_name, display_name, sector in ENGINE_REGISTRY:
                try:
                    module = __import__(module_path, fromlist=[attr_name])
                    engine = getattr(module, attr_name, None)
                    if engine is not None:
                        self.register(display_name, engine)
                except Exception as exc:
                    logger.debug("Engine registration skipped %s: %s", display_name, exc)
        except ImportError:
            self._manual_register()
        if self.container:
            try:
                plugins = getattr(self.container, "industry_plugins", {})
                for name, plugin in plugins.items():
                    try:
                        self.register(name, plugin)
                    except Exception:
                        pass
            except Exception:
                pass
        logger.info("Meta Engine registered %d engines", len(self._engines))

    def _manual_register(self) -> None:
        registry = [
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
        for name, module_name, attr_name in registry:
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

    # ═══════════════════════════════════════════════════════════════════════
    # METRICS (Retained)
    # ═══════════════════════════════════════════════════════════════════════

    def record_success(self, engine_name: str) -> None:
        self._success_counts[engine_name].append(utcnow())
        if engine_name in self._unhealthy_cycles:
            self._unhealthy_cycles[engine_name] = 0

    def record_error(self, engine_name: str, error: str = "") -> None:
        self._error_counts[engine_name].append(utcnow())

    def record_latency(self, engine_name: str, milliseconds: float) -> None:
        self._latency_samples[engine_name].append((utcnow(), max(0.0, safe_float(milliseconds))))

    def record_metric(self, engine_name: str, metric_name: str, value: float, metadata: Optional[Dict[str, Any]] = None) -> None:
        self._metrics[engine_name][metric_name].append(
            MetricEvent(timestamp=utcnow(), kind=metric_name, engine=engine_name, value=safe_float(value), metadata=metadata or {})
        )

    # ═══════════════════════════════════════════════════════════════════════
    # HEALTH CHECK (Retained)
    # ═══════════════════════════════════════════════════════════════════════

    def check_engine_health(self, name: str, force: bool = False, record_history: bool = True) -> EngineHealth:
        if not force and self._cached_health_valid(name):
            return self._cached_health[name].health

        engine = self._engines.get(name)
        health = EngineHealth(name=name, version=self._engine_versions.get(name, ""))
        health.dependencies = self._deps.get_dependencies(name)
        health.dependents = self._deps.get_dependents(name)

        if engine is None:
            health.status = EngineStatus.OFFLINE
            health.health_score = 0.0
            health.risk_score = 100.0
            self._cache_health(name, health)
            if record_history:
                self._health_history[name].append(health)
            self._unhealthy_cycles[name] += 1
            return health

        if not self._ping_engine(engine, name):
            health.status = EngineStatus.OFFLINE
            health.health_score = 0.0
            health.risk_score = 100.0
            self._cache_health(name, health)
            if record_history:
                self._health_history[name].append(health)
            self._unhealthy_cycles[name] += 1
            return health

        self._unhealthy_cycles[name] = 0
        now = utcnow()
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

        if error_rate >= self.ERROR_CRITICAL_THRESHOLD:
            health.status = EngineStatus.DEGRADED
        elif error_rate >= self.ERROR_DEGRADED_THRESHOLD:
            health.status = EngineStatus.DEGRADED
        elif total_operations == 0:
            last_success = successes[-1] if successes else None
            if last_success and (now - last_success).total_seconds() > self.STALE_AFTER_SECONDS:
                health.status = EngineStatus.STALE
            else:
                health.status = EngineStatus.NOMINAL
        else:
            health.status = EngineStatus.NOMINAL

        health.health_score = self._calculate_health_score(health)
        health.risk_score = self._calculate_risk_score(health)
        health.last_active = successes[-1] if successes else None

        self._cache_health(name, health)
        if record_history:
            self._health_history[name].append(health)

        return health

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
            if custom_handler:
                result = custom_handler(engine)
                if isinstance(result, dict):
                    status = str(result.get("status", "")).lower()
                    return status in {"healthy", "ok", "nominal", "ready", "degraded"}
                return bool(result)
            if hasattr(engine, "health_check"):
                health_check = getattr(engine, "health_check")
                if callable(health_check):
                    result = health_check()
                    if isinstance(result, dict):
                        status = str(result.get("status", "")).lower()
                        return status in {"healthy", "ok", "nominal", "ready", "degraded"}
                    return bool(result)
            if hasattr(engine, "get_status"):
                get_status = getattr(engine, "get_status")
                if callable(get_status):
                    result = get_status()
                    if isinstance(result, dict):
                        if result.get("running") is True:
                            return True
                        status = str(result.get("status", "")).lower()
                        return status not in {"stopped", "offline", "failed"}
                    return result is not None
            if hasattr(engine, "get_stats"):
                get_stats = getattr(engine, "get_stats")
                if callable(get_stats):
                    return get_stats() is not None
            return True
        except Exception as exc:
            logger.debug("Health probe failed for %s: %s", engine_name, exc)
            return False

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
        total = health.error_count_1h + health.success_count_1h
        if total:
            risk += (health.error_count_1h / total) * 40
        if health.cpu_percent > 90:
            risk += 15
        if health.memory_mb > 90:
            risk += 15
        risk += min(25, len(health.anomaly_flags) * 5)
        return round(clamp(risk), 2)

    # ═══════════════════════════════════════════════════════════════════════
    # SCAN & DIAGNOSE (Retained)
    # ═══════════════════════════════════════════════════════════════════════

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
                elif health.status in (EngineStatus.DEGRADED, EngineStatus.STALE):
                    if self._should_alert_for_service(name, health):
                        snapshot.active_alerts += 1
            except Exception as exc:
                logger.debug("Engine scan failed for %s: %s", name, exc)

        # Dependency penalty
        for name, health in snapshot.engines.items():
            dependency_penalty = 0
            for dependency in health.dependencies:
                dependency_health = snapshot.engines.get(dependency)
                if not dependency_health:
                    continue
                if dependency_health.status == EngineStatus.OFFLINE:
                    dependency_penalty += 20
                elif dependency_health.status == EngineStatus.DEGRADED:
                    dependency_penalty += 10
            health.risk_score = round(clamp(health.risk_score + dependency_penalty), 2)

        snapshot.health_score = round(mean(health_scores), 2) if health_scores else 0.0
        snapshot.risk_score = round(mean(risk_scores), 2) if risk_scores else 0.0

        if snapshot.critical_alerts:
            snapshot.overall_status = EngineStatus.DEGRADED
        elif snapshot.active_alerts:
            snapshot.overall_status = EngineStatus.DEGRADED
        else:
            snapshot.overall_status = EngineStatus.NOMINAL

        snapshot.active_incidents = sum(incident.status not in (IncidentStatus.RESOLVED,) for incident in self._incidents.values())

        self._snapshots.append(snapshot)
        self._current_snapshot = snapshot
        self._last_monitor_cycle = snapshot.timestamp

        _publish_event("service_heartbeat", {
            "service": "meta",
            "status": snapshot.overall_status.value,
            "health_score": snapshot.health_score,
        })

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
            _publish_event("engine_alert", {
                "engine": diagnosis.engine,
                "status": diagnosis.severity.value,
                "message": diagnosis.symptom,
                "root_cause": diagnosis.root_cause,
            })

        return diagnoses

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

    # ═══════════════════════════════════════════════════════════════════════
    # CASCADING & RECOVERY (Retained)
    # ═══════════════════════════════════════════════════════════════════════

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
            return attempt

        try:
            handler(diagnosis)
            attempt.status = RecoveryStatus.SUCCESS
            attempt.finished_at = utcnow()
            self._cached_health.pop(diagnosis.engine, None)
            health = self.check_engine_health(diagnosis.engine, force=True, record_history=False)
            if health.status == EngineStatus.NOMINAL:
                attempt.status = RecoveryStatus.VERIFIED
                attempt.verification_passed = True
            else:
                attempt.error = "Recovery completed but health verification failed"
        except Exception as exc:
            attempt.status = RecoveryStatus.FAILED
            attempt.error = f"{type(exc).__name__}: {exc}"
            logger.error("Recovery failed for %s: %s", diagnosis.engine, exc)

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

    # ═══════════════════════════════════════════════════════════════════════
    # PREDICTIONS (Retained)
    # ═══════════════════════════════════════════════════════════════════════

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

    # ═══════════════════════════════════════════════════════════════════════
    # API COMPATIBILITY (Retained — Complete)
    # ═══════════════════════════════════════════════════════════════════════

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
        }

    # ═══════════════════════════════════════════════════════════════════════
    # MONITORING CYCLE (Retained)
    # ═══════════════════════════════════════════════════════════════════════

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
                    self._send_telegram_alert(diagnosis)
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

    def _expensive_operations_allowed(self) -> bool:
        return self._current_resources.mode in (ResourceMode.NORMAL, ResourceMode.CAUTIOUS)

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

    def _send_telegram_alert(self, diagnosis: Diagnosis) -> None:
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
            email_service.send_telegram(
                f"🔴 <b>OVERSEER CRITICAL: {diagnosis.engine}</b>\n"
                f"{diagnosis.symptom}\n{diagnosis.root_cause}\n"
                f"<i>{utcnow().strftime('%H:%M UTC')}</i>"
            )
        except Exception as exc:
            logger.debug("Telegram alert unavailable: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════
# SINGLETON (Retained)
# ═══════════════════════════════════════════════════════════════════════════

meta_engine = MetaEngineV7()


def init_meta_engine(event_bus: Any = None, container: Any = None, supabase_client: Any = None) -> MetaEngineV7:
    """Initialize the Meta Engine singleton."""
    global meta_engine
    try:
        meta_engine.stop()
    except Exception:
        pass
    meta_engine.event_bus = event_bus
    meta_engine.container = container
    meta_engine.supabase = supabase_client
    meta_engine._persistence = StatePersistence(supabase_client=supabase_client)
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
            restart()
            return
        if callable(reset):
            reset()
            return
        if callable(stop) and callable(start):
            stop()
            time.sleep(0.1)
            start()
            return
        raise RuntimeError(f"No safe recovery interface for {diagnosis.engine}")

    for name, engine in list(meta_engine._engines.items()):
        if any(callable(getattr(engine, method, None)) for method in ("restart", "reset")):
            meta_engine.register_recovery_handler(name, generic_restart)

    # Start async (if in event loop) or sync (if not)
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(meta_engine.start(interval_seconds=60))
    except RuntimeError:
        meta_engine._running = True
        meta_engine._async_task = None
        # Fallback: start in thread for backward compatibility
        thread = threading.Thread(target=meta_engine._monitoring_loop_sync, name="meta-engine-fallback", daemon=True)
        thread.start()

    logger.info("KANYOZA META ENGINE v%s — supervising %d engines", meta_engine.VERSION, len(meta_engine._engines))
    return meta_engine


def _monitoring_loop_sync(self) -> None:
    """Fallback sync monitoring loop for backward compatibility."""
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


# Add sync fallback method to class
MetaEngineV7._monitoring_loop_sync = _monitoring_loop_sync


def shutdown_meta_engine() -> None:
    """Shutdown the Meta Engine."""
    try:
        meta_engine.stop()
    except Exception:
        pass


atexit.register(shutdown_meta_engine)


__all__ = [
    "MetaEngineV7",
    "meta_engine",
    "init_meta_engine",
    "shutdown_meta_engine",
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