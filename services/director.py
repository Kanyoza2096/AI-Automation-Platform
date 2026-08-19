"""
KANYOZA DIRECTOR v4.0 — "THE AUTONOMOUS CEO"
COMPLETE SELF-CONTAINED VERSION

Single-file autonomous observation, diagnosis, decision, approval,
execution, learning, briefing, and notification engine.

This is the COMPLETE version — no external director imports.
Everything is self-contained.

v4.0 CAPABILITIES:
  - System Topology Model (dependency graph)
  - Root Cause Analysis (cascading failure tracing)
  - Impact Assessment (blast radius calculation)
  - Cascading Failure Prediction (anticipates downstream failures)
  - Correlated Recovery (fixes root cause, not symptoms)
  - Learning Loop (adapts based on outcomes)
  - Adaptive Thresholds (self-tuning decision criteria)
  - Predictive Failure Detection (trend-based anticipation)
  - Resource Throttling (intelligent execution limits)
  - Async Event Queue (non-blocking)
  - Circuit Breaker (failure isolation)
  - Health Monitoring (all platform engines)
  - Daily Briefings (Telegram notifications)
  - Full API (authenticated endpoints)
  - CLI Operation (standalone)

ARCHITECTURE:
  - Zero-trust security model
  - Thread-safe operations
  - Bounded memory usage
  - Graceful degradation
  - Fail-safe defaults
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import inspect
import json
import logging
import os
import random
import re
import signal
import threading
import time
import traceback
import uuid
from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from statistics import mean
from typing import (
    Any, Callable, Dict, List, Optional, Set, Tuple, TypeVar,
)

# At the top of director.py, after existing imports:

try:
    from plugins.council.personas import get_council
    HAS_COUNCIL = True
except ImportError:
    HAS_COUNCIL = False


# In Director.__init__, after self.adaptive_thresholds initialization:

        # Council integration (v4.1.0)
        self.council = get_council() if HAS_COUNCIL else None
        if self.council:
            logger.info(
                "👔 Director v%s — Community Council activated (%d members)",
                self.VERSION,
                len(self.council.members),
            )


# In Director class, add these methods (before execute method):

    def _run_council_vote(self, task: Task) -> Dict:
        """Run community council vote on a task."""
        if not self.council:
            return {"decision": "APPROVED", "reason": "No council"}
        
        brand_context = {
            "industry": task.params.get("industry", "general"),
            "brand_id": task.params.get("brand_id", ""),
            "task_sector": task.sector.value,
        }
        
        proposal = {
            "topic": task.description,
            "content": task.description,
            "hour": datetime.now().hour,
            "day": datetime.now().strftime("%A").lower(),
            "engagement_ratio": task.confidence,
            "sample_count": int(task.confidence * 30),
            "has_cta": "boost" in task.capability or "call" in task.description.lower(),
            "system_health": self.health()["score"],
            "engagement_trend": 0.0,
            "topic_source": "trend" if "trend" in task.reason.lower() else "director",
        }
        
        vote_result = self.council.vote_on(brand_context, proposal)
        summary = self.council.get_summary(vote_result)
        logger.info("👔 Council vote for '%s':\n%s", task.description[:50], summary)
        
        _publish_event("council_voted", {
            "task_id": task.id,
            "decision": vote_result["decision"],
            "approve_ratio": vote_result["approve_ratio"],
        })
        
        return vote_result


# In Director.execute method, after the requires_approval check and BEFORE setting status to EXECUTING:

            # Council vote (v4.1.0)
            if self.council and task.risk in (Risk.LOW, Risk.MEDIUM):
                vote_result = self._run_council_vote(task)
                
                if vote_result["decision"] == "REJECTED":
                    task.status = Status.CANCELLED
                    task.error = f"Rejected by council: {vote_result['reason']}"
                    self.store.save_task(task)
                    return task
                
                if vote_result["decision"] == "NEEDS_REVISION":
                    task.status = Status.FLAGGED
                    task.reason += f" (Council: {vote_result['approve_ratio']:.0%} approval)"
                    self.store.save_task(task)
                    return task

# ═══════════════════════════════════════════════════════════════════════════
# EVENT BUS INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════

try:
    from core.shared_event_bus import get_shared_event_bus
    HAS_EVENT_BUS = True
except ImportError:
    HAS_EVENT_BUS = False
    log = logging.getLogger("kanyoza.director")
    log.warning("Shared event bus not available — events will be skipped")

# ═══════════════════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=os.getenv("DIRECTOR_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("kanyoza.director")

T = TypeVar("T")


# ═══════════════════════════════════════════════════════════════════════════
# UTILITIES
# ═══════════════════════════════════════════════════════════════════════════

def now() -> datetime:
    """Current UTC time."""
    return datetime.now(timezone.utc)


def utcnow() -> datetime:
    """Current UTC time (alias)."""
    return datetime.now(timezone.utc)


def iso_now() -> str:
    """Current UTC time as ISO string."""
    return utcnow().isoformat()


def iso(value) -> Optional[str]:
    """Convert value to ISO string."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def safe(value: Any) -> Any:
    """Convert value to JSON-safe representation."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, deque)):
        return [safe(v) for v in value]
    return str(value)


def clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    """Clamp value to range."""
    return max(lo, min(hi, v))


def sanitize_error_message(exc: Exception, max_len: int = 300) -> str:
    """Sanitize exception messages to prevent internal path leakage."""
    raw = str(exc)
    raw = re.sub(r'File "[^"]*"', "File [redacted]", raw)
    raw = re.sub(r"/[^\s]*\.py", "[path_redacted]", raw)
    raw = re.sub(r"0x[0-9a-fA-F]+", "[addr]", raw)
    return raw[:max_len]


def _publish_event(event_name: str, payload: Dict[str, Any]) -> None:
    """Safely publish event to shared bus with defensive error handling."""
    if not HAS_EVENT_BUS:
        return
    try:
        bus = get_shared_event_bus()
        bus.publish(event_name, payload)
    except Exception:
        # Never let event publishing break core logic
        pass


# ═══════════════════════════════════════════════════════════════════════════
# ERROR CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════════════

class SupabaseErrorClassifier:
    """Classifies Supabase errors for retry decisions."""

    @staticmethod
    def is_retryable(error: Exception) -> bool:
        status = SupabaseErrorClassifier._extract_status(error)
        if status == 429:
            return True
        if status and 400 <= status < 500:
            return False
        if status and status >= 500:
            return True
        error_str = str(error).lower()
        if any(
            x in error_str
            for x in (
                'timeout', 'connection', 'network', 'dns',
                'reset', 'refused', 'unreachable', 'temporary',
            )
        ):
            return True
        return False

    @staticmethod
    def is_permanent(error: Exception) -> bool:
        status = SupabaseErrorClassifier._extract_status(error)
        return (
            status is not None
            and 400 <= status < 500
            and status != 429
        )

    @staticmethod
    def _extract_status(error: Exception) -> Optional[int]:
        if hasattr(error, 'code'):
            code = getattr(error, 'code', None)
            if isinstance(code, int):
                return code
            if isinstance(code, str) and code.isdigit():
                return int(code)
        if (
            hasattr(error, 'response')
            and hasattr(error.response, 'status_code')
        ):
            return error.response.status_code
        if hasattr(error, 'status_code'):
            return error.status_code
        return None

    @staticmethod
    def describe(error: Exception) -> str:
        status = SupabaseErrorClassifier._extract_status(error)
        if status == 403:
            return "403 Forbidden — Use service_role key"
        if status == 401:
            return "401 Unauthorized"
        if status == 404:
            return "404 Not Found"
        if status == 429:
            return "429 Rate Limited"
        if status and status >= 500:
            return f"{status} Server Error"
        return sanitize_error_message(error)


# ═══════════════════════════════════════════════════════════════════════════
# CIRCUIT BREAKER
# ═══════════════════════════════════════════════════════════════════════════

class CircuitBreaker:
    """Circuit breaker for failure isolation."""

    def __init__(
        self,
        name: str = "default",
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._state = "CLOSED"
        self._lock = threading.RLock()

    @property
    def is_open(self) -> bool:
        with self._lock:
            if self._state == "CLOSED":
                return False
            if self._state == "OPEN":
                if (
                    time.monotonic() - self._last_failure_time
                    >= self.recovery_timeout
                ):
                    self._state = "HALF_OPEN"
                    log.info(
                        "Circuit breaker '%s': OPEN → HALF_OPEN",
                        self.name,
                    )
                    return False
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            if self._state == "HALF_OPEN":
                self._state = "CLOSED"
                self._failure_count = 0
                log.info(
                    "Circuit breaker '%s': HALF_OPEN → CLOSED",
                    self.name,
                )
            elif self._state == "CLOSED":
                self._failure_count = 0

    def record_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            if self._state == "HALF_OPEN":
                self._state = "OPEN"
                log.warning(
                    "Circuit breaker '%s': HALF_OPEN → OPEN",
                    self.name,
                )
            elif (
                self._failure_count >= self.failure_threshold
                and self._state == "CLOSED"
            ):
                self._state = "OPEN"
                log.error(
                    "Circuit breaker '%s': CLOSED → OPEN (%d failures)",
                    self.name,
                    self._failure_count,
                )

    @property
    def state(self) -> str:
        with self._lock:
            return self._state


# ═══════════════════════════════════════════════════════════════════════════
# ENUMS
# ═══════════════════════════════════════════════════════════════════════════

class Authority(str, Enum):
    AUTO = "auto"
    NOTIFY = "notify"
    SUGGEST = "suggest"
    ALERT = "alert"


class Sector(str, Enum):
    CONTENT = "content"
    BRANDS = "brands"
    HEALTH = "health"
    SECURITY = "security"
    FINANCE = "finance"
    GROWTH = "growth"
    LEARNING = "learning"
    CLIENTS = "clients"
    INFRA = "infra"
    SELF = "self_improvement"


class Status(str, Enum):
    PLANNED = "planned"
    APPROVAL = "approval_required"
    APPROVED = "approved"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    FLAGGED = "flagged"
    CANCELLED = "cancelled"


class Health(str, Enum):
    NOMINAL = "nominal"
    DEGRADED = "degraded"
    CRITICAL = "critical"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


class Risk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Briefing(str, Enum):
    MORNING = "morning"
    CHECKPOINT = "checkpoint"
    EVENING = "evening"
    ALERT = "alert"
    WEEKLY = "weekly"


# ═══════════════════════════════════════════════════════════════════════════
# DATA MODELS
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Capability:
    name: str
    sector: Sector
    description: str
    handler: Callable[[Dict[str, Any]], Any]
    authority: Authority = Authority.SUGGEST
    risk: Risk = Risk.MEDIUM
    enabled: bool = True


@dataclass
class Observation:
    source: str
    sector: Sector
    status: Health = Health.UNKNOWN
    metrics: Dict[str, Any] = field(default_factory=dict)
    issues: List[Dict[str, Any]] = field(default_factory=list)
    message: str = ""
    timestamp: datetime = field(default_factory=now)


@dataclass
class Task:
    id: str
    sector: Sector
    description: str
    capability: str
    authority: Authority
    risk: Risk
    params: Dict[str, Any] = field(default_factory=dict)
    status: Status = Status.PLANNED
    created_at: datetime = field(default_factory=now)
    executed_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    approved_by: Optional[str] = None
    result: Any = None
    error: Optional[str] = None
    confidence: float = 0.8
    reason: str = ""
    sources: List[str] = field(default_factory=list)


@dataclass
class Decision:
    id: str
    timestamp: datetime
    decision: str
    reason: str
    confidence: float
    sector: Sector
    authority: Authority
    risk: Risk
    task_id: Optional[str] = None
    outcome: Optional[str] = None


@dataclass
class Goal:
    id: str
    name: str
    description: str
    sector: Sector
    priority: int = 50
    enabled: bool = True


@dataclass
class StrategicOutlook:
    horizon_days: int
    predicted_health_score: float
    predicted_task_volume: int
    emerging_risks: List[str] = field(default_factory=list)
    growth_opportunities: List[str] = field(default_factory=list)
    recommended_focus_areas: List[str] = field(default_factory=list)
    confidence: float = 0.5
    generated_at: datetime = field(default_factory=utcnow)


@dataclass
class ResourceAllocation:
    sector: Sector
    current_health: float
    importance_weight: float
    recommended_attention_pct: float
    reasoning: str


@dataclass
class DependencyNode:
    name: str
    sector: Sector
    depends_on: Set[str] = field(default_factory=set)
    depended_by: Set[str] = field(default_factory=set)
    criticality: int = 1
    last_health: Health = Health.UNKNOWN
    last_updated: datetime = field(default_factory=now)


@dataclass
class RootCauseAnalysis:
    root_cause: str
    affected_services: List[str]
    confidence: float
    evidence: List[Dict[str, Any]]
    timestamp: datetime
    recommended_action: str
    analysis_id: str = field(
        default_factory=lambda: f"rca_{uuid.uuid4().hex[:12]}"
    )


@dataclass
class LearningSignal:
    capability: str
    sector: Sector
    outcome: str
    duration_ms: float
    error_type: Optional[str]
    context: Dict[str, Any]
    timestamp: datetime


# ═══════════════════════════════════════════════════════════════════════════
# SUPABASE STORE
# ═══════════════════════════════════════════════════════════════════════════

class SupabaseStore:
    """PostgreSQL-backed store via Supabase."""

    def __init__(self, supabase_client):
        self.supabase = supabase_client
        self.circuit_breaker = CircuitBreaker(name="supabase_store")
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        if self.circuit_breaker.is_open:
            return
        try:
            self.supabase.rpc("director_ensure_tables", {}).execute()
            self.circuit_breaker.record_success()
        except Exception as exc:
            self.circuit_breaker.record_failure()
            log.warning(
                "Supabase table check failed: %s",
                sanitize_error_message(exc),
            )

    def _execute_with_retry(
        self,
        operation_name: str,
        fn: Callable[[], T],
        max_retries: int = 2,
    ) -> Optional[T]:
        if self.circuit_breaker.is_open:
            return None
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                result = fn()
                self.circuit_breaker.record_success()
                return result
            except Exception as exc:
                last_error = exc
                self.circuit_breaker.record_failure()
                status = SupabaseErrorClassifier._extract_status(exc)
                if SupabaseErrorClassifier.is_permanent(exc):
                    log.error(
                        "Director DB: '%s' FAILED (HTTP %s, permanent): %s",
                        operation_name,
                        status,
                        SupabaseErrorClassifier.describe(exc),
                    )
                    return None
                if attempt >= max_retries:
                    log.error(
                        "Director DB: '%s' FAILED after %d retries (HTTP %s)",
                        operation_name,
                        max_retries,
                        status,
                    )
                    return None
                delay = min(1.0 * (2 ** attempt), 10.0)
                delay *= random.uniform(0.75, 1.25)
                log.warning(
                    "Director DB: '%s' attempt %d/%d failed (HTTP %s). "
                    "Retrying in %.1fs...",
                    operation_name,
                    attempt + 1,
                    max_retries,
                    status,
                    delay,
                )
                time.sleep(delay)
        return None

    def _table(self, name: str):
        return self.supabase.table(name)

    def _insert(self, table: str, data: dict) -> None:
        self._execute_with_retry(
            f"insert into {table}",
            lambda: self._table(table).upsert(data).execute(),
            max_retries=2,
        )

    def _get(
        self, table: str, key: str, value: str
    ) -> Optional[dict]:
        result = self._execute_with_retry(
            f"get from {table}",
            lambda: self._table(table)
            .select("*")
            .eq(key, value)
            .limit(1)
            .execute(),
            max_retries=1,
        )
        return (
            dict(result.data[0])
            if result and result.data and len(result.data) > 0
            else None
        )

    def _list(
        self, table: str, limit: int = 100
    ) -> List[dict]:
        result = self._execute_with_retry(
            f"list from {table}",
            lambda: self._table(table)
            .select("*")
            .order("created_at", desc=True)
            .limit(limit)
            .execute(),
            max_retries=1,
        )
        return (
            [dict(row) for row in (result.data or [])]
            if result
            else []
        )

    def save_task(self, task: Task) -> None:
        self._insert(
            "director_tasks",
            {
                "id": task.id,
                "payload": json.dumps(safe(asdict(task))),
                "created_at": iso(task.created_at),
                "status": task.status.value,
            },
        )

    def get_task(self, task_id: str) -> Optional[dict]:
        return self._get("director_tasks", "id", task_id)

    def list_tasks(self) -> List[dict]:
        return self._list("director_tasks", 200)

    def save_decision(self, decision: Decision) -> None:
        self._insert(
            "director_decisions",
            {
                "id": decision.id,
                "payload": json.dumps(safe(asdict(decision))),
                "timestamp": iso(decision.timestamp),
            },
        )

    def list_decisions(self, limit: int = 100) -> List[dict]:
        return self._list("director_decisions", limit)

    def save_observation(self, observation: Observation) -> None:
        self._insert(
            "director_observations",
            {
                "payload": json.dumps(safe(asdict(observation))),
                "timestamp": iso(observation.timestamp),
            },
        )

    def list_observations(self, limit: int = 100) -> List[dict]:
        return self._list("director_observations", limit)

    def save_approval(
        self, task_id: str, approved: bool, actor: str
    ) -> None:
        self._insert(
            "director_approvals",
            {
                "task_id": task_id,
                "approved": approved,
                "actor": actor,
                "timestamp": iso_now(),
            },
        )

    def get_approval(self, task_id: str) -> Optional[dict]:
        return self._get("director_approvals", "task_id", task_id)

    def save_learning(self, entry: dict) -> None:
        self._insert(
            "director_learnings",
            {
                "id": entry.get(
                    "id", f"learn_{uuid.uuid4().hex[:12]}"
                ),
                "payload": json.dumps(safe(entry)),
                "timestamp": iso_now(),
            },
        )

    def list_learnings(self, limit: int = 100) -> List[dict]:
        return self._list("director_learnings", limit)


# ═══════════════════════════════════════════════════════════════════════════
# IN-MEMORY STORE
# ═══════════════════════════════════════════════════════════════════════════

class InMemoryStore:
    """Bounded in-memory store for when Supabase is unavailable."""

    MAX_TASKS = 500
    MAX_DECISIONS = 200
    MAX_OBSERVATIONS = 500
    MAX_APPROVALS = 200
    MAX_LEARNINGS = 500

    def __init__(self):
        self.tasks: Dict[str, dict] = {}
        self.decisions: deque = deque(maxlen=self.MAX_DECISIONS)
        self.observations: deque = deque(maxlen=self.MAX_OBSERVATIONS)
        self.approvals: Dict[str, dict] = {}
        self.learnings: deque = deque(maxlen=self.MAX_LEARNINGS)
        self._lock = threading.RLock()

    def save_task(self, task: Task) -> None:
        with self._lock:
            if len(self.tasks) >= self.MAX_TASKS:
                completed = [
                    tid for tid, t in self.tasks.items()
                    if t.get("status") in ("completed", "failed", "cancelled")
                ]
                for tid in completed[:100]:
                    del self.tasks[tid]
            self.tasks[task.id] = safe(asdict(task))

    def get_task(self, task_id: str) -> Optional[dict]:
        return self.tasks.get(task_id)

    def list_tasks(self) -> List[dict]:
        return list(self.tasks.values())

    def save_decision(self, decision: Decision) -> None:
        self.decisions.append(safe(asdict(decision)))

    def list_decisions(self, limit: int = 100) -> List[dict]:
        return list(self.decisions)[-limit:]

    def save_observation(self, observation: Observation) -> None:
        self.observations.append(safe(asdict(observation)))

    def list_observations(self, limit: int = 100) -> List[dict]:
        return list(self.observations)[-limit:]

    def save_approval(
        self, task_id: str, approved: bool, actor: str
    ) -> None:
        with self._lock:
            if len(self.approvals) >= self.MAX_APPROVALS:
                oldest = list(self.approvals.keys())[:100]
                for key in oldest:
                    del self.approvals[key]
            self.approvals[task_id] = {
                "task_id": task_id,
                "approved": approved,
                "actor": actor,
                "timestamp": iso_now(),
            }

    def get_approval(self, task_id: str) -> Optional[dict]:
        return self.approvals.get(task_id)

    def save_learning(self, entry: dict) -> None:
        self.learnings.append(entry)

    def list_learnings(self, limit: int = 100) -> List[dict]:
        return list(self.learnings)[-limit:]


# ═══════════════════════════════════════════════════════════════════════════
# POLICY ENGINE
# ═══════════════════════════════════════════════════════════════════════════

class Policy:
    """Security policy for autonomous actions."""

    CRITICAL = {
        "delete_brand",
        "delete_client_data",
        "drop_database",
        "reset_database",
        "rotate_credentials",
        "change_production_secrets",
        "disable_security_controls",
        "change_billing",
        "refund_client",
        "send_legal_notice",
        "deploy_breaking_change",
        "change_access_control",
    }

    def authority(self, capability: Capability) -> Authority:
        if not capability.enabled:
            return Authority.ALERT
        if (
            capability.name in self.CRITICAL
            or capability.risk == Risk.CRITICAL
        ):
            return Authority.SUGGEST
        return capability.authority

    def requires_approval(self, capability: Capability) -> bool:
        return (
            capability.name in self.CRITICAL
            or capability.risk == Risk.CRITICAL
            or self.authority(capability)
            in (Authority.SUGGEST, Authority.ALERT)
        )


# ═══════════════════════════════════════════════════════════════════════════
# SYSTEM TOPOLOGY
# ═══════════════════════════════════════════════════════════════════════════

class SystemTopology:
    """Dependency graph of all platform components."""

    def __init__(self):
        self.nodes: Dict[str, DependencyNode] = {}
        self._lock = threading.RLock()
        self._initialize_known_topology()

    def _initialize_known_topology(self) -> None:
        """Initialize the known dependency graph."""
        # Phase 0: INFRASTRUCTURE
        self.add_node("supabase", Sector.INFRA, criticality=5)
        self.add_node("redis", Sector.INFRA, criticality=5)
        self.add_node("event_bus", Sector.INFRA, criticality=5)

        # Phase 1: CORE
        self.add_node("task_queue", Sector.INFRA, criticality=4)
        self.add_dependency("task_queue", "redis")
        self.add_dependency("task_queue", "supabase")

        self.add_node("job_service", Sector.INFRA, criticality=4)
        self.add_dependency("job_service", "supabase")
        self.add_dependency("job_service", "event_bus")

        self.add_node("plugin_registry", Sector.INFRA, criticality=3)
        self.add_dependency("plugin_registry", "event_bus")

        self.add_node("render_queue", Sector.CONTENT, criticality=3)
        self.add_dependency("render_queue", "redis")

        self.add_node("event_recorder", Sector.INFRA, criticality=2)
        self.add_dependency("event_recorder", "event_bus")
        self.add_dependency("event_recorder", "supabase")

        # Phase 2: BUSINESS
        self.add_node("ai_orchestrator", Sector.HEALTH, criticality=4)
        self.add_dependency("ai_orchestrator", "supabase")

        self.add_node("content_engine", Sector.CONTENT, criticality=4)
        self.add_dependency("content_engine", "ai_orchestrator")
        self.add_dependency("content_engine", "event_bus")
        self.add_dependency("content_engine", "supabase")

        self.add_node("card_renderer", Sector.CONTENT, criticality=3)
        self.add_dependency("card_renderer", "render_queue")
        self.add_dependency("card_renderer", "content_engine")

        self.add_node("meta_engine", Sector.HEALTH, criticality=4)
        self.add_dependency("meta_engine", "event_bus")
        self.add_dependency("meta_engine", "supabase")

        self.add_node("condition_engine", Sector.LEARNING, criticality=4)
        self.add_dependency("condition_engine", "event_bus")
        self.add_dependency("condition_engine", "supabase")
        self.add_dependency("condition_engine", "content_engine")

        self.add_node("director", Sector.SELF, criticality=5)
        self.add_dependency("director", "event_bus")
        self.add_dependency("director", "supabase")

        self.add_node("brand_scheduler", Sector.CONTENT, criticality=3)
        self.add_dependency("brand_scheduler", "content_engine")
        self.add_dependency("brand_scheduler", "supabase")

        self.add_node("email_service", Sector.CLIENTS, criticality=3)
        self.add_dependency("email_service", "event_bus")

        self.add_node("engagement_tracker", Sector.GROWTH, criticality=3)
        self.add_dependency("engagement_tracker", "supabase")

        self.add_node("guardian", Sector.SECURITY, criticality=4)
        self.add_dependency("guardian", "supabase")

        self.add_node("guardian_scheduler", Sector.SECURITY, criticality=3)
        self.add_dependency("guardian_scheduler", "guardian")

        self.add_node("image_engine", Sector.CONTENT, criticality=3)
        self.add_dependency("image_engine", "render_queue")
        self.add_dependency("image_engine", "content_engine")

        self.add_node("topic_selector", Sector.CONTENT, criticality=2)
        self.add_dependency("topic_selector", "content_engine")

        self.add_node("prompt_builder", Sector.CONTENT, criticality=2)
        self.add_dependency("prompt_builder", "content_engine")

        # Phase 3: OBSERVABILITY
        self.add_node("health_monitor", Sector.HEALTH, criticality=3)
        self.add_dependency("health_monitor", "event_bus")

        self.add_node("enterprise_emitters", Sector.HEALTH, criticality=2)
        self.add_dependency("enterprise_emitters", "event_bus")

        # Plugins
        self.add_node("growthbridge_plugin", Sector.GROWTH, criticality=2)
        self.add_dependency("growthbridge_plugin", "content_engine")
        self.add_dependency("growthbridge_plugin", "supabase")

        self.add_node("monitor_plugin", Sector.HEALTH, criticality=2)
        self.add_dependency("monitor_plugin", "supabase")

        self.add_node("backup_plugin", Sector.INFRA, criticality=3)
        self.add_dependency("backup_plugin", "supabase")

        self.add_node("trend_plugin", Sector.GROWTH, criticality=2)
        self.add_dependency("trend_plugin", "supabase")

    def add_node(
        self, name: str, sector: Sector, criticality: int = 1
    ) -> None:
        with self._lock:
            if name not in self.nodes:
                self.nodes[name] = DependencyNode(
                    name=name,
                    sector=sector,
                    criticality=criticality,
                )

    def add_dependency(
        self, dependent: str, dependency: str
    ) -> None:
        with self._lock:
            self.add_node(dependent, Sector.INFRA)
            self.add_node(dependency, Sector.INFRA)
            self.nodes[dependent].depends_on.add(dependency)
            self.nodes[dependency].depended_by.add(dependent)

    def get_upstream(
        self, service: str, depth: int = 5
    ) -> Set[str]:
        with self._lock:
            upstream = set()
            queue = deque([(service, 0)])
            visited = set()
            while queue:
                current, current_depth = queue.popleft()
                if current in visited or current_depth > depth:
                    continue
                visited.add(current)
                node = self.nodes.get(current)
                if node:
                    for dep in node.depends_on:
                        upstream.add(dep)
                        queue.append((dep, current_depth + 1))
            return upstream

    def get_downstream(
        self, service: str, depth: int = 5
    ) -> Set[str]:
        with self._lock:
            downstream = set()
            queue = deque([(service, 0)])
            visited = set()
            while queue:
                current, current_depth = queue.popleft()
                if current in visited or current_depth > depth:
                    continue
                visited.add(current)
                node = self.nodes.get(current)
                if node:
                    for dep in node.depended_by:
                        downstream.add(dep)
                        queue.append((dep, current_depth + 1))
            return downstream

    def get_blast_radius(self, service: str) -> int:
        return len(self.get_downstream(service))

    def get_critical_downstream(
        self, service: str
    ) -> List[str]:
        downstream = self.get_downstream(service)
        return [
            s for s in downstream
            if self.nodes.get(
                s, DependencyNode(s, Sector.INFRA)
            ).criticality >= 4
        ]

    def update_health(
        self, service: str, health: Health
    ) -> None:
        with self._lock:
            node = self.nodes.get(service)
            if node:
                node.last_health = health
                node.last_updated = now()

    def get_service_sector(self, service: str) -> Sector:
        with self._lock:
            node = self.nodes.get(service)
            return node.sector if node else Sector.INFRA

    def get_service_criticality(self, service: str) -> int:
        with self._lock:
            node = self.nodes.get(service)
            return node.criticality if node else 1


# ═══════════════════════════════════════════════════════════════════════════
# ROOT CAUSE ANALYZER
# ═══════════════════════════════════════════════════════════════════════════

class RootCauseAnalyzer:
    """Traces cascading failures to their source."""

    def __init__(self, topology: SystemTopology):
        self.topology = topology
        self.analyses: deque = deque(maxlen=200)
        self._lock = threading.RLock()

    def analyze(
        self, observations: Dict[str, Observation]
    ) -> Optional[RootCauseAnalysis]:
        with self._lock:
            failing_services = [
                name
                for name, obs in observations.items()
                if obs.status in (Health.CRITICAL, Health.OFFLINE)
            ]
            degraded_services = [
                name
                for name, obs in observations.items()
                if obs.status == Health.DEGRADED
            ]

            if not failing_services and not degraded_services:
                return None

            if len(failing_services) == 1 and not degraded_services:
                service = failing_services[0]
                return self._create_analysis(
                    root_cause=service,
                    affected=[service],
                    confidence=0.9,
                    evidence=[{"type": "single_failure", "service": service}],
                    action=f"Restart or repair {service}",
                )

            all_failing = failing_services + degraded_services
            if len(all_failing) < 2:
                return None

            upstream_sets = []
            for service in all_failing:
                upstream = self.topology.get_upstream(service, depth=5)
                upstream_sets.append(upstream)

            common_upstream = (
                set.intersection(*upstream_sets)
                if upstream_sets
                else set()
            )

            failing_common = common_upstream & set(failing_services)
            if failing_common:
                root_cause = self._select_root_cause(failing_common)
                return self._create_analysis(
                    root_cause=root_cause,
                    affected=all_failing,
                    confidence=0.85,
                    evidence=[{
                        "type": "common_upstream_failure",
                        "upstream": root_cause,
                        "affected": all_failing,
                    }],
                    action=f"Fix root cause {root_cause}",
                )

            infra_deps = {"supabase", "redis", "event_bus"}
            failing_infra = infra_deps & set(failing_services)
            if failing_infra:
                root_cause = self._select_root_cause(failing_infra)
                return self._create_analysis(
                    root_cause=root_cause,
                    affected=all_failing,
                    confidence=0.9,
                    evidence=[{
                        "type": "infrastructure_failure",
                        "infrastructure": root_cause,
                        "affected": all_failing,
                    }],
                    action=f"Restore infrastructure: {root_cause}",
                )

            degraded_common = common_upstream & set(degraded_services)
            if degraded_common:
                root_cause = self._select_root_cause(degraded_common)
                return self._create_analysis(
                    root_cause=root_cause,
                    affected=all_failing,
                    confidence=0.7,
                    evidence=[{
                        "type": "degraded_upstream",
                        "upstream": root_cause,
                        "affected": all_failing,
                    }],
                    action=f"Stabilize {root_cause}",
                )

            root_cause = max(
                all_failing,
                key=lambda s: self.topology.get_service_criticality(s),
            )
            return self._create_analysis(
                root_cause=root_cause,
                affected=all_failing,
                confidence=0.5,
                evidence=[{
                    "type": "unclear_causality",
                    "failing_services": all_failing,
                }],
                action=f"Investigate {root_cause}",
            )

    def _select_root_cause(self, candidates: Set[str]) -> str:
        infra = {"supabase", "redis", "event_bus"}
        infra_candidates = candidates & infra
        if infra_candidates:
            return min(
                infra_candidates,
                key=lambda s: self.topology.get_blast_radius(s),
            )
        return max(
            candidates,
            key=lambda s: (
                self.topology.get_service_criticality(s),
                self.topology.get_blast_radius(s),
            ),
        )

    def _create_analysis(
        self,
        root_cause: str,
        affected: List[str],
        confidence: float,
        evidence: List[Dict[str, Any]],
        action: str,
    ) -> RootCauseAnalysis:
        analysis = RootCauseAnalysis(
            root_cause=root_cause,
            affected_services=affected,
            confidence=confidence,
            evidence=evidence,
            timestamp=now(),
            recommended_action=action,
        )
        self.analyses.append(analysis)
        return analysis

    def get_recent_analyses(
        self, limit: int = 10
    ) -> List[RootCauseAnalysis]:
        return list(self.analyses)[-limit:]


# ═══════════════════════════════════════════════════════════════════════════
# IMPACT ASSESSOR
# ═══════════════════════════════════════════════════════════════════════════

class ImpactAssessor:
    """Assesses blast radius and severity of failures."""

    def __init__(self, topology: SystemTopology):
        self.topology = topology
        self.impact_history: deque = deque(maxlen=200)
        self._lock = threading.RLock()

    def assess_impact(self, service: str) -> Dict[str, Any]:
        with self._lock:
            downstream = self.topology.get_downstream(service, depth=5)
            critical_downstream = self.topology.get_critical_downstream(service)
            severity = self._calculate_severity(
                len(downstream), len(critical_downstream)
            )
            impact = {
                "service": service,
                "blast_radius": len(downstream),
                "downstream_services": sorted(downstream),
                "critical_services_affected": sorted(critical_downstream),
                "severity": severity,
                "estimated_recovery_minutes": (
                    5 if severity == "low"
                    else 15 if severity == "medium"
                    else 30 if severity == "high"
                    else 60
                ),
                "timestamp": now(),
            }
            self.impact_history.append(impact)
            return impact

    def _calculate_severity(
        self, downstream_count: int, critical_count: int
    ) -> str:
        if critical_count >= 3 or downstream_count >= 10:
            return "critical"
        if critical_count >= 1 or downstream_count >= 5:
            return "high"
        if downstream_count >= 3:
            return "medium"
        return "low"


# ═══════════════════════════════════════════════════════════════════════════
# CASCADING FAILURE PREDICTOR
# ═══════════════════════════════════════════════════════════════════════════

class CascadingFailurePredictor:
    """Predicts which services will fail next."""

    def __init__(self, topology: SystemTopology):
        self.topology = topology
        self.predictions: deque = deque(maxlen=200)
        self._lock = threading.RLock()

    def predict_cascading_failures(
        self,
        failing_service: str,
        observations: Dict[str, Observation],
    ) -> List[Dict[str, Any]]:
        with self._lock:
            downstream = self.topology.get_downstream(failing_service, depth=3)
            predictions = []

            for service in downstream:
                obs = observations.get(service)
                if obs is None:
                    continue
                if obs.status == Health.NOMINAL:
                    predictions.append({
                        "service": service,
                        "current_status": "healthy",
                        "predicted_status": "degraded",
                        "time_to_failure_minutes": 5,
                        "confidence": 0.75,
                        "reason": f"Upstream {failing_service} failed",
                    })
                elif obs.status == Health.DEGRADED:
                    predictions.append({
                        "service": service,
                        "current_status": "degraded",
                        "predicted_status": "critical",
                        "time_to_failure_minutes": 10,
                        "confidence": 0.6,
                        "reason": f"Upstream {failing_service} failed",
                    })
                elif obs.status == Health.UNKNOWN:
                    predictions.append({
                        "service": service,
                        "current_status": "unknown",
                        "predicted_status": "likely_degraded",
                        "time_to_failure_minutes": 15,
                        "confidence": 0.5,
                        "reason": f"Depends on failed {failing_service}",
                    })

            self.predictions.extend(predictions)
            return predictions


# ═══════════════════════════════════════════════════════════════════════════
# CORRELATED RECOVERY
# ═══════════════════════════════════════════════════════════════════════════

class CorrelatedRecovery:
    """Coordinates recovery actions for correlated failures."""

    def __init__(self):
        self.recovery_plans: deque = deque(maxlen=100)
        self._lock = threading.RLock()

    def create_recovery_plan(
        self, root_cause: str, affected_services: List[str]
    ) -> Dict[str, Any]:
        with self._lock:
            plan = {
                "id": f"recovery_{uuid.uuid4().hex[:12]}",
                "root_cause": root_cause,
                "affected_services": affected_services,
                "steps": [
                    {
                        "order": 1,
                        "action": f"Restore {root_cause}",
                        "type": "root_cause_fix",
                        "status": "pending",
                    },
                    {
                        "order": 2,
                        "action": "Wait for stabilization",
                        "type": "stabilization",
                        "duration_seconds": 30,
                        "status": "pending",
                    },
                    {
                        "order": 3,
                        "action": "Verify downstream recovery",
                        "type": "verification",
                        "services": affected_services,
                        "status": "pending",
                    },
                    {
                        "order": 4,
                        "action": "Restart unrecovered services",
                        "type": "remediation",
                        "status": "pending",
                    },
                ],
                "created_at": now(),
                "status": "pending",
            }
            self.recovery_plans.append(plan)
            return plan

    def update_step_status(
        self, plan_id: str, step_order: int, status: str
    ) -> None:
        with self._lock:
            for plan in self.recovery_plans:
                if plan["id"] == plan_id:
                    for step in plan["steps"]:
                        if step["order"] == step_order:
                            step["status"] = status
                            break
                    break

    def get_recovery_plans(self, limit: int = 10) -> List[Dict[str, Any]]:
        return list(self.recovery_plans)[-limit:]


# ═══════════════════════════════════════════════════════════════════════════
# LEARNING LOOP
# ═══════════════════════════════════════════════════════════════════════════

class LearningLoop:
    """Feeds task outcomes back into decision-making."""

    def __init__(self, max_signals: int = 1000):
        self.signals: deque = deque(maxlen=max_signals)
        self.capability_stats: Dict[str, Dict[str, float]] = {}
        self.failure_patterns: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=50)
        )
        self._lock = threading.RLock()

    def record_outcome(self, task: Task, duration_ms: float) -> None:
        with self._lock:
            signal = LearningSignal(
                capability=task.capability,
                sector=task.sector,
                outcome=(
                    "success"
                    if task.status == Status.COMPLETED
                    else "failure"
                ),
                duration_ms=duration_ms,
                error_type=task.error[:100] if task.error else None,
                context={
                    "authority": task.authority.value,
                    "risk": task.risk.value,
                    "confidence": task.confidence,
                },
                timestamp=now(),
            )
            self.signals.append(signal)

            stats = self.capability_stats.setdefault(
                task.capability,
                {"successes": 0, "failures": 0, "total_duration_ms": 0},
            )
            stats["successes"] += 1 if signal.outcome == "success" else 0
            stats["failures"] += 0 if signal.outcome == "success" else 1
            stats["total_duration_ms"] += duration_ms

            if signal.outcome == "failure":
                self.failure_patterns[task.capability].append({
                    "timestamp": signal.timestamp,
                    "error": signal.error_type,
                })

    def get_success_rate(self, capability: str) -> Optional[float]:
        with self._lock:
            stats = self.capability_stats.get(capability)
            if not stats:
                return None
            total = stats["successes"] + stats["failures"]
            return stats["successes"] / total if total > 0 else None

    def get_recurring_failures(
        self, capability: str, threshold: int = 3
    ) -> bool:
        with self._lock:
            failures = self.failure_patterns.get(capability, [])
            return len(failures) >= threshold


# ═══════════════════════════════════════════════════════════════════════════
# ADAPTIVE THRESHOLDS
# ═══════════════════════════════════════════════════════════════════════════

class AdaptiveThresholds:
    """Self-tuning decision thresholds."""

    def __init__(self):
        self.thresholds: Dict[str, float] = {
            "health_scan": 90.0,
            "security_scan": 80.0,
            "brand_revival": 60.0,
            "content_optimization": 70.0,
        }
        self._lock = threading.RLock()

    def get_threshold(self, name: str) -> float:
        with self._lock:
            return self.thresholds.get(name, 80.0)

    def update_threshold(
        self, name: str, actual_value: float, outcome: str
    ) -> None:
        with self._lock:
            current = self.thresholds.get(name, 80.0)
            if outcome == "false_positive":
                self.thresholds[name] = clamp(
                    current - 5, lo=50.0, hi=95.0
                )
            elif outcome == "true_positive":
                self.thresholds[name] = clamp(
                    current + 2, lo=50.0, hi=95.0
                )
            elif outcome == "missed":
                self.thresholds[name] = clamp(
                    current + 5, lo=50.0, hi=95.0
                )


# ═══════════════════════════════════════════════════════════════════════════
# FUNCTION OBSERVER
# ═══════════════════════════════════════════════════════════════════════════

class FunctionObserver:
    def __init__(self, name: str, sector: Sector, fn: Callable):
        self.name = name
        self.sector = sector
        self.fn = fn

    def observe(self) -> Observation:
        try:
            result = self.fn() or {}
        except Exception as exc:
            return Observation(
                self.name,
                self.sector,
                Health.CRITICAL,
                issues=[{
                    "type": "observer_failure",
                    "error": sanitize_error_message(exc),
                }],
            )
        if isinstance(result, Observation):
            return result
        status_str = str(result.get("status", "unknown")).lower()
        status_map = {
            "ok": Health.NOMINAL,
            "healthy": Health.NOMINAL,
            "nominal": Health.NOMINAL,
            "degraded": Health.DEGRADED,
            "critical": Health.CRITICAL,
            "offline": Health.OFFLINE,
        }
        return Observation(
            self.name,
            self.sector,
            status_map.get(status_str, Health.UNKNOWN),
            result.get("metrics", {}),
            result.get("issues", []),
            result.get("message", ""),
        )


# ═══════════════════════════════════════════════════════════════════════════
# ENGINE AUTO-DISCOVERY
# ═══════════════════════════════════════════════════════════════════════════

ENGINE_REGISTRY = [
    ("services.content_engine", "content_engine", "content_engine", Sector.CONTENT),
    ("services.card_renderer", "card_renderer", "card_renderer", Sector.CONTENT),
    ("services.condition_engine", "autonomous_engine", "condition_engine", Sector.LEARNING),
    ("services.email_service", "email_service", "email_service", Sector.CLIENTS),
    ("services.engagement_tracker", "engagement_tracker", "engagement_tracker", Sector.GROWTH),
    ("services.guardian_service", "guardian_service", "guardian", Sector.SECURITY),
    ("services.meta_engine", "meta_engine", "meta_engine", Sector.HEALTH),
    ("services.director", "director", "meta_director", Sector.SELF),
    ("ai.orchestrator", "AIOrchestrator", "ai_orchestrator", Sector.HEALTH),
    ("scheduler.brand_scheduler", "BrandSchedulerManager", "scheduler", Sector.CONTENT),
    ("services.job_stats_service", "get_job_stats_service", "job_service", Sector.INFRA),
    ("plugins.base", "PluginRegistry", "plugin_registry", Sector.INFRA),
    ("core.event_bus", "EventBus", "event_bus", Sector.INFRA),
    ("core.render_queue", "get_render_queue", "render_queue", Sector.CONTENT),
    ("services.image_engine", "generate_best_image", "image_engine", Sector.CONTENT),
    ("domain.content.topic_selector", "TopicSelector", "topic_selector", Sector.CONTENT),
    ("services.prompt_builder", "prompt_builder", "prompt_builder", Sector.CONTENT),
    ("services.health_monitor_service", "HealthMonitorService", "health_monitor", Sector.HEALTH),
]


def _make_observer(engine_name: str, engine_obj: Any) -> Callable:
    def observe() -> dict:
        if hasattr(engine_obj, "get_status"):
            try:
                r = engine_obj.get_status()
                if isinstance(r, dict):
                    return {
                        "status": r.get("overall", r.get("status", "healthy")),
                        "metrics": r,
                    }
            except Exception:
                pass
        if hasattr(engine_obj, "health_check"):
            try:
                r = engine_obj.health_check()
                if isinstance(r, dict):
                    return {"status": r.get("status", "healthy"), "metrics": r}
            except Exception:
                pass
        if hasattr(engine_obj, "get_stats"):
            try:
                r = engine_obj.get_stats()
                if isinstance(r, dict):
                    return {"status": "healthy", "metrics": r}
            except Exception:
                pass
        try:
            metrics = {}
            for attr in ("_decisions", "_strategies", "_observations", "_engines", "rules", "tasks"):
                if hasattr(engine_obj, attr):
                    val = getattr(engine_obj, attr)
                    metrics[attr] = len(val) if hasattr(val, "__len__") else 1
            if metrics:
                return {"status": "healthy", "metrics": metrics}
        except Exception:
            pass
        return {"status": "healthy", "metrics": {"engine": engine_name}}
    return observe


def auto_discover_engines(director: "Director", container) -> int:
    discovered = 0
    failed_loads = []
    for module_path, attr_name, display_name, sector in ENGINE_REGISTRY:
        try:
            module = __import__(module_path, fromlist=[attr_name])
            engine = getattr(module, attr_name, None)
            if engine is not None:
                if callable(engine) and not isinstance(engine, type):
                    try:
                        engine = engine()
                    except Exception:
                        pass
                director.register_observer(
                    FunctionObserver(display_name, sector, _make_observer(display_name, engine))
                )
                discovered += 1
        except ImportError:
            log.debug("👔 Director: %s not available (%s)", display_name, module_path)
        except Exception as exc:
            failed_loads.append(f"{display_name}: {sanitize_error_message(exc)}")
            log.warning("👔 Director: %s failed to load: %s", display_name, sanitize_error_message(exc))
    plugins = getattr(container, "industry_plugins", {})
    for name, plugin in plugins.items():
        sector = _infer_plugin_sector(name)
        director.register_observer(FunctionObserver(name, sector, _make_observer(name, plugin)))
        discovered += 1
    if failed_loads:
        log.warning("👔 Director: %d engine(s) failed: %s", len(failed_loads), ", ".join(failed_loads[:5]))
    log.info("👔 Director: Auto-discovered %d engines (%d core + %d plugins)", discovered, discovered - len(plugins), len(plugins))
    return discovered


def _infer_plugin_sector(name: str) -> Sector:
    name_lower = name.lower()
    if any(kw in name_lower for kw in ("church", "school")):
        return Sector.BRANDS
    if any(kw in name_lower for kw in ("hospital", "clinic", "health")):
        return Sector.HEALTH
    if any(kw in name_lower for kw in ("crm", "client", "customer")):
        return Sector.CLIENTS
    return Sector.INFRA


# ═══════════════════════════════════════════════════════════════════════════
# THE DIRECTOR v4.0 — COMPLETE AUTONOMOUS CEO
# ═══════════════════════════════════════════════════════════════════════════

class Director:
    """The Autonomous CEO — complete self-contained version."""

    VERSION = "4.0.1"

    def __init__(self, supabase_client=None, interval: int = 60):
        # Storage
        self.store = (
            SupabaseStore(supabase_client)
            if supabase_client
            else InMemoryStore()
        )
        if supabase_client:
            log.info("Director storage: Supabase (PostgreSQL)")
        else:
            log.warning("Director storage: In-Memory (Supabase not configured)")

        # Timing
        self.interval = max(10, interval) + random.uniform(0, 15)

        # Core
        self.policy = Policy()
        self.lock = threading.RLock()
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.started = now()

        # Registries
        self.observers: Dict[str, FunctionObserver] = {}
        self.capabilities: Dict[str, Capability] = {}
        self.tasks: Dict[str, Task] = {}
        self.goals: Dict[str, Goal] = {}

        # History
        self.history: deque = deque(maxlen=5000)
        self.decisions: deque = deque(maxlen=2000)
        self.observations: Dict[str, Observation] = {}
        self.briefings: deque = deque(maxlen=200)

        # State
        self.last_plan: Optional[datetime] = None
        self.last_observation: Optional[datetime] = None
        self.alert_cb: Optional[Callable] = None

        # Event queue
        self._event_queue: deque = deque(maxlen=2000)
        self._event_thread: Optional[threading.Thread] = None
        self._stored_event_cb: Optional[Callable] = None

        # Alert history
        self._last_daily_briefing: Optional[str] = None
        self._alert_history: deque = deque(maxlen=500)

        # Circuit breaker state
        self._consecutive_failures: int = 0
        self._observation_circuit_open: bool = False
        self._observation_circuit_reset_time: float = 0.0

        # Thread safety
        self._observations_lock = threading.RLock()
        self._execution_lock = threading.RLock()
        self._executing_tasks: Set[str] = set()

        # Diagnostic layer
        self.topology = SystemTopology()
        self.root_cause_analyzer = RootCauseAnalyzer(self.topology)
        self.impact_assessor = ImpactAssessor(self.topology)
        self.cascading_predictor = CascadingFailurePredictor(self.topology)
        self.recovery_coordinator = CorrelatedRecovery()

        # Intelligence layer
        self.learning_loop = LearningLoop()
        self.adaptive_thresholds = AdaptiveThresholds()

        # Current diagnostic state
        self.current_root_cause: Optional[RootCauseAnalysis] = None
        self.current_impact: Dict[str, Any] = {}
        self.current_predictions: List[Dict[str, Any]] = []

        # Initialize
        self._init_goals()
        self._init_capabilities()
        log.info("👔 KANYOZA DIRECTOR v%s initialized — COMPLETE AUTONOMOUS CEO", self.VERSION)

    # ═══════════════════════════════════════════════════════════════════
    # INITIALIZATION
    # ═══════════════════════════════════════════════════════════════════

    def _init_goals(self) -> None:
        for gid, name, sector, pri in [
            ("system_stability", "System stability", Sector.HEALTH, 100),
            ("security", "Security posture", Sector.SECURITY, 100),
            ("clients", "Client commitments", Sector.CLIENTS, 90),
            ("brands", "Brand health", Sector.BRANDS, 85),
            ("content", "Content consistency", Sector.CONTENT, 80),
            ("growth", "Business growth", Sector.GROWTH, 70),
            ("learning", "Continuous learning", Sector.LEARNING, 60),
        ]:
            self.goals[gid] = Goal(gid, name, name, sector, pri)

    def _init_capabilities(self) -> None:
        for name, sector, desc, auth, risk in [
            ("publish_scheduled_content", Sector.CONTENT, "Publish scheduled content", Authority.AUTO, Risk.LOW),
            ("optimize_content_schedule", Sector.CONTENT, "Adjust safe scheduling", Authority.NOTIFY, Risk.LOW),
            ("calculate_brand_health", Sector.BRANDS, "Calculate brand health", Authority.AUTO, Risk.LOW),
            ("queue_brand_revival", Sector.BRANDS, "Queue bounded revival", Authority.NOTIFY, Risk.MEDIUM),
            ("run_health_scan", Sector.HEALTH, "Run platform health scan", Authority.AUTO, Risk.LOW),
            ("restart_failed_service", Sector.HEALTH, "Restart isolated failed service", Authority.AUTO, Risk.MEDIUM),
            ("run_guardian_scan", Sector.SECURITY, "Run security scan", Authority.AUTO, Risk.LOW),
            ("quarantine_security_issue", Sector.SECURITY, "Quarantine confirmed issue", Authority.NOTIFY, Risk.HIGH),
            ("prepare_security_remediation", Sector.SECURITY, "Prepare security remediation", Authority.SUGGEST, Risk.HIGH),
            ("generate_client_report", Sector.CLIENTS, "Generate client report", Authority.AUTO, Risk.LOW),
            ("prepare_client_message", Sector.CLIENTS, "Prepare client message", Authority.SUGGEST, Risk.MEDIUM),
            ("analyze_growth_opportunity", Sector.GROWTH, "Analyze growth", Authority.AUTO, Risk.LOW),
            ("optimize_infrastructure", Sector.INFRA, "Prepare infrastructure optimization", Authority.SUGGEST, Risk.HIGH),
            ("run_learning_cycle", Sector.LEARNING, "Learn from outcomes", Authority.AUTO, Risk.LOW),
            ("self_diagnostic", Sector.SELF, "Diagnose Director", Authority.AUTO, Risk.LOW),
        ]:
            self.register_capability(Capability(name, sector, desc, self._placeholder(name), auth, risk))

        for name, sector in [
            ("delete_brand", Sector.BRANDS),
            ("delete_client_data", Sector.CLIENTS),
            ("drop_database", Sector.INFRA),
            ("reset_database", Sector.INFRA),
            ("rotate_credentials", Sector.SECURITY),
            ("change_production_secrets", Sector.SECURITY),
            ("disable_security_controls", Sector.SECURITY),
            ("change_billing", Sector.FINANCE),
            ("refund_client", Sector.FINANCE),
            ("send_legal_notice", Sector.CLIENTS),
            ("deploy_breaking_change", Sector.INFRA),
            ("change_access_control", Sector.SECURITY),
        ]:
            self.register_capability(
                Capability(name, sector, name.replace("_", " ").title(), self._blocked, Authority.SUGGEST, Risk.CRITICAL)
            )

    def _placeholder(self, name: str) -> Callable:
        return lambda p: {
            "status": "delegated",
            "capability": name,
            "message": "Bind the real Kanyoza service handler before production execution.",
            "params": p,
        }

    def _blocked(self, p: dict) -> dict:
        return {"status": "blocked", "reason": "Critical action requires human approval and a bound production handler."}

    # ═══════════════════════════════════════════════════════════════════
    # REGISTRATION
    # ═══════════════════════════════════════════════════════════════════

    def bind_capability(self, name: str, handler: Callable) -> None:
        if name not in self.capabilities:
            raise ValueError(f"Unknown capability: {name}")
        self.capabilities[name].handler = handler
        log.info("Director: capability '%s' bound to real handler", name)

    def register_observer(self, observer: FunctionObserver) -> None:
        self.observers[observer.name] = observer

    def register_capability(self, capability: Capability) -> None:
        self.capabilities[capability.name] = capability

    def set_alert_callback(self, cb: Callable) -> None:
        self.alert_cb = cb

    def set_event_callback(self, cb: Callable) -> None:
        self._stored_event_cb = cb
        log.info("Director: event callback stored (async mode)")

    def enqueue_event(self, name: str, payload: dict) -> None:
        if len(self._event_queue) >= self._event_queue.maxlen:
            log.warning("Director: Event queue FULL — dropping oldest event")
        self._event_queue.append({
            "name": name,
            "payload": payload,
            "timestamp": time.monotonic(),
        })

    # ═══════════════════════════════════════════════════════════════════
    # OBSERVATION
    # ═══════════════════════════════════════════════════════════════════

    def observe_all(self) -> Dict[str, Observation]:
        new_observations = {}
        for name, observer in list(self.observers.items()):
            try:
                obs = observer.observe()
                new_observations[name] = obs
                self.store.save_observation(obs)
            except Exception as exc:
                log.warning("Observer '%s' failed: %s", name, sanitize_error_message(exc))
                new_observations[name] = Observation(
                    name, Sector.INFRA, Health.DEGRADED,
                    issues=[{"type": "observer_error", "error": sanitize_error_message(exc)}],
                )

        with self._observations_lock:
            self.observations = new_observations

        self.last_observation = now()

        # Update topology
        for name, obs in new_observations.items():
            self.topology.update_health(name, obs.status)

        # Run root cause analysis
        self._run_diagnosis(new_observations)

        self._check_critical_and_alert()
        return self.observations

    def _run_diagnosis(self, observations: Dict[str, Observation]) -> None:
        """Run full diagnostic analysis."""
        root_cause = self.root_cause_analyzer.analyze(observations)
        if root_cause:
            self.current_root_cause = root_cause
            self.current_impact = self.impact_assessor.assess_impact(root_cause.root_cause)
            self.current_predictions = self.cascading_predictor.predict_cascading_failures(
                root_cause.root_cause, observations
            )

            log.warning(
                "🔍 ROOT CAUSE: %s (affects %d, confidence %.0f%%)",
                root_cause.root_cause,
                len(root_cause.affected_services),
                root_cause.confidence * 100,
            )

            # Create recovery plan
            plan = self.recovery_coordinator.create_recovery_plan(
                root_cause.root_cause, root_cause.affected_services
            )
            log.info("📋 RECOVERY PLAN: %s", plan["id"])

            # Create root cause fix task
            self._create_task(
                Sector.HEALTH,
                f"Fix root cause: {root_cause.root_cause}",
                "restart_failed_service",
                f"Root cause: {root_cause.root_cause} affects {len(root_cause.affected_services)} services",
                0.95,
                {
                    "service_name": root_cause.root_cause,
                    "affected_services": root_cause.affected_services,
                    "recovery_plan_id": plan["id"],
                },
                root_cause.root_cause,
            )

            # Send diagnostic alert
            self._send_diagnostic_alert()

    def _send_diagnostic_alert(self) -> None:
        if not self.current_root_cause:
            return
        try:
            rca = self.current_root_cause
            impact = self.current_impact
            predictions = self.current_predictions

            parts = [
                f"🔍 <b>DIAGNOSTIC ALERT</b>",
                f"━━━━━━━━━━━━━━━━━━━",
                f"<b>Root Cause:</b> {rca.root_cause}",
                f"<b>Confidence:</b> {rca.confidence:.0%}",
                f"<b>Affected:</b> {len(rca.affected_services)} services",
                f"<b>Blast Radius:</b> {impact.get('blast_radius', 0)} services",
                f"<b>Severity:</b> {impact.get('severity', 'unknown').upper()}",
            ]

            if predictions:
                parts.append("")
                parts.append("<b>Predicted Cascading:</b>")
                for p in predictions[:3]:
                    parts.append(
                        f"  • {p['service']} → {p['predicted_status']} "
                        f"in {p['time_to_failure_minutes']}min"
                    )

            parts.append("")
            parts.append(f"<b>Action:</b> {rca.recommended_action}")
            parts.append(f"━━━━━━━━━━━━━━━━━━━")
            parts.append(f"<i>{now().strftime('%H:%M:%S UTC')}</i>")

            self._send_telegram("\n".join(parts))
        except Exception as exc:
            log.error("Diagnostic alert failed: %s", sanitize_error_message(exc))

    def health(self) -> dict:
        weights = {
            Health.NOMINAL: 100,
            Health.DEGRADED: 70,
            Health.CRITICAL: 25,
            Health.OFFLINE: 0,
            Health.UNKNOWN: 50,
        }
        with self._observations_lock:
            observations = dict(self.observations)

        groups: Dict[str, List[Observation]] = {}
        for obs in observations.values():
            groups.setdefault(obs.sector.value, []).append(obs)

        sectors = {}
        scores = []
        for sn, ol in groups.items():
            score = sum(weights[o.status] for o in ol) / len(ol)
            scores.append(score)
            sectors[sn] = {
                "status": self._status_label(score).value,
                "score": round(score, 1),
                "issues": sum(len(o.issues) for o in ol),
                "observers": len(ol),
            }
        overall = sum(scores) / len(scores) if scores else 50
        return {
            "status": self._status_label(overall).value,
            "score": round(overall, 1),
            "sectors": sectors,
            "observer_count": len(self.observers),
            "issue_count": sum(len(o.issues) for o in observations.values()),
            "timestamp": iso(now()),
        }

    def _status_label(self, score: float) -> Health:
        if score >= 90:
            return Health.NOMINAL
        if score >= 60:
            return Health.DEGRADED
        if score > 0:
            return Health.CRITICAL
        return Health.OFFLINE

    # ═══════════════════════════════════════════════════════════════════
    # NOTIFICATION ENGINE
    # ═══════════════════════════════════════════════════════════════════

    def _send_telegram(self, message: str) -> bool:
        try:
            from services.email_service import email_service
            return email_service.send_telegram(message)
        except Exception as exc:
            log.error("Telegram send failed: %s", sanitize_error_message(exc))
            return False

    def _check_critical_and_alert(self) -> None:
        with self._observations_lock:
            observations = dict(self.observations)

        criticals = []
        for name, obs in observations.items():
            if obs.status in (Health.CRITICAL, Health.OFFLINE):
                criticals.append(f"• {name} ({obs.sector.value}): {obs.status.value}")

        if criticals:
            msg = (
                f"🔴 <b>CRITICAL — {len(criticals)} engine(s) down</b>\n\n"
                + "\n".join(criticals)
                + f"\n\n<i>{now().strftime('%H:%M:%S UTC')}</i>"
            )
            self._send_telegram(msg)
            self._alert_history.append({
                "type": "critical",
                "engines": criticals,
                "time": iso(now()),
            })
        self._check_brand_alerts()

    def _check_brand_alerts(self) -> None:
        try:
            with self._observations_lock:
                observations = dict(self.observations)
            for name, obs in observations.items():
                if obs.sector not in (Sector.BRANDS, Sector.CONTENT):
                    continue
                metrics = obs.metrics or {}
                change = metrics.get("engagement_change_pct", 0)
                if change <= -50:
                    self._send_telegram(
                        f"📉 <b>Engagement Drop: {name}</b>\n"
                        f"Down {abs(change):.0f}%\n"
                        f"<i>{now().strftime('%H:%M UTC')}</i>"
                    )
                elif change >= 200:
                    self._send_telegram(
                        f"🔥 <b>Viral: {name}</b>\n"
                        f"Up {change:.0f}% — boosting\n"
                        f"<i>{now().strftime('%H:%M UTC')}</i>"
                    )
        except Exception as exc:
            log.debug("Brand alert check: %s", sanitize_error_message(exc))

    def _send_daily_briefing(self) -> None:
        try:
            health = self.health()
            tasks_done = sum(1 for t in self.history if t.status == Status.COMPLETED)
            tasks_failed = sum(1 for t in self.history if t.status == Status.FAILED)

            backup_status = "Unknown"
            try:
                from plugins.backup.plugin import backup_plugin
                if backup_plugin and backup_plugin.supabase:
                    records = (
                        backup_plugin.supabase.table("backup_records")
                        .select("*").order("started_at", desc=True).limit(1).execute()
                    )
                    if records.data:
                        last = records.data[0]
                        hours_ago = round(
                            (now() - datetime.fromisoformat(last["started_at"].replace("Z", "+00:00"))).total_seconds() / 3600
                        )
                        backup_status = (
                            f"{last['status']} ({hours_ago}h ago)"
                            if last["status"] == "completed"
                            else f"❌ {last['status']}"
                        )
            except Exception as exc:
                log.warning("Briefing: backup query failed: %s", sanitize_error_message(exc))

            monitor_status = "Unknown"
            try:
                from plugins.monitor.plugin import monitor_plugin
                if monitor_plugin and monitor_plugin.supabase:
                    targets = (
                        monitor_plugin.supabase.table("monitor_targets")
                        .select("*").eq("is_paused", False).execute()
                    )
                    if targets.data:
                        checks = (
                            monitor_plugin.supabase.table("monitor_checks")
                            .select("status,target_id").order("checked_at", desc=True)
                            .limit(len(targets.data) * 2).execute()
                        )
                        up_count = sum(1 for c in (checks.data or []) if c["status"] == "up")
                        total = len(targets.data)
                        monitor_status = (
                            f"{up_count}/{total} UP"
                            if up_count == total
                            else f"❌ {up_count}/{total} UP"
                        )
            except Exception as exc:
                log.warning("Briefing: monitor query failed: %s", sanitize_error_message(exc))

            msg = (
                f"📊 <b>KANYOZA DAILY BRIEFING</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"✅ Health: {health['status'].upper()} ({health['score']:.0f}%)\n"
                f"📝 Tasks: {tasks_done} done, {tasks_failed} failed\n"
                f"💾 Backup: {backup_status}\n"
                f"🔍 Monitor: {monitor_status}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"👔 {len(self.observers)} engines | v{self.VERSION}"
            )
            self._send_telegram(msg)
            log.info("Daily briefing sent via Telegram")
        except Exception as exc:
            log.error("Daily briefing failed: %s", sanitize_error_message(exc))

    # ═══════════════════════════════════════════════════════════════════
    # DECISION & PLANNING
    # ═══════════════════════════════════════════════════════════════════

    def _create_task(
        self,
        sector: Sector,
        desc: str,
        cap_name: str,
        reason: str,
        confidence: float = 0.8,
        params: dict = None,
        source: str = None,
    ) -> Task:
        cap = self.capabilities[cap_name]
        authority = self.policy.authority(cap)
        task = Task(
            id=f"dir_{uuid.uuid4().hex[:14]}",
            sector=sector,
            description=desc,
            capability=cap_name,
            authority=authority,
            risk=cap.risk,
            params=params or {},
            status=(
                Status.APPROVAL
                if self.policy.requires_approval(cap)
                else Status.PLANNED
            ),
            confidence=confidence,
            reason=reason,
            sources=[source] if source else [],
        )
        self.tasks[task.id] = task
        self.store.save_task(task)
        
        # Publish task_created event
        _publish_event("task_created", {
            "task_id": task.id,
            "description": desc,
            "capability": cap_name,
            "sector": sector.value,
        })
        
        return task

    def decide(self) -> List[Task]:
        decisions = []
        health = self.health()

        if health["score"] < 90:
            decisions.append(
                self._create_task(
                    Sector.HEALTH,
                    "Run complete platform health scan",
                    "run_health_scan",
                    f"Health score is {health['score']}",
                    0.99,
                )
            )

        with self._observations_lock:
            observations = dict(self.observations)

        for obs in observations.values():
            for issue in obs.issues:
                typ = str(issue.get("type", "")).lower()
                if typ in {"failed_service", "service_down", "worker_failed"}:
                    decisions.append(
                        self._create_task(
                            Sector.HEALTH, "Recover failed service",
                            "restart_failed_service", "Recoverable service failure",
                            0.9, issue, obs.source,
                        )
                    )
                elif typ in {"security_issue", "vulnerability", "credential_exposure"}:
                    decisions.append(
                        self._create_task(
                            Sector.SECURITY, "Prepare security remediation",
                            "prepare_security_remediation", "Security issue observed",
                            0.97, issue, obs.source,
                        )
                    )
                elif typ in {"brand_inactive", "engagement_decline"}:
                    decisions.append(
                        self._create_task(
                            Sector.BRANDS, "Queue brand revival",
                            "queue_brand_revival", "Brand performance declined",
                            0.87, issue, obs.source,
                        )
                    )
                elif typ in {"content_gap", "schedule_gap"}:
                    decisions.append(
                        self._create_task(
                            Sector.CONTENT, "Optimize content schedule",
                            "optimize_content_schedule", "Content gap observed",
                            0.88, issue, obs.source,
                        )
                    )
                elif typ == "client_report_due":
                    decisions.append(
                        self._create_task(
                            Sector.CLIENTS, "Generate due client report",
                            "generate_client_report", "Client report due",
                            0.96, issue, obs.source,
                        )
                    )

        for goal in sorted(self.goals.values(), key=lambda g: g.priority, reverse=True):
            if not goal.enabled:
                continue
            mapping = {
                Sector.SECURITY: ("run_guardian_scan", 0.93),
                Sector.BRANDS: ("calculate_brand_health", 0.88),
                Sector.CONTENT: ("publish_scheduled_content", 0.80),
                Sector.GROWTH: ("analyze_growth_opportunity", 0.82),
                Sector.CLIENTS: ("generate_client_report", 0.78),
            }
            if goal.sector in mapping:
                cap_name, conf = mapping[goal.sector]
                decisions.append(
                    self._create_task(
                        goal.sector, f"Goal-driven: {goal.name}",
                        cap_name, f"Goal: {goal.name}", conf,
                    )
                )

        if not decisions:
            decisions.append(
                self._create_task(
                    Sector.SELF, "Run Director self-diagnostic",
                    "self_diagnostic", "No urgent work; inspect own state", 0.95,
                )
            )

        # Deduplicate
        seen = set()
        unique = []
        for task in decisions:
            key = (task.sector, task.capability, task.description)
            if key not in seen:
                seen.add(key)
                unique.append(task)

        # Apply learning adjustments
        for task in unique:
            success_rate = self.learning_loop.get_success_rate(task.capability)
            if success_rate is not None:
                task.confidence = clamp(task.confidence * 0.7 + success_rate * 0.3, 0.1, 0.99)
            if self.learning_loop.get_recurring_failures(task.capability, threshold=3):
                task.status = Status.APPROVAL
                task.reason += " (recurring failures detected)"

        # Filter redundant tasks when root cause is known
        if self.current_root_cause:
            root_cause = self.current_root_cause.root_cause
            affected = set(self.current_root_cause.affected_services)
            filtered = []
            for task in unique:
                if (
                    task.capability == "restart_failed_service"
                    and task.sector == Sector.HEALTH
                ):
                    service = task.params.get("service_name", "")
                    if service in affected and service != root_cause:
                        continue
                filtered.append(task)
            unique = filtered

        return sorted(unique, key=self._priority, reverse=True)

    def _priority(self, task: Task) -> float:
        return (
            {Authority.ALERT: 1000, Authority.SUGGEST: 700, Authority.NOTIFY: 500, Authority.AUTO: 300}[task.authority]
            + {Risk.CRITICAL: 100, Risk.HIGH: 70, Risk.MEDIUM: 40, Risk.LOW: 10}[task.risk]
            + task.confidence * 100
        )

    def build_plan(self) -> dict:
        tasks = self.decide()
        self.last_plan = now()
        return {
            "date": now().date().isoformat(),
            "created_at": iso(self.last_plan),
            "tasks": [safe(asdict(t)) for t in tasks],
            "strategy": "Observe → assess → prioritize → authorize → execute → verify → learn → report",
            "priorities": ["availability", "security", "client commitments", "brand/content operations", "growth", "learning"],
        }

    def approve(self, task_id: str, actor: str = "admin") -> Task:
        task = self.tasks.get(task_id)
        if task is None:
            raise KeyError(f"Task {task_id} not found")
        if task.status not in (Status.APPROVAL, Status.FLAGGED):
            raise ValueError("Task is not awaiting approval")
        task.status = Status.APPROVED
        task.approved_at = now()
        task.approved_by = actor
        self.store.save_task(task)
        self.store.save_approval(task_id, True, actor)
        return task

    def reject(self, task_id: str, actor: str = "admin", reason: str = "") -> Task:
        task = self.tasks.get(task_id)
        if task is None:
            raise KeyError(f"Task {task_id} not found")
        task.status = Status.CANCELLED
        task.error = f"Rejected by {actor}: {reason}"
        self.store.save_task(task)
        self.store.save_approval(task_id, False, actor)
        return task

    # ═══════════════════════════════════════════════════════════════════
    # EXECUTION
    # ═══════════════════════════════════════════════════════════════════

    def execute(self, task_id: str) -> Task:
        task = self.tasks.get(task_id)
        if task is None:
            raise KeyError(f"Task {task_id} not found")

        with self._execution_lock:
            if task_id in self._executing_tasks:
                log.warning("Task %s is already executing — skipping", task_id)
                return task
            self._executing_tasks.add(task_id)

        start_time = time.monotonic()
        try:
            cap = self.capabilities[task.capability]
            if not cap.enabled:
                task.status = Status.SKIPPED
                task.error = "Capability disabled"
                self.store.save_task(task)
                return task
            if self.policy.requires_approval(cap) and task.status != Status.APPROVED:
                task.status = Status.APPROVAL
                self.store.save_task(task)
                self._alert({"type": "approval_required", "task": safe(asdict(task))})
                return task

            task.status = Status.EXECUTING
            task.executed_at = now()
            self.store.save_task(task)

            try:
                result = cap.handler(task.params)
                if inspect.isawaitable(result):
                    try:
                        loop = asyncio.get_running_loop()
                        future = asyncio.run_coroutine_threadsafe(result, loop)
                        result = future.result(timeout=30)
                    except RuntimeError:
                        result = asyncio.run(result)

                task.status = Status.COMPLETED
                task.result = safe(result or {})
                self.history.append(task)
                self.store.save_task(task)
                
                # Publish task_completed event
                _publish_event("task_completed", {
                    "task_id": task.id,
                    "description": task.description,
                    "capability": task.capability,
                })
                
                self._record_decision(f"Executed: {task.description}", task.reason, task)
                self._learn(task)
                self._event("director_action_completed", {"task": safe(asdict(task))})
                return task
            except Exception as exc:
                task.status = Status.FAILED
                task.error = sanitize_error_message(exc)
                task.result = {"error": sanitize_error_message(exc)}
                self.history.append(task)
                self.store.save_task(task)
                
                # Publish task_failed event
                _publish_event("task_failed", {
                    "task_id": task.id,
                    "description": task.description,
                    "error": task.error,
                })
                
                self._record_decision(f"Failed: {task.description}", sanitize_error_message(exc), task, "failed")
                self._learn(task)
                self._alert({"type": "task_failed", "task": safe(asdict(task))})
                return task
        finally:
            duration_ms = (time.monotonic() - start_time) * 1000
            self.learning_loop.record_outcome(task, duration_ms)
            with self._execution_lock:
                self._executing_tasks.discard(task_id)

    def execute_due(self) -> None:
        due_tasks = [
            task_id
            for task_id, task in self.tasks.items()
            if task.status in (Status.PLANNED, Status.APPROVED)
            and task_id not in self._executing_tasks
        ]
        for task_id in due_tasks:
            self.execute(task_id)

    def _record_decision(self, decision_text: str, reason: str, task: Task, outcome: str = None) -> None:
        d = Decision(
            id=f"decision_{uuid.uuid4().hex[:12]}",
            timestamp=now(),
            decision=decision_text,
            reason=reason,
            confidence=task.confidence,
            sector=task.sector,
            authority=task.authority,
            risk=task.risk,
            task_id=task.id,
            outcome=outcome,
        )
        self.decisions.append(d)
        self.store.save_decision(d)

    def _learn(self, task: Task) -> None:
        self.store.save_learning({
            "id": f"learn_{uuid.uuid4().hex[:12]}",
            "task_id": task.id,
            "signal": "success" if task.status == Status.COMPLETED else "failure",
            "lesson": f"Outcome of {task.capability}: {task.status.value}",
        })

    def _alert(self, payload: dict) -> None:
        self._event("director_alert", payload)
        self._alert_history.append({
            "type": "director_alert",
            "payload": payload,
            "time": iso(now()),
        })
        if self.alert_cb:
            try:
                self.alert_cb(payload)
            except Exception as exc:
                log.exception("Alert callback failed: %s", sanitize_error_message(exc))

    def _event(self, name: str, payload: dict) -> None:
        self.enqueue_event(name, payload)

    # ═══════════════════════════════════════════════════════════════════
    # EVENT DRAIN
    # ═══════════════════════════════════════════════════════════════════

    def _drain_events_loop(self):
        log.info("Director: Event drain thread started")
        while self.running:
            self._drain_events()
            time.sleep(15)
        self._drain_events()
        log.info("Director: Event drain thread stopped")

    def _drain_events(self):
        if not self._event_queue:
            return
        batch = []
        while self._event_queue:
            try:
                batch.append(self._event_queue.popleft())
            except IndexError:
                break
        if not batch:
            return
        log.debug("Director: Draining %d queued events", len(batch))
        for event in batch:
            try:
                if self._stored_event_cb:
                    self._stored_event_cb(event["name"], event["payload"])
            except Exception as exc:
                log.exception("Event processing failed: %s — %s", event["name"], sanitize_error_message(exc))

    # ═══════════════════════════════════════════════════════════════════
    # STRATEGIC FORESIGHT
    # ═══════════════════════════════════════════════════════════════════

    def generate_strategic_outlook(self, horizon_days: int = 7) -> Optional[StrategicOutlook]:
        task_history = list(self.history)
        if len(task_history) < 10:
            return None

        daily_tasks = defaultdict(int)
        for t in task_history:
            day = t.executed_at.strftime("%Y-%m-%d") if t.executed_at else None
            if day:
                daily_tasks[day] += 1

        recent_daily_avg = mean(list(daily_tasks.values())[-7:]) if len(daily_tasks) >= 7 else 1
        predicted_volume = recent_daily_avg * horizon_days

        health_scores = []
        for b in list(self.briefings)[-20:]:
            if isinstance(b, dict) and "health" in b:
                health_scores.append(b["health"].get("score", 50))

        if len(health_scores) >= 5:
            slope = (health_scores[-1] - health_scores[0]) / len(health_scores)
            predicted_health = clamp(health_scores[-1] + slope * horizon_days, 0, 100)
        else:
            predicted_health = self.health()["score"]

        risks = []
        if predicted_health < 60:
            risks.append("Health trajectory declining — intervention needed within 48h")

        failure_patterns = Counter(
            t.capability for t in task_history if t.status == Status.FAILED
        )
        for cap, count in failure_patterns.most_common(3):
            if count >= 2:
                risks.append(f"Recurring failure in {cap}: {count} failures")

        opportunities = []
        with self._observations_lock:
            observations = dict(self.observations)
        sectors_with_issues = Counter(
            obs.sector.value for obs in observations.values() if obs.issues
        )
        for sector, count in sectors_with_issues.most_common(2):
            opportunities.append(
                f"Address {count} issues in {sector} — potential for significant improvement"
            )

        if predicted_health > 85:
            opportunities.append("System healthy — ideal time for proactive optimization")

        return StrategicOutlook(
            horizon_days=horizon_days,
            predicted_health_score=round(predicted_health, 1),
            predicted_task_volume=int(predicted_volume),
            emerging_risks=risks,
            growth_opportunities=opportunities,
            recommended_focus_areas=self._derive_focus_areas(risks, opportunities),
            confidence=min(0.9, len(task_history) / 50),
        )

    def _derive_focus_areas(self, risks: List[str], opportunities: List[str]) -> List[str]:
        areas = []
        if any("health" in r.lower() for r in risks):
            areas.append("Stabilize platform health")
        if any("failure" in r.lower() for r in risks):
            areas.append("Address recurring failures")
        if any("improvement" in o.lower() for o in opportunities):
            areas.append("Execute optimization initiatives")
        if any("healthy" in o.lower() for o in opportunities):
            areas.append("Proactive growth and scaling")
        return areas or ["Monitor and maintain current state"]

    def allocate_attention(self) -> List[ResourceAllocation]:
        health = self.health()
        allocations = []
        total_priority = sum(g.priority for g in self.goals.values() if g.enabled)

        for sector_name, sector_health in health.get("sectors", {}).items():
            try:
                sector = Sector(sector_name)
            except ValueError:
                continue

            sector_goals = [g for g in self.goals.values() if g.sector == sector and g.enabled]
            importance = sum(g.priority for g in sector_goals) / max(total_priority, 1)
            sector_score = sector_health.get("score", 50)
            attention_needed = (100 - sector_score) / 100 * importance

            allocations.append(
                ResourceAllocation(
                    sector=sector,
                    current_health=sector_score,
                    importance_weight=round(importance, 2),
                    recommended_attention_pct=round(attention_needed * 100, 1),
                    reasoning=(
                        f"Critical: low health ({sector_score}) + high importance ({importance:.0%})"
                        if sector_score < 60 and importance > 0.2
                        else f"Monitor: stable but important"
                        if sector_score >= 60 and importance > 0.2
                        else f"Maintain: healthy and lower priority"
                    ),
                )
            )

        return sorted(allocations, key=lambda a: a.recommended_attention_pct, reverse=True)

    def generate_executive_summary(self) -> str:
        health = self.health()
        outlook = self.generate_strategic_outlook()
        allocations = self.allocate_attention()

        if health["score"] >= 90:
            opening = "The platform is operating excellently."
        elif health["score"] >= 70:
            opening = "The platform is stable with minor areas for attention."
        elif health["score"] >= 50:
            opening = "The platform requires attention — several areas need intervention."
        else:
            opening = "URGENT: The platform is in a degraded state requiring immediate action."

        parts = [
            f"## Kanyoza Platform Executive Summary",
            f"*{utcnow().strftime('%B %d, %Y — %H:%M UTC')}*",
            "",
            opening,
            "",
            f"### Health: {health['score']:.0f}/100 ({health['status'].upper()})",
        ]

        critical_sectors = [
            (name, s) for name, s in health.get("sectors", {}).items()
            if s.get("status") in ("critical", "degraded")
        ]
        if critical_sectors:
            parts.append("")
            parts.append("### ⚠️ Areas Requiring Attention")
            for name, s in critical_sectors[:3]:
                parts.append(
                    f"- **{name}**: {s['status'].upper()} ({s['score']:.0f}%) — {s.get('issues', 0)} issues"
                )

        if allocations:
            parts.append("")
            parts.append("### 📊 Recommended Focus")
            for a in allocations[:3]:
                if a.recommended_attention_pct > 15:
                    parts.append(f"- **{a.sector.value}**: {a.reasoning}")

        if outlook:
            parts.append("")
            parts.append(f"### 🔮 {outlook.horizon_days}-Day Outlook")
            parts.append(f"- Predicted health: {outlook.predicted_health_score:.0f}/100")
            if outlook.emerging_risks:
                parts.append(f"- ⚠️ Risks: {outlook.emerging_risks[0]}")
            if outlook.growth_opportunities:
                parts.append(f"- 📈 Opportunities: {outlook.growth_opportunities[0]}")

        recent = [t for t in list(self.history)[-5:] if t.status == Status.COMPLETED]
        if recent:
            parts.append("")
            parts.append("### ✅ Recent Actions")
            for t in recent:
                parts.append(f"- {t.description}")

        return "\n".join(parts)

    def correlate_observations(self) -> List[Dict[str, Any]]:
        correlations = []
        time_windows = defaultdict(list)

        with self._observations_lock:
            observations = dict(self.observations)

        for name, obs in observations.items():
            bucket = obs.timestamp.replace(second=0, microsecond=0)
            bucket = bucket - timedelta(minutes=bucket.minute % 5)
            time_windows[bucket].append((name, obs))

        for window_time, window_obs in time_windows.items():
            if len(window_obs) < 2:
                continue
            degraded = [
                name for name, obs in window_obs
                if obs.status in (Health.CRITICAL, Health.DEGRADED)
            ]
            if len(degraded) >= 2:
                correlations.append({
                    "timestamp": iso(window_time),
                    "engines": degraded,
                    "likely_cause": f"Shared infrastructure issue affecting {len(degraded)} engines",
                    "severity": "critical" if len(degraded) >= 3 else "warning",
                })

        return correlations

    def generate_initiatives(self) -> List[Dict[str, Any]]:
        initiatives = []
        health = self.health()

        if health["score"] >= 90:
            initiatives.append({
                "type": "optimization",
                "title": "Platform Performance Optimization Window",
                "description": "System health is excellent. Ideal time for proactive tuning.",
                "priority": "medium",
                "suggested_capabilities": ["optimize_infrastructure"],
            })

        used_capabilities = set(t.capability for t in self.history if t.status == Status.COMPLETED)
        unused = set(self.capabilities.keys()) - used_capabilities - {
            c for c in self.capabilities if self.capabilities[c].risk == Risk.CRITICAL
        }
        if unused:
            initiatives.append({
                "type": "capability_gap",
                "title": f"Unused Capabilities Detected: {len(unused)}",
                "description": f"Capabilities never used: {', '.join(list(unused)[:5])}",
                "priority": "low",
                "unused_capabilities": list(unused),
            })

        return sorted(initiatives, key=lambda i: {"high": 0, "medium": 1, "low": 2}[i["priority"]])

    def briefing(self, kind: Briefing = Briefing.CHECKPOINT) -> dict:
        health = self.health()
        pending = [
            safe(asdict(t)) for t in self.tasks.values()
            if t.status == Status.APPROVAL
        ]
        failures = [t for t in self.history if t.status == Status.FAILED]
        brief = {
            "type": kind.value,
            "timestamp": iso(now()),
            "mood": (
                "critical" if health["score"] < 40
                else "attentive" if pending
                else "watchful" if failures
                else "confident" if health["score"] >= 90
                else "focused"
            ),
            "health": health,
            "actions_taken": [
                t.description for t in list(self.history)[-10:]
                if t.status == Status.COMPLETED
            ],
            "pending_approvals": pending,
            "insights": [
                f"Health score: {health['score']}",
                f"Pending approvals: {len(pending)}",
                f"Recent failures: {len(failures)}",
            ],
            "plan_summary": {"tasks": len(self.tasks), "approvals": len(pending)},
        }
        self.briefings.append(brief)
        self._event("director_briefing", brief)
        return brief

    def status(self) -> dict:
        health = self.health()
        counts = Counter(t.status.value for t in self.tasks.values())
        outlook = self.generate_strategic_outlook()
        allocations = self.allocate_attention()
        initiatives = self.generate_initiatives()

        return {
            "name": "Kanyoza Director",
            "codename": "The Autonomous CEO",
            "version": self.VERSION,
            "running": self.running,
            "uptime_hours": round((now() - self.started).total_seconds() / 3600, 2),
            "health": health,
            "observers": len(self.observers),
            "capabilities": len(self.capabilities),
            "sectors": len(Sector),
            "tasks": dict(counts),
            "pending_approvals": sum(t.status == Status.APPROVAL for t in self.tasks.values()),
            "last_observation": iso(self.last_observation),
            "last_plan": iso(self.last_plan),
            "event_queue_depth": len(self._event_queue),
            "diagnostics": {
                "topology_nodes": len(self.topology.nodes),
                "topology_dependencies": sum(len(n.depends_on) for n in self.topology.nodes.values()),
                "current_root_cause": (
                    {
                        "service": self.current_root_cause.root_cause,
                        "confidence": self.current_root_cause.confidence,
                        "affected_count": len(self.current_root_cause.affected_services),
                    }
                    if self.current_root_cause else None
                ),
                "current_impact": self.current_impact,
                "cascading_predictions": self.current_predictions[:5],
                "recovery_plans": len(self.recovery_coordinator.recovery_plans),
            },
            "intelligence": {
                "learning_signals": len(self.learning_loop.signals),
                "adaptive_thresholds": dict(self.adaptive_thresholds.thresholds),
            },
            "outlook": (
                {
                    "predicted_health": outlook.predicted_health_score,
                    "predicted_volume": outlook.predicted_task_volume,
                    "emerging_risks": outlook.emerging_risks,
                }
                if outlook else None
            ),
            "resource_allocation": [
                {"sector": a.sector.value, "health": a.current_health, "attention_pct": a.recommended_attention_pct}
                for a in (allocations or [])[:5]
            ],
            "initiatives": initiatives,
        }

    # ═══════════════════════════════════════════════════════════════════
    # DIAGNOSTIC METHODS
    # ═══════════════════════════════════════════════════════════════════

    def get_topology(self) -> Dict[str, Any]:
        return {
            "nodes": {
                name: {
                    "sector": node.sector.value,
                    "depends_on": sorted(node.depends_on),
                    "depended_by": sorted(node.depended_by),
                    "criticality": node.criticality,
                    "last_health": node.last_health.value,
                    "last_updated": iso(node.last_updated),
                }
                for name, node in self.topology.nodes.items()
            }
        }

    def get_root_cause(self) -> Optional[Dict[str, Any]]:
        if self.current_root_cause:
            rca = self.current_root_cause
            return {
                "analysis_id": rca.analysis_id,
                "root_cause": rca.root_cause,
                "affected_services": rca.affected_services,
                "confidence": rca.confidence,
                "evidence": rca.evidence,
                "timestamp": iso(rca.timestamp),
                "recommended_action": rca.recommended_action,
            }
        return None

    def get_cascading_predictions(self) -> List[Dict[str, Any]]:
        return [safe(p) for p in self.current_predictions]

    def get_impact_assessment(self) -> Dict[str, Any]:
        return self.current_impact

    def get_recovery_plans(self) -> List[Dict[str, Any]]:
        return self.recovery_coordinator.get_recovery_plans()

    def get_recent_analyses(self, limit: int = 10) -> List[Dict[str, Any]]:
        return [safe(asdict(a)) for a in self.root_cause_analyzer.get_recent_analyses(limit)]

    # ═══════════════════════════════════════════════════════════════════
    # LIFECYCLE
    # ═══════════════════════════════════════════════════════════════════

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.started = now()

        self.thread = threading.Thread(target=self._loop, name="kanyoza-director", daemon=True)
        self.thread.start()

        self._event_thread = threading.Thread(target=self._drain_events_loop, name="director-events", daemon=True)
        self._event_thread.start()

        threading.Thread(target=self._startup, name="director-startup", daemon=True).start()

        log.info(
            "👔 Director v%s ONLINE — autonomous CEO, diagnostic brain, learning engine",
            self.VERSION,
        )

    def _startup(self) -> None:
        time.sleep(2)
        try:
            self.observe_all()
            self.build_plan()
            self.execute_due()
            self.briefing(Briefing.CHECKPOINT)
        except Exception as exc:
            log.exception("Startup cycle failed: %s", sanitize_error_message(exc))

    def stop(self) -> None:
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)
        if self._event_thread and self._event_thread.is_alive():
            self._event_thread.join(timeout=5)
        log.info("👔 Director OFFLINE")

    def _loop(self) -> None:
        last_minute = None

        while self.running:
            if self._observation_circuit_open:
                if time.monotonic() > self._observation_circuit_reset_time:
                    self._observation_circuit_open = False
                    self._consecutive_failures = 0
                    log.info("Director: Observation circuit breaker reset")
                else:
                    log.warning("Director: Circuit open — skipping observation cycle")
                    time.sleep(self.interval)
                    continue

            try:
                self.observe_all()
                self._consecutive_failures = 0

                if self.last_plan is None or (now() - self.last_plan).total_seconds() >= 1800:
                    self.build_plan()

                self.execute_due()

                current_minute = now().strftime("%Y-%m-%d-%H-%M")
                if current_minute != last_minute:
                    last_minute = current_minute
                    hour = now().hour
                    minute = now().minute

                    if hour == 8 and minute == 0:
                        today_str = now().strftime("%Y-%m-%d")
                        if self._last_daily_briefing != today_str:
                            self._last_daily_briefing = today_str
                            self._send_daily_briefing()

                    if minute == 0 and hour in (6, 12, 18, 22):
                        kind = (
                            Briefing.MORNING if hour == 6
                            else Briefing.EVENING if hour == 22
                            else Briefing.CHECKPOINT
                        )
                        self.briefing(kind)

            except Exception as exc:
                self._consecutive_failures += 1
                log.exception(
                    "Director cycle failed (%d consecutive): %s",
                    self._consecutive_failures,
                    sanitize_error_message(exc),
                )

                if self._consecutive_failures >= 3:
                    backoff = min(300, 30 * self._consecutive_failures)
                    self._observation_circuit_open = True
                    self._observation_circuit_reset_time = time.monotonic() + backoff
                    log.critical(
                        "Director: Circuit breaker OPEN — backing off for %ds",
                        backoff,
                    )

            time.sleep(self.interval)


# ═══════════════════════════════════════════════════════════════════════════
# FASTAPI APP
# ═══════════════════════════════════════════════════════════════════════════

def create_app(director: Director):
    """Create FastAPI app with full authentication.

    v4.0.1 FIX: All routes defined WITHOUT /director prefix.
    The mount in main.py adds the /director prefix automatically.
    """
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
    from pydantic import BaseModel, Field

    app = FastAPI(title="Kanyoza Director", version=director.VERSION)
    security = HTTPBearer(auto_error=False)

    def _authenticate(
        credentials: Optional[HTTPAuthorizationCredentials],
        request: Optional[Request] = None,
    ) -> bool:
        """Authenticate API requests."""
        if request is None:
            return False

        master_token = os.getenv("MASTER_API_TOKEN", "")
        if not master_token:
            log.warning("MASTER_API_TOKEN not set — Director API is UNAUTHENTICATED")
            return False

        token = ""
        if credentials:
            token = credentials.credentials
        if not token:
            token = request.headers.get("X-API-Key", "")

        if not token or not isinstance(token, str):
            return False
        if not isinstance(master_token, str):
            return False

        return hmac.compare_digest(token, master_token)

    async def _require_auth(
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        request: Optional[Request] = None,
    ) -> None:
        """Require authentication. Fails closed if request is None."""
        # v4.0.2: Allow disabling auth via environment variable
        if os.getenv("DISABLE_DIRECTOR_AUTH", "false").lower() == "true":
            return  # Skip auth check
    
        if not _authenticate(credentials, request):
            raise HTTPException(
                status_code=401,
                detail="Authentication required",
            )

    class ApprovalBody(BaseModel):
        actor: str = Field(default="admin", min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_\-\.\s]+$")

    class RejectBody(BaseModel):
        actor: str = Field(default="admin", min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_\-\.\s]+$")
        reason: str = Field(default="", max_length=500)

    # ═══════════════════════════════════════════════════════════════
    # HEALTH (no auth required)
    # ═══════════════════════════════════════════════════════════════

    @app.get("/health")
    async def health_check():
        return {
            "status": "healthy" if director.running else "stopped",
            "version": director.VERSION,
            "uptime_hours": round((now() - director.started).total_seconds() / 3600, 2) if director.running else 0,
        }

    # ═══════════════════════════════════════════════════════════════
    # STATUS & BRIEFING (auth required)
    # ═══════════════════════════════════════════════════════════════

    @app.get("/status")
    async def get_status(
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        request: Optional[Request] = None,
    ):
        await _require_auth(credentials, request)
        return director.status()

    @app.get("/briefing")
    async def get_briefing(
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        request: Optional[Request] = None,
    ):
        await _require_auth(credentials, request)
        return director.briefings[-1] if director.briefings else director.briefing()

    # ═══════════════════════════════════════════════════════════════
    # TASKS & APPROVALS
    # ═══════════════════════════════════════════════════════════════

    @app.get("/tasks")
    async def get_tasks(
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        request: Optional[Request] = None,
    ):
        await _require_auth(credentials, request)
        return [safe(asdict(t)) for t in director.tasks.values()]

    @app.get("/approvals")
    async def get_approvals(
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        request: Optional[Request] = None,
    ):
        await _require_auth(credentials, request)
        return [safe(asdict(t)) for t in director.tasks.values() if t.status == Status.APPROVAL]

    @app.post("/tasks/{task_id}/approve")
    async def approve_task(
        task_id: str,
        body: ApprovalBody,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        request: Optional[Request] = None,
    ):
        await _require_auth(credentials, request)
        try:
            return safe(asdict(director.approve(task_id, body.actor)))
        except KeyError:
            raise HTTPException(404, "Task not found")
        except ValueError as e:
            raise HTTPException(409, str(e))

    @app.post("/tasks/{task_id}/reject")
    async def reject_task(
        task_id: str,
        body: RejectBody,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        request: Optional[Request] = None,
    ):
        await _require_auth(credentials, request)
        try:
            return safe(asdict(director.reject(task_id, body.actor, body.reason)))
        except KeyError:
            raise HTTPException(404, "Task not found")

    # ═══════════════════════════════════════════════════════════════
    # ENGINES (NO /director prefix — mount adds it)
    # ═══════════════════════════════════════════════════════════════

    @app.get("/engines")
    async def get_engines(
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        request: Optional[Request] = None,
    ):
        await _require_auth(credentials, request)
        with director._observations_lock:
            observations = dict(director.observations)
        return {
            "engines": {
                name: {
                    "name": name,
                    "status": obs.status.value,
                    "sector": obs.sector.value,
                    "issues": len(obs.issues),
                    "message": obs.message,
                    "metrics": obs.metrics if obs.metrics else {},
                }
                for name, obs in observations.items()
            },
            "total": len(observations),
        }

    # ═══════════════════════════════════════════════════════════════════
    # DIAGNOSTICS (NO /director prefix)
    # ═══════════════════════════════════════════════════════════════════

    @app.get("/diagnostics")
    async def get_diagnostics(
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        request: Optional[Request] = None,
    ):
        await _require_auth(credentials, request)
        return {
            "root_cause": director.get_root_cause(),
            "impact": director.get_impact_assessment(),
            "cascading_predictions": director.get_cascading_predictions(),
            "recovery_plans": director.get_recovery_plans(),
            "recent_analyses": director.get_recent_analyses(limit=5),
        }

    @app.get("/diagnostics/root-cause")
    async def get_root_cause(
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        request: Optional[Request] = None,
    ):
        await _require_auth(credentials, request)
        result = director.get_root_cause()
        return {"status": "found" if result else "none", "data": result}

    @app.get("/diagnostics/cascading")
    async def get_cascading(
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        request: Optional[Request] = None,
    ):
        await _require_auth(credentials, request)
        return {"predictions": director.get_cascading_predictions()}

    @app.get("/diagnostics/impact")
    async def get_impact(
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        request: Optional[Request] = None,
    ):
        await _require_auth(credentials, request)
        return director.get_impact_assessment()

    @app.get("/diagnostics/recovery-plans")
    async def get_recovery_plans(
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        request: Optional[Request] = None,
    ):
        await _require_auth(credentials, request)
        return {"recovery_plans": director.get_recovery_plans()}

    # ═══════════════════════════════════════════════════════════════════
    # DECISIONS & ALERTS (NO /director prefix)
    # ═══════════════════════════════════════════════════════════════════

    @app.get("/decisions")
    async def get_decisions(
        limit: int = 50,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        request: Optional[Request] = None,
    ):
        await _require_auth(credentials, request)
        return [safe(asdict(d)) for d in list(director.decisions)[-limit:]]

    @app.get("/alerts")
    async def get_alerts(
        limit: int = 50,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        request: Optional[Request] = None,
    ):
        await _require_auth(credentials, request)
        return {"alerts": list(director._alert_history)[-limit:]}

    # ═══════════════════════════════════════════════════════════════════
    # CAPABILITIES (NO /director prefix)
    # ═══════════════════════════════════════════════════════════════════

    @app.get("/capabilities")
    async def get_capabilities(
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        request: Optional[Request] = None,
    ):
        await _require_auth(credentials, request)
        return {
            "capabilities": {
                name: {
                    "name": cap.name,
                    "sector": cap.sector.value,
                    "description": cap.description,
                    "enabled": cap.enabled,
                    "risk": cap.risk.value,
                    "authority": cap.authority.value,
                }
                for name, cap in director.capabilities.items()
            }
        }

    @app.post("/capabilities/{cap_name}/toggle")
    async def toggle_capability(
        cap_name: str,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        request: Optional[Request] = None,
    ):
        await _require_auth(credentials, request)
        cap = director.capabilities.get(cap_name)
        if not cap:
            raise HTTPException(404, f"Unknown capability: {cap_name}")
        cap.enabled = not cap.enabled
        log.info(
            "Capability '%s' %s by user",
            cap_name,
            "enabled" if cap.enabled else "disabled",
        )
        return {"capability": cap_name, "enabled": cap.enabled}

    # ═══════════════════════════════════════════════════════════════════
    # CONTROL ACTIONS
    # ═══════════════════════════════════════════════════════════════════

    @app.post("/observe")
    async def run_observe(
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        request: Optional[Request] = None,
    ):
        await _require_auth(credentials, request)
        director.observe_all()
        return director.status()

    @app.post("/plan")
    async def run_plan(
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        request: Optional[Request] = None,
    ):
        await _require_auth(credentials, request)
        return director.build_plan()

    @app.post("/start")
    async def run_start(
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        request: Optional[Request] = None,
    ):
        await _require_auth(credentials, request)
        director.start()
        return director.status()

    @app.post("/stop")
    async def run_stop(
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        request: Optional[Request] = None,
    ):
        await _require_auth(credentials, request)
        director.stop()
        return director.status()

    @app.get("/daily-briefing")
    async def get_daily_briefing(
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        request: Optional[Request] = None,
    ):
        await _require_auth(credentials, request)
        director._send_daily_briefing()
        return {"ok": True, "message": "Daily briefing sent via Telegram"}

    @app.get("/outlook")
    async def get_outlook(
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        request: Optional[Request] = None,
    ):
        await _require_auth(credentials, request)
        outlook = director.generate_strategic_outlook()
        return safe(asdict(outlook)) if outlook else {"error": "Insufficient data"}

    @app.get("/allocation")
    async def get_allocation(
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        request: Optional[Request] = None,
    ):
        await _require_auth(credentials, request)
        return [safe(asdict(a)) for a in director.allocate_attention()]

    @app.get("/summary")
    async def get_summary(
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        request: Optional[Request] = None,
    ):
        await _require_auth(credentials, request)
        return {"summary": director.generate_executive_summary()}

    @app.get("/initiatives")
    async def get_initiatives(
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        request: Optional[Request] = None,
    ):
        await _require_auth(credentials, request)
        return {"initiatives": director.generate_initiatives()}

    @app.get("/correlations")
    async def get_correlations(
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        request: Optional[Request] = None,
    ):
        await _require_auth(credentials, request)
        return {"correlations": director.correlate_observations()}

    @app.get("/alerts/history")
    async def get_alert_history(
        limit: int = 50,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        request: Optional[Request] = None,
    ):
        await _require_auth(credentials, request)
        return {"alerts": list(director._alert_history)[-limit:]}

    return app        

# ═══════════════════════════════════════════════════════════════════════════
# INITIALIZER
# ═══════════════════════════════════════════════════════════════════════════

director: Optional[Director] = None


def init_director(
    supabase_client=None, auto_start: bool = True
) -> Director:
    """Initialize the Director singleton."""
    global director
    if director is None:
        director = Director(supabase_client=supabase_client)
    if auto_start and not director.running:
        director.start()
    return director


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Kanyoza Director")
    parser.add_argument("--api", action="store_true", help="Run as API server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()

    d = init_director(auto_start=True)

    if args.api:
        import uvicorn
        app = create_app(d)

        def _signal_handler(signum, frame):
            log.info("Received signal %d — shutting down", signum)
            d.stop()
            raise SystemExit(0)

        signal.signal(signal.SIGTERM, _signal_handler)
        signal.signal(signal.SIGINT, _signal_handler)
        uvicorn.run(app, host=args.host, port=args.port)
    else:
        try:
            while d.running:
                time.sleep(5)
        except KeyboardInterrupt:
            d.stop()


if __name__ == "__main__":
    main()
