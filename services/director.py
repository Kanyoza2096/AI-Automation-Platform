"""
KANYOZA DIRECTOR v5.1 — "THE AUTONOMOUS CEO" (Enhanced Learning Edition)
=========================================================================

v5.1 CHANGES (Additive only — nothing removed):
  - ADDED: Context-aware learning (sector + capability tracking)
  - ADDED: Time-of-day pattern learning (morning/afternoon/evening/night)
  - ADDED: Recency-weighted confidence (7-day decay window)
  - ADDED: Duration tracking (spot slow capabilities)
  - ADDED: Automatic escalation for unreliable sector+capability combos
  - ADDED: Automatic skip for critically unreliable combos
  - ADDED: 7 new /learning/* audit endpoints
  - ADDED: Learning report embedded in status()
  - ADDED: 7 new /ooda/* endpoints (Autonomous Loop introspection)
  - ADDED: 11 new /meta/* endpoints (Meta Engine introspection)
  - All v5.0 features preserved (nothing removed)

v5.0 CHANGES (Additive only — nothing removed):
  - Async-native loop (asyncio.Task replaces threading.Thread)
  - Concurrent task execution (semaphore-limited)
  - Bounded task dict (prevents memory leak)
  - Health check integration (works with lifecycle manager)
  - Async Supabase storage (non-blocking)
  - Correlation ID tracking

RETAINED FROM V4.0 (Complete — nothing removed):
  - System Topology Model (25+ nodes)
  - Root Cause Analysis
  - Impact Assessment
  - Cascading Failure Prediction
  - Correlated Recovery
  - Learning Loop
  - Adaptive Thresholds
  - Predictive Failure Detection
  - Resource Throttling
  - Strategic Outlook
  - Resource Allocation
  - Executive Summary
  - Correlation Analysis
  - Initiatives Generation
  - Engine Auto-Discovery
  - Supabase Error Classification
  - Complete API (25+ endpoints)
  - Community Council Integration
  - Full Capability List (20+)
  - Daily Briefings
  - CLI Operation
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
import time
import uuid
from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from statistics import mean
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, TypeVar, Awaitable

log = logging.getLogger("kanyoza.director")

# Optional OpenTelemetry
try:
    from opentelemetry import trace
    tracer = trace.get_tracer("kanyoza.director", "5.1.0")
    HAS_TRACING = True
except ImportError:
    tracer = None
    HAS_TRACING = False

# Council Integration
try:
    from council.integration import get_council
    HAS_COUNCIL = True
except ImportError:
    HAS_COUNCIL = False




# Event Bus Integration
try:
    from core.unified_bus import get_unified_bus
    HAS_EVENT_BUS = True
except ImportError:
    HAS_EVENT_BUS = False


def _director_log_debug(msg: str, *args: Any) -> None:
    """Safe logger — uses logging directly so it works before `log` is bound."""
    logging.getLogger("kanyoza.director").debug(msg, *args)


async def _publish_event_async(event_name: str, payload: Dict[str, Any]) -> None:
    """Async publish on UnifiedEventBus."""
    if not HAS_EVENT_BUS:
        return
    pl = dict(payload or {})
    pl.setdefault("workspace_id", "platform")
    try:
        bus = await get_unified_bus()
        try:
            await bus.publish(event_name, pl, source="director", workspace_id="platform")
        except TypeError:
            await bus.publish(event_name, pl)
    except Exception as exc:
        _director_log_debug("Director publish failed for %s: %s", event_name, exc)


def _publish_event(event_name: str, payload: Dict[str, Any]) -> None:
    """Publish event — Director is async-native."""
    if not HAS_EVENT_BUS:
        return
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_publish_event_async(event_name, payload))
    except RuntimeError:
        _director_log_debug("Director: no running loop, skipping publish %s", event_name)
# ═══════════════════════════════════════════════════════════════════════════
# UTILITIES
# ═══════════════════════════════════════════════════════════════════════════

def now() -> datetime:
    return datetime.now(timezone.utc)

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

def iso_now() -> str:
    return utcnow().isoformat()

def iso(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)

def safe(value: Any) -> Any:
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
    return max(lo, min(hi, v))

def sanitize_error_message(exc: Exception, max_len: int = 300) -> str:
    raw = str(exc)
    raw = re.sub(r'File "[^"]*"', "File [redacted]", raw)
    raw = re.sub(r"/[^\s]*\.py", "[path_redacted]", raw)
    raw = re.sub(r"0x[0-9a-fA-F]+", "[addr]", raw)
    return raw[:max_len]

def generate_correlation_id() -> str:
    return uuid.uuid4().hex




# ═══════════════════════════════════════════════════════════════════════════
# SUPABASE ERROR CLASSIFIER
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
        if any(x in error_str for x in ('timeout', 'connection', 'network', 'dns', 'reset', 'refused', 'unreachable', 'temporary')):
            return True
        return False

    @staticmethod
    def is_permanent(error: Exception) -> bool:
        status = SupabaseErrorClassifier._extract_status(error)
        return status is not None and 400 <= status < 500 and status != 429

    @staticmethod
    def _extract_status(error: Exception) -> Optional[int]:
        if hasattr(error, 'code'):
            code = getattr(error, 'code', None)
            if isinstance(code, int):
                return code
            if isinstance(code, str) and code.isdigit():
                return int(code)
        if hasattr(error, 'response') and hasattr(error.response, 'status_code'):
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
# CIRCUIT BREAKER (Async)
# ═══════════════════════════════════════════════════════════════════════════

class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpenError(Exception):
    def __init__(self, name: str):
        self.name = name
        super().__init__(f"Circuit breaker '{name}' is OPEN")


class AsyncCircuitBreaker:
    """Async circuit breaker for failure isolation."""

    def __init__(self, name: str = "default", failure_threshold: int = 5, recovery_timeout: float = 30.0):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._lock = asyncio.Lock()

    @property
    def is_open(self) -> bool:
        if self.state == CircuitState.OPEN:
            if time.monotonic() - self._last_failure_time >= self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                log.info("Circuit breaker '%s': OPEN → HALF_OPEN", self.name)
                return False
            return True
        return False

    async def execute(self, func: Callable[[], Awaitable[Any]]) -> Any:
        if self.is_open:
            raise CircuitBreakerOpenError(self.name)
        try:
            result = await func()
            await self._record_success()
            return result
        except Exception as exc:
            await self._record_failure()
            raise

    async def _record_success(self) -> None:
        async with self._lock:
            if self.state == CircuitState.HALF_OPEN:
                self.state = CircuitState.CLOSED
                self._failure_count = 0
                log.info("Circuit breaker '%s': HALF_OPEN → CLOSED", self.name)
            elif self.state == CircuitState.CLOSED:
                self._failure_count = 0

    async def _record_failure(self) -> None:
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            if self.state == CircuitState.HALF_OPEN:
                self.state = CircuitState.OPEN
                log.warning("Circuit breaker '%s': HALF_OPEN → OPEN", self.name)
            elif self._failure_count >= self.failure_threshold and self.state == CircuitState.CLOSED:
                self.state = CircuitState.OPEN
                log.error("Circuit breaker '%s': CLOSED → OPEN (%d failures)", self.name, self._failure_count)


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
    correlation_id: str = field(default_factory=generate_correlation_id)

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
    correlation_id: str = field(default_factory=generate_correlation_id)

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
    analysis_id: str = field(default_factory=lambda: f"rca_{uuid.uuid4().hex[:12]}")

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
# SUPABASE STORE (Async)
# ═══════════════════════════════════════════════════════════════════════════

class AsyncSupabaseStore:
    """PostgreSQL-backed store via Supabase (async)."""

    def __init__(self, supabase_client):
        self.supabase = supabase_client
        self._client_factory: Optional[Callable[[], Any]] = None
        self._refresh_lock = asyncio.Lock()
        try:
            from services.supabase import create_fresh_service_client
            self._client_factory = create_fresh_service_client
        except Exception as exc:
            log.warning("Director DB: fresh Supabase client factory unavailable: %s", str(exc)[:200])
        self.circuit_breaker = AsyncCircuitBreaker(name="supabase_store")

    @staticmethod
    def _is_transport_failure(exc: Exception) -> bool:
        """Identify failures where reusing the HTTP transport is unsafe."""
        names = {
            "ConnectError", "ConnectTimeout", "ReadError", "ReadTimeout",
            "WriteError", "WriteTimeout", "PoolTimeout", "RemoteProtocolError",
            "LocalProtocolError", "NetworkError", "ConnectionError",
            "BrokenPipeError", "ConnectionResetError", "ConnectionAbortedError",
        }
        if exc.__class__.__name__ in names:
            return True
        text = str(exc).lower()
        return any(token in text for token in (
            "broken pipe", "connectionterminated", "connection terminated",
            "remoteprotocolerror", "connection reset", "connection aborted",
            "connection refused", "connection pool", "transport",
        ))

    async def _refresh_client(self) -> bool:
        if not self._client_factory:
            return False
        async with self._refresh_lock:
            try:
                client = await asyncio.to_thread(self._client_factory)
                if client is not None:
                    self.supabase = client
                    log.info("Director DB: refreshed Supabase transport after connection failure")
                    return True
            except Exception as exc:
                log.warning("Director DB: Supabase transport refresh failed: %s", str(exc)[:200])
        return False

    async def _execute_with_retry(self, operation_name: str, fn: Callable, max_retries: int = 2) -> Optional[Any]:
        if self.circuit_breaker.is_open:
            return None

        last_error = None
        for attempt in range(max_retries + 1):
            try:
                # Resolve the callable on every attempt so a refreshed client is used.
                current_fn = fn
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(None, current_fn)
                await self.circuit_breaker._record_success()
                return result
            except Exception as exc:
                last_error = exc
                status = SupabaseErrorClassifier._extract_status(exc)

                if SupabaseErrorClassifier.is_permanent(exc):
                    await self.circuit_breaker._record_failure()
                    log.error("Director DB: '%s' FAILED (HTTP %s, permanent): %s", operation_name, status, SupabaseErrorClassifier.describe(exc))
                    return None

                if attempt >= max_retries:
                    # Count one failed operation, not every internal retry attempt.
                    await self.circuit_breaker._record_failure()
                    log.error("Director DB: '%s' FAILED after %d retries (HTTP %s): %s", operation_name, max_retries, status, SupabaseErrorClassifier.describe(exc))
                    return None

                if self._is_transport_failure(exc):
                    refreshed = await self._refresh_client()
                    if refreshed:
                        # Rebuild the operation against the newly refreshed client.
                        # Lambdas in this class close over self.supabase, so the next
                        # attempt resolves the new client rather than the dead one.
                        pass

                delay = min(1.0 * (2 ** attempt), 10.0)
                delay *= random.uniform(0.75, 1.25)
                log.warning(
                    "Director DB: '%s' attempt %d/%d failed (%s). Retrying in %.1fs...",
                    operation_name, attempt + 1, max_retries,
                    "transport" if self._is_transport_failure(exc) else "retryable", delay,
                )
                await asyncio.sleep(delay)
        return None

    async def save_task(self, task: Task) -> None:
        await self._execute_with_retry(
            "insert into director_tasks",
            lambda: self.supabase.table("director_tasks").upsert({
                "id": task.id,
                "payload": json.dumps(safe(asdict(task))),
                "created_at": iso(task.created_at),
                "status": task.status.value,
            }).execute(),
        )

    async def get_task(self, task_id: str) -> Optional[dict]:
        result = await self._execute_with_retry(
            "get from director_tasks",
            lambda: self.supabase.table("director_tasks").select("*").eq("id", task_id).limit(1).execute(),
        )
        return dict(result.data[0]) if result and result.data else None

    async def list_tasks(self, limit: int = 200) -> List[dict]:
        result = await self._execute_with_retry(
            "list from director_tasks",
            lambda: self.supabase.table("director_tasks").select("*").order("created_at", desc=True).limit(limit).execute(),
        )
        return [dict(row) for row in (result.data or [])] if result else []

    async def save_decision(self, decision: Decision) -> None:
        await self._execute_with_retry(
            "insert into director_decisions",
            lambda: self.supabase.table("director_decisions").upsert({
                "id": decision.id,
                "payload": json.dumps(safe(asdict(decision))),
                "timestamp": iso(decision.timestamp),
            }).execute(),
        )

    async def list_decisions(self, limit: int = 100) -> List[dict]:
        result = await self._execute_with_retry(
            "list from director_decisions",
            lambda: self.supabase.table("director_decisions").select("*").order("timestamp", desc=True).limit(limit).execute(),
        )
        return [dict(row) for row in (result.data or [])] if result else []

    async def save_observation(self, observation: Observation) -> None:
        await self._execute_with_retry(
            "insert into director_observations",
            lambda: self.supabase.table("director_observations").insert({
                "payload": json.dumps(safe(asdict(observation))),
                "timestamp": iso(observation.timestamp),
            }).execute(),
        )

    async def list_observations(self, limit: int = 100) -> List[dict]:
        result = await self._execute_with_retry(
            "list from director_observations",
            lambda: self.supabase.table("director_observations").select("*").order("timestamp", desc=True).limit(limit).execute(),
        )
        return [dict(row) for row in (result.data or [])] if result else []

    async def save_approval(self, task_id: str, approved: bool, actor: str) -> None:
        await self._execute_with_retry(
            "insert into director_approvals",
            lambda: self.supabase.table("director_approvals").upsert({
                "task_id": task_id,
                "approved": approved,
                "actor": actor,
                "timestamp": iso_now(),
            }).execute(),
        )

    async def get_approval(self, task_id: str) -> Optional[dict]:
        result = await self._execute_with_retry(
            "get from director_approvals",
            lambda: self.supabase.table("director_approvals").select("*").eq("task_id", task_id).limit(1).execute(),
        )
        return dict(result.data[0]) if result and result.data else None

    async def save_learning(self, entry: dict) -> None:
        await self._execute_with_retry(
            "insert into director_learnings",
            lambda: self.supabase.table("director_learnings").upsert({
                "id": entry.get("id", f"learn_{uuid.uuid4().hex[:12]}"),
                "payload": json.dumps(safe(entry)),
                "timestamp": iso_now(),
            }).execute(),
        )

    async def list_learnings(self, limit: int = 100) -> List[dict]:
        result = await self._execute_with_retry(
            "list from director_learnings",
            lambda: self.supabase.table("director_learnings").select("*").order("timestamp", desc=True).limit(limit).execute(),
        )
        return [dict(row) for row in (result.data or [])] if result else []


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

    async def save_task(self, task: Task) -> None:
        if len(self.tasks) >= self.MAX_TASKS:
            completed = [tid for tid, t in self.tasks.items() if t.get("status") in ("completed", "failed", "cancelled")]
            for tid in completed[:100]:
                del self.tasks[tid]
        self.tasks[task.id] = safe(asdict(task))

    async def get_task(self, task_id: str) -> Optional[dict]:
        return self.tasks.get(task_id)

    async def list_tasks(self) -> List[dict]:
        return list(self.tasks.values())

    async def save_decision(self, decision: Decision) -> None:
        self.decisions.append(safe(asdict(decision)))

    async def list_decisions(self, limit: int = 100) -> List[dict]:
        return list(self.decisions)[-limit:]

    async def save_observation(self, observation: Observation) -> None:
        self.observations.append(safe(asdict(observation)))

    async def list_observations(self, limit: int = 100) -> List[dict]:
        return list(self.observations)[-limit:]

    async def save_approval(self, task_id: str, approved: bool, actor: str) -> None:
        if len(self.approvals) >= self.MAX_APPROVALS:
            oldest = list(self.approvals.keys())[:100]
            for key in oldest:
                del self.approvals[key]
        self.approvals[task_id] = {"task_id": task_id, "approved": approved, "actor": actor, "timestamp": iso_now()}

    async def get_approval(self, task_id: str) -> Optional[dict]:
        return self.approvals.get(task_id)

    async def save_learning(self, entry: dict) -> None:
        self.learnings.append(entry)

    async def list_learnings(self, limit: int = 100) -> List[dict]:
        return list(self.learnings)[-limit:]


# ═══════════════════════════════════════════════════════════════════════════
# POLICY ENGINE
# ═══════════════════════════════════════════════════════════════════════════

class Policy:
    """Security policy for autonomous actions."""

    CRITICAL = {
        "delete_brand", "delete_client_data", "drop_database", "reset_database",
        "rotate_credentials", "change_production_secrets", "disable_security_controls",
        "change_billing", "refund_client", "send_legal_notice",
        "deploy_breaking_change", "change_access_control",
    }

    def authority(self, capability: Capability) -> Authority:
        if not capability.enabled:
            return Authority.ALERT
        if capability.name in self.CRITICAL or capability.risk == Risk.CRITICAL:
            return Authority.SUGGEST
        return capability.authority

    def requires_approval(self, capability: Capability) -> bool:
        return (
            capability.name in self.CRITICAL
            or capability.risk == Risk.CRITICAL
            or self.authority(capability) in (Authority.SUGGEST, Authority.ALERT)
        )


# ═══════════════════════════════════════════════════════════════════════════
# SYSTEM TOPOLOGY
# ═══════════════════════════════════════════════════════════════════════════

class SystemTopology:
    """Dependency graph of all platform components."""

    def __init__(self):
        self.nodes: Dict[str, DependencyNode] = {}
        self._initialize_known_topology()

    def _initialize_known_topology(self) -> None:
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

        # Canonical dependency authority
        try:
            from core.engine_topology import CANONICAL_ENGINE_DEPENDENCIES
            for node in self.nodes.values():
                node.depends_on.clear()
                node.depended_by.clear()
            for dependent, dependencies in CANONICAL_ENGINE_DEPENDENCIES.items():
                if dependent in self.nodes:
                    for dependency in dependencies:
                        if dependency in self.nodes:
                            self.nodes[dependent].depends_on.add(dependency)
                            self.nodes[dependency].depended_by.add(dependent)
        except ImportError:
            pass

    def add_node(self, name: str, sector: Sector, criticality: int = 1) -> None:
        if name not in self.nodes:
            self.nodes[name] = DependencyNode(name=name, sector=sector, criticality=criticality)

    def add_dependency(self, dependent: str, dependency: str) -> None:
        self.add_node(dependent, Sector.INFRA)
        self.add_node(dependency, Sector.INFRA)
        self.nodes[dependent].depends_on.add(dependency)
        self.nodes[dependency].depended_by.add(dependent)

    def get_upstream(self, service: str, depth: int = 5) -> Set[str]:
        upstream = set()
        queue = deque([(service, 0)])
        visited = set()
        while queue:
            current, d = queue.popleft()
            if current in visited or d > depth:
                continue
            visited.add(current)
            node = self.nodes.get(current)
            if node:
                for dep in node.depends_on:
                    upstream.add(dep)
                    queue.append((dep, d + 1))
        return upstream

    def get_downstream(self, service: str, depth: int = 5) -> Set[str]:
        downstream = set()
        queue = deque([(service, 0)])
        visited = set()
        while queue:
            current, d = queue.popleft()
            if current in visited or d > depth:
                continue
            visited.add(current)
            node = self.nodes.get(current)
            if node:
                for dep in node.depended_by:
                    downstream.add(dep)
                    queue.append((dep, d + 1))
        return downstream

    def get_blast_radius(self, service: str) -> int:
        return len(self.get_downstream(service))

    def get_critical_downstream(self, service: str) -> List[str]:
        return [s for s in self.get_downstream(service) if self.nodes.get(s, DependencyNode(s, Sector.INFRA)).criticality >= 4]

    def update_health(self, service: str, health: Health) -> None:
        node = self.nodes.get(service)
        if node:
            node.last_health = health
            node.last_updated = now()

    def get_service_sector(self, service: str) -> Sector:
        node = self.nodes.get(service)
        return node.sector if node else Sector.INFRA

    def get_service_criticality(self, service: str) -> int:
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

    def analyze(self, observations: Dict[str, Observation]) -> Optional[RootCauseAnalysis]:
        failing = [n for n, o in observations.items() if o.status in (Health.CRITICAL, Health.OFFLINE)]
        degraded = [n for n, o in observations.items() if o.status == Health.DEGRADED]

        if not failing and not degraded:
            return None

        all_affected = failing + degraded

        if len(failing) == 1 and not degraded:
            service = failing[0]
            return self._create_analysis(service, [service], 0.9, f"Restart or repair {service}")

        if len(all_affected) < 2:
            return None

        upstream_sets = [self.topology.get_upstream(s) for s in all_affected]
        common = set.intersection(*upstream_sets) if upstream_sets else set()

        failing_common = common & set(failing)
        if failing_common:
            root = self._select_root_cause(failing_common)
            return self._create_analysis(root, all_affected, 0.85, f"Fix root cause {root}")

        infra = {"supabase", "redis", "event_bus"} & set(failing)
        if infra:
            root = self._select_root_cause(infra)
            return self._create_analysis(root, all_affected, 0.9, f"Restore infrastructure: {root}")

        degraded_common = common & set(degraded)
        if degraded_common:
            root = self._select_root_cause(degraded_common)
            return self._create_analysis(root, all_affected, 0.7, f"Stabilize {root}")

        root = max(all_affected, key=lambda s: self.topology.get_service_criticality(s))
        return self._create_analysis(root, all_affected, 0.5, f"Investigate {root}")

    def _select_root_cause(self, candidates: Set[str]) -> str:
        infra = {"supabase", "redis", "event_bus"} & candidates
        if infra:
            return min(infra, key=lambda s: self.topology.get_blast_radius(s))
        return max(candidates, key=lambda s: (self.topology.get_service_criticality(s), self.topology.get_blast_radius(s)))

    def _create_analysis(self, root_cause: str, affected: List[str], confidence: float, action: str) -> RootCauseAnalysis:
        analysis = RootCauseAnalysis(
            root_cause=root_cause,
            affected_services=affected,
            confidence=confidence,
            evidence=[{"type": "cascading_failure", "root": root_cause, "affected": affected}],
            timestamp=now(),
            recommended_action=action,
        )
        self.analyses.append(analysis)
        return analysis

    def get_recent_analyses(self, limit: int = 10) -> List[RootCauseAnalysis]:
        return list(self.analyses)[-limit:]


# ═══════════════════════════════════════════════════════════════════════════
# IMPACT ASSESSOR
# ═══════════════════════════════════════════════════════════════════════════

class ImpactAssessor:
    """Assesses blast radius and severity of failures."""

    def __init__(self, topology: SystemTopology):
        self.topology = topology
        self.impact_history: deque = deque(maxlen=200)

    def assess_impact(self, service: str) -> Dict[str, Any]:
        downstream = self.topology.get_downstream(service, depth=5)
        critical = self.topology.get_critical_downstream(service)
        severity = self._calculate_severity(len(downstream), len(critical))

        impact = {
            "service": service,
            "blast_radius": len(downstream),
            "downstream_services": sorted(downstream),
            "critical_services_affected": sorted(critical),
            "severity": severity,
            "estimated_recovery_minutes": {"low": 5, "medium": 15, "high": 30, "critical": 60}[severity],
            "timestamp": now(),
        }
        self.impact_history.append(impact)
        return impact

    def _calculate_severity(self, downstream_count: int, critical_count: int) -> str:
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

    def predict_cascading_failures(self, failing_service: str, observations: Dict[str, Observation]) -> List[Dict[str, Any]]:
        downstream = self.topology.get_downstream(failing_service, depth=3)
        predictions = []

        for service in downstream:
            obs = observations.get(service)
            if obs is None:
                continue
            if obs.status == Health.NOMINAL:
                predictions.append({"service": service, "current_status": "healthy", "predicted_status": "degraded", "time_to_failure_minutes": 5, "confidence": 0.75, "reason": f"Upstream {failing_service} failed"})
            elif obs.status == Health.DEGRADED:
                predictions.append({"service": service, "current_status": "degraded", "predicted_status": "critical", "time_to_failure_minutes": 10, "confidence": 0.6, "reason": f"Upstream {failing_service} failed"})
            elif obs.status == Health.UNKNOWN:
                predictions.append({"service": service, "current_status": "unknown", "predicted_status": "likely_degraded", "time_to_failure_minutes": 15, "confidence": 0.5, "reason": f"Depends on failed {failing_service}"})

        self.predictions.extend(predictions)
        return predictions


# ═══════════════════════════════════════════════════════════════════════════
# CORRELATED RECOVERY
# ═══════════════════════════════════════════════════════════════════════════

class CorrelatedRecovery:
    """Coordinates recovery actions for correlated failures."""

    def __init__(self):
        self.recovery_plans: deque = deque(maxlen=100)

    def create_recovery_plan(self, root_cause: str, affected_services: List[str]) -> Dict[str, Any]:
        plan = {
            "id": f"recovery_{uuid.uuid4().hex[:12]}",
            "root_cause": root_cause,
            "affected_services": affected_services,
            "steps": [
                {"order": 1, "action": f"Restore {root_cause}", "type": "root_cause_fix", "status": "pending"},
                {"order": 2, "action": "Wait for stabilization", "type": "stabilization", "duration_seconds": 30, "status": "pending"},
                {"order": 3, "action": "Verify downstream recovery", "type": "verification", "services": affected_services, "status": "pending"},
                {"order": 4, "action": "Restart unrecovered services", "type": "remediation", "status": "pending"},
            ],
            "created_at": now(),
            "status": "pending",
        }
        self.recovery_plans.append(plan)
        return plan

    def get_recovery_plans(self, limit: int = 10) -> List[Dict[str, Any]]:
        return list(self.recovery_plans)[-limit:]


# ═══════════════════════════════════════════════════════════════════════════
# LEARNING LOOP v2 (Enhanced — Context-Aware, Time-Aware, Decay-Aware)
# ═══════════════════════════════════════════════════════════════════════════

class LearningLoop:
    """
    Feeds task outcomes back into decision-making.

    v2 enhancements (deterministic, no ML):
      - Context-aware learning: tracks success by sector + capability
      - Time-of-day patterns: learns when capabilities work best
      - Recency-weighted rates: recent outcomes count more than old ones
      - Duration tracking: learns which capabilities are slow
    """

    def __init__(self, max_signals: int = 1000, decay_days: int = 7):
        self.signals: deque = deque(maxlen=max_signals)
        self.capability_stats: Dict[str, Dict[str, float]] = {}
        self.failure_patterns: Dict[str, deque] = defaultdict(lambda: deque(maxlen=50))

        # Context-aware learning
        self.context_stats: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {"successes": 0, "failures": 0}
        )

        # Time-of-day learning
        self.time_stats: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {"successes": 0, "failures": 0}
        )

        # Duration tracking
        self.duration_stats: Dict[str, deque] = defaultdict(lambda: deque(maxlen=100))

        # Config
        self.decay_days = decay_days

    # ──────────────────────────────────────────────────────────────────
    # TIME CONTEXT
    # ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _get_time_context(dt: Optional[datetime] = None) -> str:
        """Classify time-of-day into a bucket."""
        h = (dt or now()).hour
        if 0 <= h < 6:
            return "night"
        if 6 <= h < 12:
            return "morning"
        if 12 <= h < 18:
            return "afternoon"
        return "evening"

    @staticmethod
    def _get_day_context(dt: Optional[datetime] = None) -> str:
        """Classify day into weekday/weekend."""
        d = (dt or now()).weekday()
        return "weekend" if d >= 5 else "weekday"

    def _context_key(self, sector: Sector, capability: str) -> str:
        return f"{sector.value}:{capability}"

    def _time_key(self, sector: Sector, capability: str) -> str:
        return f"{sector.value}:{capability}:{self._get_time_context()}"

    # ──────────────────────────────────────────────────────────────────
    # RECORDING
    # ──────────────────────────────────────────────────────────────────

    def record_outcome(self, task: Task, duration_ms: float) -> None:
        """Record a task outcome (enhanced with context)."""
        outcome = "success" if task.status == Status.COMPLETED else "failure"

        signal = LearningSignal(
            capability=task.capability,
            sector=task.sector,
            outcome=outcome,
            duration_ms=duration_ms,
            error_type=task.error[:100] if task.error else None,
            context={
                "authority": task.authority.value,
                "risk": task.risk.value,
                "confidence": task.confidence,
                "time_context": self._get_time_context(),
                "day_context": self._get_day_context(),
            },
            timestamp=now(),
        )
        self.signals.append(signal)

        # Global stats
        stats = self.capability_stats.setdefault(
            task.capability,
            {"successes": 0, "failures": 0, "total_duration_ms": 0},
        )
        stats["successes" if outcome == "success" else "failures"] += 1
        stats["total_duration_ms"] += duration_ms

        # Context stats (sector + capability)
        ctx_key = self._context_key(task.sector, task.capability)
        self.context_stats[ctx_key]["successes" if outcome == "success" else "failures"] += 1

        # Time-of-day stats
        time_key = self._time_key(task.sector, task.capability)
        self.time_stats[time_key]["successes" if outcome == "success" else "failures"] += 1

        # Duration tracking
        self.duration_stats[task.capability].append(duration_ms)

        # Failure patterns
        if outcome == "failure":
            self.failure_patterns[task.capability].append({
                "timestamp": signal.timestamp,
                "error": signal.error_type,
                "context": ctx_key,
            })

    # ──────────────────────────────────────────────────────────────────
    # QUERY METHODS
    # ──────────────────────────────────────────────────────────────────

    def get_success_rate(self, capability: str) -> Optional[float]:
        """All-time success rate for a capability."""
        stats = self.capability_stats.get(capability)
        if not stats:
            return None
        total = stats["successes"] + stats["failures"]
        return stats["successes"] / total if total > 0 else None

    def get_recent_success_rate(self, capability: str, days: Optional[int] = None) -> Optional[float]:
        """Recency-weighted success rate — recent outcomes weighted more."""
        days = days or self.decay_days
        cutoff = now() - timedelta(days=days)

        recent = [
            s for s in self.signals
            if s.capability == capability and s.timestamp >= cutoff
        ]
        if not recent:
            return self.get_success_rate(capability)

        successes = sum(1 for s in recent if s.outcome == "success")
        return successes / len(recent)

    def get_context_success_rate(self, sector: Sector, capability: str) -> Optional[float]:
        """Success rate for a specific sector + capability combo."""
        ctx_key = self._context_key(sector, capability)
        stats = self.context_stats.get(ctx_key)
        if not stats:
            return None
        total = stats["successes"] + stats["failures"]
        if total < 3:
            return None
        return stats["successes"] / total

    def get_time_success_rate(self, sector: Sector, capability: str, time_context: Optional[str] = None) -> Optional[float]:
        """Success rate for sector + capability + time-of-day."""
        time_context = time_context or self._get_time_context()
        time_key = f"{sector.value}:{capability}:{time_context}"
        stats = self.time_stats.get(time_key)
        if not stats:
            return None
        total = stats["successes"] + stats["failures"]
        if total < 3:
            return None
        return stats["successes"] / total

    def get_avg_duration_ms(self, capability: str) -> Optional[float]:
        """Average duration for a capability."""
        durations = self.duration_stats.get(capability)
        if not durations:
            return None
        return mean(durations)

    def get_recurring_failures(self, capability: str, threshold: int = 3) -> bool:
        """Check for recurring failures."""
        return len(self.failure_patterns.get(capability, [])) >= threshold

    # ──────────────────────────────────────────────────────────────────
    # DECISION HELPERS
    # ──────────────────────────────────────────────────────────────────

    def should_escalate(self, sector: Sector, capability: str) -> bool:
        """Escalate to approval if context or time-specific success rate is low."""
        ctx_rate = self.get_context_success_rate(sector, capability)
        if ctx_rate is not None and ctx_rate < 0.5:
            return True

        time_rate = self.get_time_success_rate(sector, capability)
        if time_rate is not None and time_rate < 0.4:
            return True

        return False

    def should_skip(self, sector: Sector, capability: str) -> bool:
        """Skip if context or time-specific success rate is critically low."""
        ctx_rate = self.get_context_success_rate(sector, capability)
        if ctx_rate is not None and ctx_rate < 0.2:
            return True

        time_rate = self.get_time_success_rate(sector, capability)
        if time_rate is not None and time_rate < 0.15:
            return True

        return False

    def adjust_confidence(self, task: Task) -> float:
        """
        Compute adjusted confidence using:
          - Global success rate (30% weight)
          - Recent success rate (40% weight)
          - Context success rate (30% weight)
        """
        weights = []
        rates = []

        global_rate = self.get_success_rate(task.capability)
        if global_rate is not None:
            rates.append(global_rate)
            weights.append(0.30)

        recent_rate = self.get_recent_success_rate(task.capability)
        if recent_rate is not None:
            rates.append(recent_rate)
            weights.append(0.40)

        ctx_rate = self.get_context_success_rate(task.sector, task.capability)
        if ctx_rate is not None:
            rates.append(ctx_rate)
            weights.append(0.30)

        if not rates:
            return task.confidence

        total_weight = sum(weights)
        weighted_rate = sum(r * w for r, w in zip(rates, weights)) / total_weight

        # Blend with original confidence (30% heuristic, 70% learned)
        return clamp(task.confidence * 0.3 + weighted_rate * 0.7, 0.1, 0.99)

    # ──────────────────────────────────────────────────────────────────
    # REPORTING
    # ──────────────────────────────────────────────────────────────────

    def get_learning_report(self) -> Dict[str, Any]:
        """Detailed learning report for observability."""
        return {
            "total_signals": len(self.signals),
            "capabilities_tracked": len(self.capability_stats),
            "contexts_tracked": len(self.context_stats),
            "time_buckets_tracked": len(self.time_stats),
            "capabilities": {
                cap: {
                    "success_rate": round(self.get_success_rate(cap) or 0, 2),
                    "recent_rate": round(self.get_recent_success_rate(cap) or 0, 2),
                    "avg_duration_ms": round(self.get_avg_duration_ms(cap) or 0, 0),
                    "total": stats["successes"] + stats["failures"],
                }
                for cap, stats in self.capability_stats.items()
            },
        }


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

    def get_threshold(self, name: str) -> float:
        return self.thresholds.get(name, 80.0)

    def update_threshold(self, name: str, actual_value: float, outcome: str) -> None:
        current = self.thresholds.get(name, 80.0)
        if outcome == "false_positive":
            self.thresholds[name] = clamp(current - 5, lo=50.0, hi=95.0)
        elif outcome == "true_positive":
            self.thresholds[name] = clamp(current + 2, lo=50.0, hi=95.0)
        elif outcome == "missed":
            self.thresholds[name] = clamp(current + 5, lo=50.0, hi=95.0)


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
            return Observation(self.name, self.sector, Health.CRITICAL, issues=[{"type": "observer_failure", "error": sanitize_error_message(exc)}])

        if isinstance(result, Observation):
            return result

        status_str = str(result.get("status", "unknown")).lower()
        status_map = {"ok": Health.NOMINAL, "healthy": Health.NOMINAL, "nominal": Health.NOMINAL, "degraded": Health.DEGRADED, "critical": Health.CRITICAL, "offline": Health.OFFLINE}

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
    ("services.job_stats_service", "get_job_stats_service", "job_stats_service", Sector.INFRA),
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
                    return {"status": r.get("overall", r.get("status", "healthy")), "metrics": r}
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
        return {"status": "unknown", "metrics": {"engine": engine_name, "health_contract": "missing"}}
    return observe


def auto_discover_engines(director: "Director", container) -> int:
    discovered = 0
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
                director.register_observer(FunctionObserver(display_name, sector, _make_observer(display_name, engine)))
                discovered += 1
        except ImportError:
            log.debug("Director: %s not available", display_name)
        except Exception as exc:
            log.warning("Director: %s failed: %s", display_name, sanitize_error_message(exc))

    plugins = getattr(container, "industry_plugins", {})
    for name, plugin in plugins.items():
        sector = _infer_plugin_sector(name)
        director.register_observer(FunctionObserver(name, sector, _make_observer(name, plugin)))
        discovered += 1

    log.info("Director: Auto-discovered %d engines", discovered)
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
# THE DIRECTOR v5.1 — COMPLETE ENHANCED
# ═══════════════════════════════════════════════════════════════════════════

class Director:
    """The Autonomous CEO — v5.1 Complete Enhanced Learning Edition."""

    VERSION = "5.1.0"
    MAX_TASKS = 1000
    MAX_CONCURRENT_EXECUTIONS = 5

    def __init__(self, supabase_client=None, interval: int = 60):
        # Storage
        self.store = AsyncSupabaseStore(supabase_client) if supabase_client else InMemoryStore()
        log.info("Director storage: %s", "Supabase" if supabase_client else "In-Memory")

        # Timing
        self.interval = max(10, interval)

        # Core
        self.policy = Policy()
        self.running = False
        self.started = now()

        # Async state
        self._loop_task: Optional[asyncio.Task] = None
        self._event_task: Optional[asyncio.Task] = None
        self._execution_semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_EXECUTIONS)
        self._executing: Set[str] = set()
        self._lock = asyncio.Lock()

        # Registries
        self.observers: Dict[str, FunctionObserver] = {}
        self.capabilities: Dict[str, Capability] = {}
        self.tasks: Dict[str, Task] = {}
        self.goals: Dict[str, Goal] = {}

        # Bounded history
        self.history: deque = deque(maxlen=5000)
        self.decisions: deque = deque(maxlen=2000)
        self.observations: Dict[str, Observation] = {}
        self.briefings: deque = deque(maxlen=200)
        self._alert_history: deque = deque(maxlen=500)

        # Event queue
        self._event_queue: deque = deque(maxlen=2000)
        self._event_callback: Optional[Callable] = None
        self._alert_callback: Optional[Callable] = None

        # State
        self.last_plan: Optional[datetime] = None
        self.last_observation: Optional[datetime] = None
        self._last_daily_briefing: Optional[str] = None
        self._consecutive_failures: int = 0
        self._observation_circuit_open: bool = False
        self._observation_circuit_reset_time: float = 0.0

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

        # Council integration
        self.council = get_council() if HAS_COUNCIL else None
        if self.council:
            log.info("Director: Community Council activated (%d members)", len(self.council.members))

        # Initialize
        self._init_goals()
        self._init_capabilities()
        log.info("👔 KANYOZA DIRECTOR v%s initialized — COMPLETE ENHANCED LEARNING", self.VERSION)

    # ═══════════════════════════════════════════════════════════════════
    # LIFECYCLE
    # ═══════════════════════════════════════════════════════════════════

    @property
    def service_name(self) -> str:
        return "director"

    async def start(self) -> None:
        """Start the Director (async)."""
        if self.running:
            return
        self.running = True
        self.started = now()

        self._loop_task = asyncio.create_task(self._main_loop(), name="director-loop")
        self._event_task = asyncio.create_task(self._event_drain_loop(), name="director-events")

        asyncio.create_task(self._startup())

        log.info("👔 Director v%s ONLINE — async-native, enhanced learning", self.VERSION)

    async def stop(self) -> None:
        """Stop the Director (async)."""
        if not self.running:
            return

        log.info("Stopping Director...")
        self.running = False

        for task in [self._loop_task, self._event_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        self._loop_task = None
        self._event_task = None
        log.info("👔 Director OFFLINE")

    async def check_health_async(self) -> Dict[str, Any]:
        """Health check for lifecycle integration."""
        health = self.health()
        return {
            "status": "healthy" if self.running else "stopped",
            "score": health["score"],
            "version": self.VERSION,
            "observers": len(self.observers),
            "capabilities": len(self.capabilities),
            "pending_approvals": sum(1 for t in self.tasks.values() if t.status == Status.APPROVAL),
            "event_queue_depth": len(self._event_queue),
            "executing_tasks": len(self._executing),
            "topology_nodes": len(self.topology.nodes),
            "learning_signals": len(self.learning_loop.signals),
        }

    # ═══════════════════════════════════════════════════════════════════
    # MAIN LOOP
    # ═══════════════════════════════════════════════════════════════════

    async def _main_loop(self) -> None:
        """Main async loop."""
        last_minute = None

        while self.running:
            if self._observation_circuit_open:
                if time.monotonic() > self._observation_circuit_reset_time:
                    self._observation_circuit_open = False
                    self._consecutive_failures = 0
                    log.info("Director: Observation circuit breaker reset")
                else:
                    await asyncio.sleep(self.interval)
                    continue

            try:
                await self._observe_async()
                self._consecutive_failures = 0

                if self.last_plan is None or (now() - self.last_plan).total_seconds() >= 1800:
                    await self._build_plan_async()

                await self._execute_due_async()

                current_minute = now().strftime("%Y-%m-%d-%H-%M")
                if current_minute != last_minute:
                    last_minute = current_minute
                    await self._check_briefings()

            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._consecutive_failures += 1
                log.exception("Director loop error (%d consecutive): %s", self._consecutive_failures, sanitize_error_message(exc))

                if self._consecutive_failures >= 3:
                    backoff = min(300, 30 * self._consecutive_failures)
                    self._observation_circuit_open = True
                    self._observation_circuit_reset_time = time.monotonic() + backoff
                    log.critical("Director: Circuit breaker OPEN — backing off for %ds", backoff)

            await asyncio.sleep(self.interval)

    async def _event_drain_loop(self) -> None:
        """Async event drain loop."""
        while self.running:
            self._drain_events()
            await asyncio.sleep(15)
        self._drain_events()

    async def _startup(self) -> None:
        """Async startup."""
        await asyncio.sleep(2)
        try:
            await self._observe_async()
            await self._build_plan_async()
            await self._execute_due_async()
            self.briefing(Briefing.CHECKPOINT)
        except Exception as exc:
            log.exception("Startup failed: %s", sanitize_error_message(exc))

    async def _check_briefings(self) -> None:
        """Check and send briefings."""
        hour = now().hour
        minute = now().minute

        if hour == 8 and minute == 0:
            today = now().strftime("%Y-%m-%d")
            if self._last_daily_briefing != today:
                self._last_daily_briefing = today
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._send_daily_briefing)

        if minute == 0 and hour in (6, 12, 18, 22):
            kind = Briefing.MORNING if hour == 6 else Briefing.EVENING if hour == 22 else Briefing.CHECKPOINT
            self.briefing(kind)

    # ═══════════════════════════════════════════════════════════════════
    # OBSERVATION
    # ═══════════════════════════════════════════════════════════════════

    async def _observe_async(self) -> Dict[str, Observation]:
        """Async observation."""
        loop = asyncio.get_event_loop()
        new_observations = {}

        tasks = []
        for name, observer in self.observers.items():
            task = loop.run_in_executor(None, observer.observe)
            tasks.append((name, task))

        for name, task in tasks:
            try:
                obs = await task
                new_observations[name] = obs
                await self.store.save_observation(obs)
            except Exception as exc:
                new_observations[name] = Observation(
                    name, Sector.INFRA, Health.DEGRADED,
                    issues=[{"type": "observer_error", "error": sanitize_error_message(exc)}],
                )

        async with self._lock:
            self.observations = new_observations

        self.last_observation = now()

        for name, obs in new_observations.items():
            self.topology.update_health(name, obs.status)

        self._run_diagnosis(new_observations)

        return self.observations

    def _run_diagnosis(self, observations: Dict[str, Observation]) -> None:
        """Run full diagnostic analysis."""
        root_cause = self.root_cause_analyzer.analyze(observations)
        if root_cause:
            self.current_root_cause = root_cause
            self.current_impact = self.impact_assessor.assess_impact(root_cause.root_cause)
            self.current_predictions = self.cascading_predictor.predict_cascading_failures(root_cause.root_cause, observations)

            log.warning("🔍 ROOT CAUSE: %s (affects %d, confidence %.0f%%)", root_cause.root_cause, len(root_cause.affected_services), root_cause.confidence * 100)

            plan = self.recovery_coordinator.create_recovery_plan(root_cause.root_cause, root_cause.affected_services)
            log.info("📋 RECOVERY PLAN: %s", plan["id"])

            self._create_task(
                Sector.HEALTH,
                f"Fix root cause: {root_cause.root_cause}",
                "restart_failed_service",
                f"Root cause affects {len(root_cause.affected_services)} services",
                0.95,
                {"service_name": root_cause.root_cause, "affected_services": root_cause.affected_services, "recovery_plan_id": plan["id"]},
                root_cause.root_cause,
            )

            self._send_diagnostic_alert()

    def _send_diagnostic_alert(self) -> None:
        """Send diagnostic alert."""
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
                    parts.append(f"  • {p['service']} → {p['predicted_status']} in {p['time_to_failure_minutes']}min")

            parts.append("")
            parts.append(f"<b>Action:</b> {rca.recommended_action}")
            parts.append(f"━━━━━━━━━━━━━━━━━━━")
            parts.append(f"<i>{now().strftime('%H:%M:%S UTC')}</i>")

            self._send_telegram("\n".join(parts))
        except Exception as exc:
            log.error("Diagnostic alert failed: %s", sanitize_error_message(exc))

    # ═══════════════════════════════════════════════════════════════════
    # PLANNING
    # ═══════════════════════════════════════════════════════════════════

    async def _build_plan_async(self) -> dict:
        """Build plan asynchronously."""
        tasks = self.decide()
        self.last_plan = now()
        return {"date": now().date().isoformat(), "tasks": [safe(asdict(t)) for t in tasks]}

    # ═══════════════════════════════════════════════════════════════════
    # EXECUTION
    # ═══════════════════════════════════════════════════════════════════

    async def _execute_due_async(self) -> None:
        """Execute due tasks concurrently."""
        due = [tid for tid, t in self.tasks.items() if t.status in (Status.PLANNED, Status.APPROVED) and tid not in self._executing]

        if not due:
            return

        await asyncio.gather(*[self._execute_with_semaphore(tid) for tid in due], return_exceptions=True)

    async def _execute_with_semaphore(self, task_id: str) -> None:
        """Execute task with concurrency limit."""
        async with self._execution_semaphore:
            await self._execute_async(task_id)

    async def _invoke_capability_handler(self, handler: Callable, params: dict) -> Any:
        """Run a capability handler whether it is async or sync."""
        if handler is None:
            return {"status": "error", "reason": "no handler bound"}

        params = params or {}

        if inspect.iscoroutinefunction(handler):
            return await handler(params)

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, handler, params)

        if inspect.isawaitable(result):
            return await result
        return result

    async def execute_capability(self, cap_name: str, params: Optional[dict] = None) -> Dict[str, Any]:
        """Public entry for Reliability / Trend bridge to run a bound hand."""
        params = params or {}
        cap = self.capabilities.get(cap_name)
        if not cap:
            return {"ok": False, "error": f"unknown_capability:{cap_name}"}
        if not cap.enabled:
            return {"ok": False, "error": f"capability_disabled:{cap_name}"}
        handler = getattr(cap, "handler", None)
        if handler is None:
            return {"ok": False, "error": f"no_handler:{cap_name}"}
        hname = getattr(handler, "__name__", "") or ""
        if hname.startswith("placeholder_") or hname == "<lambda>":
            return {
                "ok": False,
                "error": "handler_not_bound",
                "capability": cap_name,
                "status": "unavailable",
            }
        try:
            result = await self._invoke_capability_handler(handler, params)
            ok = self._handler_result_ok(result)
            out = {"ok": ok, "capability": cap_name, "result": result}
            if not ok and isinstance(result, dict):
                out["error"] = result.get("error") or result.get("status") or "capability_failed"
            return out
        except Exception as exc:
            log.exception("execute_capability %s failed", cap_name)
            return {"ok": False, "error": str(exc)[:300], "capability": cap_name}

    async def _execute_async(self, task_id: str) -> Task:
        """Execute a single task (async)."""
        task = self.tasks.get(task_id)
        if not task:
            raise KeyError(f"Task {task_id} not found")

        async with self._lock:
            if task_id in self._executing:
                return task
            self._executing.add(task_id)

        start = time.monotonic()

        try:
            cap = self.capabilities[task.capability]

            if not cap.enabled:
                task.status = Status.SKIPPED
                task.error = "Capability disabled"
                await self.store.save_task(task)
                return task

            if self.policy.requires_approval(cap) and task.status != Status.APPROVED:
                task.status = Status.APPROVAL
                await self.store.save_task(task)
                return task

            task.status = Status.EXECUTING
            task.executed_at = now()
            await self.store.save_task(task)

            result = await self._invoke_capability_handler(cap.handler, task.params)

            if not self._handler_result_ok(result):
                err = None
                if isinstance(result, dict):
                    err = result.get("error") or result.get("message") or result.get("status")
                task.status = Status.FAILED
                task.error = str(err or "capability_result_not_ok")[:500]
                task.result = safe(result or {"ok": False})
                self.history.append(task)
                await self.store.save_task(task)
                _publish_event(
                    "task_failed",
                    {
                        "task_id": task.id,
                        "description": task.description,
                        "capability": task.capability,
                        "error": task.error,
                    },
                )
                self._record_decision(f"Failed: {task.description}", task.error, task, "failed")
                self._learn(task)
                return task

            task.status = Status.COMPLETED
            task.result = safe(result or {})
            self.history.append(task)
            await self.store.save_task(task)

            _publish_event("task_completed", {"task_id": task.id, "description": task.description, "capability": task.capability})

            self._record_decision(f"Executed: {task.description}", task.reason, task)
            self._learn(task)
            return task

        except Exception as exc:
            task.status = Status.FAILED
            task.error = sanitize_error_message(exc)
            task.result = {"error": sanitize_error_message(exc)}
            self.history.append(task)
            await self.store.save_task(task)

            _publish_event("task_failed", {"task_id": task.id, "description": task.description, "error": task.error})

            self._record_decision(f"Failed: {task.description}", sanitize_error_message(exc), task, "failed")
            self._learn(task)
            return task

        finally:
            duration = (time.monotonic() - start) * 1000
            self.learning_loop.record_outcome(task, duration)
            async with self._lock:
                self._executing.discard(task_id)

    # ═══════════════════════════════════════════════════════════════════
    # COUNCIL VOTING
    # ═══════════════════════════════════════════════════════════════════

    async def _run_council_vote(self, task: Task) -> Dict:
        """Run council vote."""
        if not self.council:
            return {"decision": "APPROVED", "reason": "No council"}

        brand_context = {"industry": task.params.get("industry", "general"), "task_sector": task.sector.value}

        proposal = {
            "topic": task.description,
            "content": task.description,
            "hour": now().hour,
            "day": now().strftime("%A").lower(),
            "engagement_ratio": task.confidence,
            "sample_count": int(task.confidence * 30),
            "has_cta": "boost" in task.capability,
            "system_health": self.health()["score"],
            "engagement_trend": 0.0,
            "topic_source": "trend" if "trend" in task.reason.lower() else "director",
        }

        loop = asyncio.get_event_loop()
        vote_result = await loop.run_in_executor(None, self.council.vote_on, brand_context, proposal)

        summary = self.council.get_summary(vote_result)
        log.info("Council vote for '%s':\n%s", task.description[:50], summary)

        _publish_event("council_voted", {"task_id": task.id, "decision": vote_result["decision"], "approve_ratio": vote_result["approve_ratio"]})

        return vote_result

    # ═══════════════════════════════════════════════════════════════════
    # DECISION MAKING
    # ═══════════════════════════════════════════════════════════════════

    def _create_task(self, sector: Sector, desc: str, cap_name: str, reason: str, confidence: float = 0.8, params: dict = None, source: str = None) -> Task:
        """Create task with bounding."""
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
            status=Status.APPROVAL if self.policy.requires_approval(cap) else Status.PLANNED,
            confidence=confidence,
            reason=reason,
            sources=[source] if source else [],
        )

        self.tasks[task.id] = task

        if len(self.tasks) > self.MAX_TASKS:
            removable = [tid for tid, t in self.tasks.items() if t.status in (Status.COMPLETED, Status.FAILED, Status.CANCELLED)]
            for tid in removable[:500]:
                del self.tasks[tid]

        if len(self.tasks) > self.MAX_TASKS:
            oldest = sorted(self.tasks.items(), key=lambda x: x[1].created_at)[:500]
            for tid, _ in oldest:
                del self.tasks[tid]

        _publish_event("task_created", {"task_id": task.id, "description": desc, "capability": cap_name, "sector": sector.value})

        return task

    def decide(self) -> List[Task]:
        """Decide what to do."""
        decisions = []
        health = self.health()

        if health["score"] < 90:
            decisions.append(self._create_task(
                Sector.HEALTH, "Run complete platform health scan", "run_health_scan",
                f"Health score is {health['score']}", 0.99,
            ))

        for obs in self.observations.values():
            for issue in obs.issues:
                typ = str(issue.get("type", "")).lower()
                if typ in {"failed_service", "service_down", "worker_failed"}:
                    decisions.append(self._create_task(Sector.HEALTH, "Recover failed service", "restart_failed_service", "Recoverable service failure", 0.9, issue, obs.source))
                elif typ in {"security_issue", "vulnerability", "credential_exposure"}:
                    decisions.append(self._create_task(Sector.SECURITY, "Prepare security remediation", "prepare_security_remediation", "Security issue observed", 0.97, issue, obs.source))
                elif typ in {"brand_inactive", "engagement_decline"}:
                    decisions.append(self._create_task(Sector.BRANDS, "Queue brand revival", "queue_brand_revival", "Brand performance declined", 0.87, issue, obs.source))
                elif typ in {"content_gap", "schedule_gap"}:
                    decisions.append(self._create_task(Sector.CONTENT, "Optimize content schedule", "optimize_content_schedule", "Content gap observed", 0.88, issue, obs.source))

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
                decisions.append(self._create_task(goal.sector, f"Goal-driven: {goal.name}", cap_name, f"Goal: {goal.name}", conf))

        if not decisions:
            decisions.append(self._create_task(Sector.SELF, "Run Director self-diagnostic", "self_diagnostic", "No urgent work; inspect own state", 0.95))

        # Deduplicate
        seen = set()
        unique = []
        for task in decisions:
            key = (task.sector, task.capability, task.description)
            if key not in seen:
                seen.add(key)
                unique.append(task)

        # ─────────────────────────────────────────────────────────────
        # APPLY ENHANCED LEARNING
        # ─────────────────────────────────────────────────────────────
        for task in unique:
            task.confidence = self.learning_loop.adjust_confidence(task)

            if self.learning_loop.should_escalate(task.sector, task.capability):
                task.status = Status.APPROVAL
                task.reason += f" (low success rate for {task.sector.value}:{task.capability})"

            if self.learning_loop.should_skip(task.sector, task.capability):
                task.status = Status.SKIPPED
                task.error = f"Capability {task.capability} unreliable for {task.sector.value}"

            if self.learning_loop.get_recurring_failures(task.capability, threshold=3):
                task.status = Status.APPROVAL
                task.reason += " (recurring failures detected)"

        return sorted(unique, key=self._priority, reverse=True)

    def _priority(self, task: Task) -> float:
        return (
            {Authority.ALERT: 1000, Authority.SUGGEST: 700, Authority.NOTIFY: 500, Authority.AUTO: 300}[task.authority]
            + {Risk.CRITICAL: 100, Risk.HIGH: 70, Risk.MEDIUM: 40, Risk.LOW: 10}[task.risk]
            + task.confidence * 100
        )

    # ═══════════════════════════════════════════════════════════════════
    # APPROVALS
    # ═══════════════════════════════════════════════════════════════════

    def approve(self, task_id: str, actor: str = "admin") -> Task:
        task = self.tasks.get(task_id)
        if task is None:
            raise KeyError(f"Task {task_id} not found")
        if task.status not in (Status.APPROVAL, Status.FLAGGED):
            raise ValueError("Task is not awaiting approval")
        task.status = Status.APPROVED
        task.approved_at = now()
        task.approved_by = actor
        return task

    def reject(self, task_id: str, actor: str = "admin", reason: str = "") -> Task:
        task = self.tasks.get(task_id)
        if task is None:
            raise KeyError(f"Task {task_id} not found")
        task.status = Status.CANCELLED
        task.error = f"Rejected by {actor}: {reason}"
        return task

    # ═══════════════════════════════════════════════════════════════════
    # HEALTH & STATUS
    # ═══════════════════════════════════════════════════════════════════

    def health(self) -> dict:
        weights = {Health.NOMINAL: 100, Health.DEGRADED: 70, Health.CRITICAL: 25, Health.OFFLINE: 0, Health.UNKNOWN: 50}

        groups: Dict[str, List[Observation]] = {}
        for obs in self.observations.values():
            groups.setdefault(obs.sector.value, []).append(obs)

        sectors = {}
        scores = []
        for sn, ol in groups.items():
            score = sum(weights[o.status] for o in ol) / len(ol)
            scores.append(score)
            sectors[sn] = {"status": self._status_label(score).value, "score": round(score, 1), "issues": sum(len(o.issues) for o in ol), "observers": len(ol)}

        overall = sum(scores) / len(scores) if scores else 50

        return {
            "status": self._status_label(overall).value,
            "score": round(overall, 1),
            "sectors": sectors,
            "observer_count": len(self.observers),
            "issue_count": sum(len(o.issues) for o in self.observations.values()),
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

    def status(self) -> dict:
        health = self.health()
        counts = Counter(t.status.value for t in self.tasks.values())
        mode = "running" if self.running else "stopped"
        status_label = str(health.get("status") or mode)

        subsystems = []
        try:
            for node_id, node in (getattr(self.topology, "nodes", {}) or {}).items():
                st = getattr(node, "status", None) or getattr(node, "health", None) or "unknown"
                if hasattr(st, "value"):
                    st = st.value
                subsystems.append({
                    "id": str(node_id),
                    "name": str(getattr(node, "name", None) or node_id),
                    "code": str(getattr(node, "kind", None) or getattr(node, "type", None) or "node"),
                    "status": str(st).lower(),
                    "health": getattr(node, "score", None),
                    "latencyMs": getattr(node, "latency_ms", None),
                    "loadPercentage": getattr(node, "load", None),
                })
        except Exception:
            subsystems = []
        if not subsystems and isinstance(health.get("sectors"), dict):
            for sn, meta in health["sectors"].items():
                subsystems.append({
                    "id": str(sn),
                    "name": str(sn),
                    "code": "sector",
                    "status": str((meta or {}).get("status", "unknown")).lower(),
                    "health": (meta or {}).get("score"),
                })

        return {
            "name": "Kanyoza Director",
            "codename": "The Autonomous CEO",
            "version": self.VERSION,
            "running": self.running,
            "mode": mode,
            "status": status_label,
            "state": mode,
            "subsystems": subsystems,
            "nodes": subsystems,
            "uptime_hours": round((now() - self.started).total_seconds() / 3600, 2),
            "health": health,
            "observers": len(self.observers),
            "capabilities": len(self.capabilities),
            "sectors": len(Sector),
            "tasks": dict(counts),
            "pending_approvals": sum(1 for t in self.tasks.values() if t.status == Status.APPROVAL),
            "last_observation": iso(self.last_observation),
            "last_plan": iso(self.last_plan),
            "event_queue_depth": len(self._event_queue),
            "executing_tasks": len(self._executing),
            "diagnostics": {
                "topology_nodes": len(self.topology.nodes),
                "current_root_cause": (
                    {"service": self.current_root_cause.root_cause, "confidence": self.current_root_cause.confidence, "affected_count": len(self.current_root_cause.affected_services)}
                    if self.current_root_cause else None
                ),
                "current_impact": self.current_impact,
                "cascading_predictions": self.current_predictions[:5],
                "recovery_plans": len(self.recovery_coordinator.recovery_plans),
            },
            "intelligence": {
                "learning_signals": len(self.learning_loop.signals),
                "adaptive_thresholds": dict(self.adaptive_thresholds.thresholds),
                "learning_report": self.learning_loop.get_learning_report(),
            },
        }

    # ═══════════════════════════════════════════════════════════════════
    # STRATEGIC OUTLOOK
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

        health_scores = [b.get("health", {}).get("score", 50) for b in list(self.briefings)[-20:] if isinstance(b, dict)]
        if len(health_scores) >= 5:
            slope = (health_scores[-1] - health_scores[0]) / len(health_scores)
            predicted_health = clamp(health_scores[-1] + slope * horizon_days, 0, 100)
        else:
            predicted_health = self.health()["score"]

        risks = []
        if predicted_health < 60:
            risks.append("Health trajectory declining — intervention needed within 48h")

        failure_patterns = Counter(t.capability for t in task_history if t.status == Status.FAILED)
        for cap, count in failure_patterns.most_common(3):
            if count >= 2:
                risks.append(f"Recurring failure in {cap}: {count} failures")

        opportunities = []
        sectors_with_issues = Counter(obs.sector.value for obs in self.observations.values() if obs.issues)
        for sector, count in sectors_with_issues.most_common(2):
            opportunities.append(f"Address {count} issues in {sector}")

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

            allocations.append(ResourceAllocation(
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
            ))

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

        parts = [f"## Kanyoza Platform Executive Summary", f"*{utcnow().strftime('%B %d, %Y — %H:%M UTC')}*", "", opening, "", f"### Health: {health['score']:.0f}/100 ({health['status'].upper()})"]

        critical_sectors = [(name, s) for name, s in health.get("sectors", {}).items() if s.get("status") in ("critical", "degraded")]
        if critical_sectors:
            parts.append("")
            parts.append("### ⚠️ Areas Requiring Attention")
            for name, s in critical_sectors[:3]:
                parts.append(f"- **{name}**: {s['status'].upper()} ({s['score']:.0f}%) — {s.get('issues', 0)} issues")

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

        for name, obs in self.observations.items():
            bucket = obs.timestamp.replace(second=0, microsecond=0)
            bucket = bucket - timedelta(minutes=bucket.minute % 5)
            time_windows[bucket].append((name, obs))

        for window_time, window_obs in time_windows.items():
            if len(window_obs) < 2:
                continue
            degraded = [name for name, obs in window_obs if obs.status in (Health.CRITICAL, Health.DEGRADED)]
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
        unused = set(self.capabilities.keys()) - used_capabilities - {c for c in self.capabilities if self.capabilities[c].risk == Risk.CRITICAL}
        if unused:
            initiatives.append({
                "type": "capability_gap",
                "title": f"Unused Capabilities Detected: {len(unused)}",
                "description": f"Capabilities never used: {', '.join(list(unused)[:5])}",
                "priority": "low",
                "unused_capabilities": list(unused),
            })

        return sorted(initiatives, key=lambda i: {"high": 0, "medium": 1, "low": 2}[i["priority"]])

    # ═══════════════════════════════════════════════════════════════════
    # BRIEFINGS
    # ═══════════════════════════════════════════════════════════════════

    def briefing(self, kind: Briefing = Briefing.CHECKPOINT) -> dict:
        health = self.health()
        pending = [safe(asdict(t)) for t in self.tasks.values() if t.status == Status.APPROVAL]
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
            "actions_taken": [t.description for t in list(self.history)[-10:] if t.status == Status.COMPLETED],
            "pending_approvals": pending,
            "insights": [
                f"Health score: {health['score']}",
                f"Pending approvals: {len(pending)}",
                f"Recent failures: {len(failures)}",
            ],
            "plan_summary": {"tasks": len(self.tasks), "approvals": len(pending)},
        }
        self.briefings.append(brief)
        _publish_event("director_briefing", brief)
        return brief

    def _send_daily_briefing(self) -> None:
        try:
            from services.email_service import email_service
            health = self.health()
            tasks_done = sum(1 for t in self.history if t.status == Status.COMPLETED)
            tasks_failed = sum(1 for t in self.history if t.status == Status.FAILED)

            msg = (
                f"📊 <b>KANYOZA DAILY BRIEFING</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"✅ Health: {health['status'].upper()} ({health['score']:.0f}%)\n"
                f"📝 Tasks: {tasks_done} done, {tasks_failed} failed\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"👔 {len(self.observers)} engines | v{self.VERSION}"
            )
            email_service.send_telegram(msg)
            log.info("Daily briefing sent via Telegram")
        except Exception as exc:
            log.error("Daily briefing failed: %s", sanitize_error_message(exc))

    # ═══════════════════════════════════════════════════════════════════
    # NOTIFICATION
    # ═══════════════════════════════════════════════════════════════════

    def _send_telegram(self, message: str) -> bool:
        try:
            from services.email_service import email_service
            return email_service.send_telegram(message)
        except Exception as exc:
            log.error("Telegram send failed: %s", sanitize_error_message(exc))
            return False

    # ═══════════════════════════════════════════════════════════════════
    # LEARNING
    # ═══════════════════════════════════════════════════════════════════

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

    def _learn(self, task: Task) -> None:
        entry = {
            "id": f"learn_{uuid.uuid4().hex[:12]}",
            "task_id": task.id,
            "signal": "success" if task.status == Status.COMPLETED else "failure",
            "lesson": f"Outcome of {task.capability}: {task.status.value}",
        }
        asyncio.create_task(self.store.save_learning(entry))

    # ═══════════════════════════════════════════════════════════════════
    # EVENT DRAIN
    # ═══════════════════════════════════════════════════════════════════

    def _drain_events(self) -> None:
        if not self._event_queue:
            return

        batch = []
        while self._event_queue:
            try:
                batch.append(self._event_queue.popleft())
            except IndexError:
                break

        for event in batch:
            try:
                if self._event_callback:
                    self._event_callback(event["name"], event["payload"])
            except Exception as exc:
                log.exception("Event processing failed: %s — %s", event["name"], sanitize_error_message(exc))

    def enqueue_event(self, name: str, payload: dict) -> None:
        self._event_queue.append({"name": name, "payload": payload, "timestamp": time.monotonic()})

    def set_event_callback(self, cb: Callable) -> None:
        self._event_callback = cb

    def set_alert_callback(self, cb: Callable) -> None:
        self._alert_callback = cb

    # ═══════════════════════════════════════════════════════════════════
    # REGISTRATION
    # ═══════════════════════════════════════════════════════════════════

    def bind_capability(self, name: str, handler: Callable) -> None:
        if name not in self.capabilities:
            raise ValueError(f"Unknown capability: {name}")
        self.capabilities[name].handler = handler
        kind = "async" if inspect.iscoroutinefunction(handler) else "sync"
        log.info("Director: capability '%s' bound (%s)", name, kind)

    def register_observer(self, observer: FunctionObserver) -> None:
        self.observers[observer.name] = observer

    def register_capability(self, capability: Capability) -> None:
        self.capabilities[capability.name] = capability

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
            ("web_search", Sector.LEARNING, "Search the live web", Authority.AUTO, Risk.LOW),
            ("web_fetch", Sector.LEARNING, "Fetch a web page", Authority.AUTO, Risk.LOW),
            ("ask_with_tools", Sector.LEARNING, "Answer using web tools when needed", Authority.AUTO, Risk.LOW),
        ]:
            self.capabilities[name] = Capability(name, sector, desc, self._placeholder(name), auth, risk)

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
            self.capabilities[name] = Capability(
                name, sector, name.replace("_", " ").title(), self._blocked, Authority.SUGGEST, Risk.CRITICAL
            )

    def _placeholder(self, name: str) -> Callable:
        """Unbound capability — never reports success."""
        def _unbound(p: dict) -> dict:
            return {
                "ok": False,
                "status": "unavailable",
                "capability": name,
                "error": "handler_not_bound",
                "message": (
                    f"Capability '{name}' has no bound production handler. "
                    "Bind via director_bindings before autonomous execution."
                ),
                "params": p or {},
            }
        _unbound.__name__ = f"placeholder_{name}"
        return _unbound

    def _blocked(self, p: dict) -> dict:
        return {
            "ok": False,
            "status": "blocked",
            "error": "critical_requires_approval",
            "reason": "Critical action requires human approval and a bound production handler.",
        }

    @staticmethod
    def _handler_result_ok(result) -> bool:
        """Truthful success: explicit failure statuses never count as success."""
        if result is None:
            return False
        if not isinstance(result, dict):
            return True
        if result.get("ok") is False:
            return False
        st = str(result.get("status") or "").lower()
        if st in (
            "delegated",
            "unavailable",
            "blocked",
            "failed",
            "error",
            "unbound",
            "not_implemented",
        ):
            return False
        if result.get("error") and result.get("ok") is not True:
            return False
        return True


# ═══════════════════════════════════════════════════════════════════════════
# FASTAPI APP
# ═══════════════════════════════════════════════════════════════════════════

def create_app(director: Director):
    """Create FastAPI app with full authentication."""
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
    from pydantic import BaseModel, Field

    app = FastAPI(title="Kanyoza Director", version=director.VERSION)
    security = HTTPBearer(auto_error=False)

    def _authenticate(credentials: Optional[HTTPAuthorizationCredentials], request: Optional[Request] = None) -> bool:
        if request is None:
            return False
        master_token = (os.getenv("MASTER_API_TOKEN", "") or "").strip()
        service_token = (os.getenv("PYTHON_SERVICE_TOKEN", "") or "").strip()
        accepted = [t for t in (master_token, service_token) if t]
        if not accepted:
            log.warning("MASTER_API_TOKEN / PYTHON_SERVICE_TOKEN not set — Director API is UNAUTHENTICATED")
            return False
        token = ""
        if credentials:
            token = credentials.credentials
        if not token:
            token = request.headers.get("X-API-Key", "") or request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not token or not isinstance(token, str):
            return False
        return any(hmac.compare_digest(token, t) for t in accepted)

    async def _require_auth(credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None) -> None:
        if not _authenticate(credentials, request):
            raise HTTPException(status_code=401, detail="Authentication required")

    class ApprovalBody(BaseModel):
        actor: str = Field(default="admin", min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_\-\.\s]+$")

    class RejectBody(BaseModel):
        actor: str = Field(default="admin", min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_\-\.\s]+$")
        reason: str = Field(default="", max_length=500)

    # ── Core ─────────────────────────────────────────────────────────

    @app.get("/health")
    async def health_check():
        return {"status": "healthy" if director.running else "stopped", "version": director.VERSION}

    @app.get("/status")
    async def get_status(credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        return director.status()

    @app.get("/briefing")
    async def get_briefing(credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        return director.briefings[-1] if director.briefings else director.briefing()

    @app.get("/tasks")
    async def get_tasks(credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        return [safe(asdict(t)) for t in director.tasks.values()]

    @app.get("/approvals")
    async def get_approvals(credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        return [safe(asdict(t)) for t in director.tasks.values() if t.status == Status.APPROVAL]

    @app.post("/tasks/{task_id}/approve")
    async def approve_task(task_id: str, body: ApprovalBody, credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        try:
            return safe(asdict(director.approve(task_id, body.actor)))
        except KeyError:
            raise HTTPException(404, "Task not found")
        except ValueError as e:
            raise HTTPException(409, str(e))

    @app.post("/tasks/{task_id}/reject")
    async def reject_task(task_id: str, body: RejectBody, credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        try:
            return safe(asdict(director.reject(task_id, body.actor, body.reason)))
        except KeyError:
            raise HTTPException(404, "Task not found")

    @app.get("/engines")
    async def get_engines(credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        return {
            "engines": {
                name: {"name": name, "status": obs.status.value, "sector": obs.sector.value, "issues": len(obs.issues)}
                for name, obs in director.observations.items()
            },
            "total": len(director.observations),
        }

    # ── Diagnostics ──────────────────────────────────────────────────

    @app.get("/diagnostics")
    async def get_diagnostics(credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        return {
            "root_cause": director.current_root_cause.__dict__ if director.current_root_cause else None,
            "impact": director.current_impact,
            "cascading_predictions": director.current_predictions[:5],
            "recovery_plans": director.recovery_coordinator.get_recovery_plans(),
        }

    @app.get("/diagnostics/root-cause")
    async def get_root_cause(credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        result = director.current_root_cause
        return {"status": "found" if result else "none", "data": safe(asdict(result)) if result else None}

    @app.get("/diagnostics/cascading")
    async def get_cascading(credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        return {"predictions": director.current_predictions}

    @app.get("/diagnostics/impact")
    async def get_impact(credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        return director.current_impact

    @app.get("/diagnostics/recovery-plans")
    async def get_recovery_plans(credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        return {"recovery_plans": director.recovery_coordinator.get_recovery_plans()}

    # ── Decisions & Alerts ───────────────────────────────────────────

    @app.get("/decisions")
    async def get_decisions(limit: int = 50, credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        return [safe(asdict(d)) for d in list(director.decisions)[-limit:]]

    @app.get("/alerts")
    async def get_alerts(limit: int = 50, credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        return {"alerts": list(director._alert_history)[-limit:]}

    # ── Capabilities ─────────────────────────────────────────────────

    @app.get("/capabilities")
    async def get_capabilities(credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        return {
            "capabilities": {
                name: {"name": cap.name, "sector": cap.sector.value, "description": cap.description, "enabled": cap.enabled, "risk": cap.risk.value, "authority": cap.authority.value}
                for name, cap in director.capabilities.items()
            }
        }

    @app.post("/capabilities/{cap_name}/toggle")
    async def toggle_capability(cap_name: str, credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        cap = director.capabilities.get(cap_name)
        if not cap:
            raise HTTPException(404, f"Unknown capability: {cap_name}")
        cap.enabled = not cap.enabled
        return {"capability": cap_name, "enabled": cap.enabled}

    # ── Control ──────────────────────────────────────────────────────

    @app.post("/observe")
    async def run_observe(credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        await director._observe_async()
        return director.status()

    @app.post("/plan")
    async def run_plan(credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        return await director._build_plan_async()

    @app.post("/start")
    async def run_start(credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        await director.start()
        return director.status()

    @app.post("/stop")
    async def run_stop(credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        await director.stop()
        return director.status()

    # ── Intelligence ─────────────────────────────────────────────────

    @app.get("/outlook")
    async def get_outlook(credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        outlook = director.generate_strategic_outlook()
        return safe(asdict(outlook)) if outlook else {"error": "Insufficient data"}

    @app.get("/allocation")
    async def get_allocation(credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        return [safe(asdict(a)) for a in director.allocate_attention()]

    @app.get("/summary")
    async def get_summary(credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        return {"summary": director.generate_executive_summary()}

    @app.get("/initiatives")
    async def get_initiatives(credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        return {"initiatives": director.generate_initiatives()}

    @app.get("/correlations")
    async def get_correlations(credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        return {"correlations": director.correlate_observations()}

    @app.get("/daily-briefing")
    async def get_daily_briefing(credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        director._send_daily_briefing()
        return {"ok": True, "message": "Daily briefing sent"}

    # ═══════════════════════════════════════════════════════════════════
    # LEARNING AUDIT
    # ═══════════════════════════════════════════════════════════════════

    @app.get("/learning/report")
    async def get_learning_report(credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        return director.learning_loop.get_learning_report()

    @app.get("/learning/capability/{capability}")
    async def get_capability_learning(capability: str, credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)

        all_time = director.learning_loop.get_success_rate(capability)
        recent = director.learning_loop.get_recent_success_rate(capability)
        avg_duration = director.learning_loop.get_avg_duration_ms(capability)

        context_breakdown = {}
        for ctx_key, stats in director.learning_loop.context_stats.items():
            if not ctx_key.endswith(f":{capability}"):
                continue
            sector_name = ctx_key.split(":", 1)[0]
            total = stats["successes"] + stats["failures"]
            context_breakdown[sector_name] = {
                "successes": stats["successes"],
                "failures": stats["failures"],
                "success_rate": round(stats["successes"] / total, 3) if total > 0 else None,
                "total": total,
            }

        time_breakdown = {}
        for time_key, stats in director.learning_loop.time_stats.items():
            if f":{capability}:" not in time_key:
                continue
            parts = time_key.split(":")
            if len(parts) < 3:
                continue
            time_bucket = parts[-1]
            time_breakdown.setdefault(time_bucket, {"successes": 0, "failures": 0})
            time_breakdown[time_bucket]["successes"] += stats["successes"]
            time_breakdown[time_bucket]["failures"] += stats["failures"]

        for bucket, stats in time_breakdown.items():
            total = stats["successes"] + stats["failures"]
            stats["success_rate"] = round(stats["successes"] / total, 3) if total > 0 else None
            stats["total"] = total

        recent_failures = list(director.learning_loop.failure_patterns.get(capability, []))[-10:]

        time_context = director.learning_loop._get_time_context()
        should_escalate = False
        should_skip = False
        for sector in Sector:
            if director.learning_loop.should_escalate(sector, capability):
                should_escalate = True
            if director.learning_loop.should_skip(sector, capability):
                should_skip = True

        return {
            "capability": capability,
            "all_time_success_rate": round(all_time, 3) if all_time is not None else None,
            "recent_success_rate": round(recent, 3) if recent is not None else None,
            "avg_duration_ms": round(avg_duration, 1) if avg_duration is not None else None,
            "context_breakdown": context_breakdown,
            "time_of_day_breakdown": time_breakdown,
            "current_time_context": time_context,
            "recent_failures": [
                {
                    "timestamp": f.get("timestamp").isoformat() if f.get("timestamp") else None,
                    "error": f.get("error"),
                    "context": f.get("context"),
                }
                for f in recent_failures
            ],
            "current_trust": {
                "should_escalate": should_escalate,
                "should_skip": should_skip,
                "verdict": (
                    "skip" if should_skip
                    else "escalate" if should_escalate
                    else "trust"
                ),
            },
        }

    @app.get("/learning/contexts")
    async def get_all_contexts(credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)

        contexts = {}
        for ctx_key, stats in director.learning_loop.context_stats.items():
            total = stats["successes"] + stats["failures"]
            contexts[ctx_key] = {
                "successes": stats["successes"],
                "failures": stats["failures"],
                "success_rate": round(stats["successes"] / total, 3) if total > 0 else None,
                "total": total,
                "verdict": (
                    "skip" if total >= 3 and (stats["successes"] / total) < 0.2
                    else "escalate" if total >= 3 and (stats["successes"] / total) < 0.5
                    else "trust" if total >= 3
                    else "insufficient_data"
                ),
            }

        return {
            "total_contexts": len(contexts),
            "contexts": contexts,
        }

    @app.get("/learning/time-patterns")
    async def get_time_patterns(credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)

        by_capability: Dict[str, Dict[str, Any]] = defaultdict(lambda: defaultdict(lambda: {"successes": 0, "failures": 0}))

        for time_key, stats in director.learning_loop.time_stats.items():
            parts = time_key.split(":")
            if len(parts) < 3:
                continue
            capability_name, time_bucket = parts[1], parts[2]
            by_capability[capability_name][time_bucket]["successes"] += stats["successes"]
            by_capability[capability_name][time_bucket]["failures"] += stats["failures"]

        result = {}
        for capability, buckets in by_capability.items():
            result[capability] = {}
            for bucket, stats in buckets.items():
                total = stats["successes"] + stats["failures"]
                result[capability][bucket] = {
                    "successes": stats["successes"],
                    "failures": stats["failures"],
                    "success_rate": round(stats["successes"] / total, 3) if total > 0 else None,
                    "total": total,
                }

        return {
            "current_time_context": director.learning_loop._get_time_context(),
            "current_day_context": director.learning_loop._get_day_context(),
            "patterns": result,
        }

    @app.get("/learning/failures")
    async def get_recent_failures(limit: int = 50, credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)

        recent = [s for s in director.learning_loop.signals if s.outcome == "failure"]
        recent = list(reversed(recent))[:limit]

        return {
            "total_recent_failures": len(recent),
            "failures": [
                {
                    "capability": s.capability,
                    "sector": s.sector.value,
                    "timestamp": s.timestamp.isoformat(),
                    "error": s.error_type,
                    "duration_ms": round(s.duration_ms, 1),
                    "context": s.context,
                }
                for s in recent
            ],
        }

    @app.get("/learning/slowest")
    async def get_slowest_capabilities(limit: int = 10, credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)

        durations = []
        for capability in director.learning_loop.duration_stats.keys():
            avg_ms = director.learning_loop.get_avg_duration_ms(capability)
            if avg_ms is not None:
                durations.append({
                    "capability": capability,
                    "avg_duration_ms": round(avg_ms, 1),
                    "sample_size": len(director.learning_loop.duration_stats[capability]),
                })

        durations.sort(key=lambda x: x["avg_duration_ms"], reverse=True)

        return {
            "total_tracked": len(durations),
            "slowest": durations[:limit],
        }

    @app.post("/learning/reset/{capability}")
    async def reset_capability_learning(capability: str, credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)

        director.learning_loop.capability_stats.pop(capability, None)
        director.learning_loop.duration_stats.pop(capability, None)
        director.learning_loop.failure_patterns.pop(capability, None)

        keys_to_remove = [k for k in director.learning_loop.context_stats.keys() if k.endswith(f":{capability}")]
        for k in keys_to_remove:
            del director.learning_loop.context_stats[k]

        time_keys_to_remove = [k for k in director.learning_loop.time_stats.keys() if f":{capability}:" in k]
        for k in time_keys_to_remove:
            del director.learning_loop.time_stats[k]

        director.learning_loop.signals = deque(
            [s for s in director.learning_loop.signals if s.capability != capability],
            maxlen=director.learning_loop.signals.maxlen or 1000,
        )

        log.info("Learning reset for capability: %s", capability)

        return {
            "ok": True,
            "capability": capability,
            "message": f"Learning data cleared for {capability}",
        }

    # ═══════════════════════════════════════════════════════════════════
    # OODA INTROSPECTION (Autonomous Loop)
    # ═══════════════════════════════════════════════════════════════════

    async def _get_autonomous_loop():
        """Safely resolve the Autonomous Loop singleton."""
        try:
            from core.autonomous_loop import get_autonomous_loop
            return await get_autonomous_loop()
        except Exception:
            return None

    @app.get("/ooda/state")
    async def get_ooda_state(credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        loop = await _get_autonomous_loop()
        if loop is None:
            return {
                "ok": False,
                "status": "unavailable",
                "message": "Autonomous Loop is not running in this process.",
            }
        try:
            return {"ok": True, "state": loop.get_ooda_state()}
        except Exception as exc:
            return {"ok": False, "status": "error", "error": str(exc)[:300]}

    @app.get("/ooda/world-model")
    async def get_world_model(credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        loop = await _get_autonomous_loop()
        if loop is None:
            return {"ok": False, "status": "unavailable"}
        try:
            return {"ok": True, "world_model": loop.get_world_model()}
        except Exception as exc:
            return {"ok": False, "status": "error", "error": str(exc)[:300]}

    @app.get("/ooda/priorities")
    async def get_ooda_priorities(credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        loop = await _get_autonomous_loop()
        if loop is None:
            return {"ok": False, "status": "unavailable"}
        try:
            priorities = dict(sorted(
                loop._observation_priorities.items(),
                key=lambda x: x[1],
                reverse=True,
            ))
            return {"ok": True, "priorities": priorities}
        except Exception as exc:
            return {"ok": False, "status": "error", "error": str(exc)[:300]}

    @app.get("/ooda/action-preferences")
    async def get_ooda_action_preferences(credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        loop = await _get_autonomous_loop()
        if loop is None:
            return {"ok": False, "status": "unavailable"}
        try:
            return {"ok": True, "preferences": loop.get_action_preferences()}
        except Exception as exc:
            return {"ok": False, "status": "error", "error": str(exc)[:300]}

    @app.get("/ooda/causal-insights")
    async def get_ooda_causal_insights(credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        loop = await _get_autonomous_loop()
        if loop is None:
            return {"ok": False, "status": "unavailable"}
        try:
            return {"ok": True, "insights": loop.get_causal_insights()}
        except Exception as exc:
            return {"ok": False, "status": "error", "error": str(exc)[:300]}

    @app.get("/ooda/learner-report")
    async def get_ooda_learner_report(credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        loop = await _get_autonomous_loop()
        if loop is None:
            return {"ok": False, "status": "unavailable"}
        try:
            return {"ok": True, "report": loop.get_learner_report()}
        except Exception as exc:
            return {"ok": False, "status": "error", "error": str(exc)[:300]}

    @app.get("/ooda/intelligence-report")
    async def get_ooda_intelligence_report(credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        loop = await _get_autonomous_loop()
        if loop is None:
            return {"ok": False, "status": "unavailable"}
        try:
            report = await loop.get_intelligence_report()
            return {"ok": True, "report": report}
        except Exception as exc:
            return {"ok": False, "status": "error", "error": str(exc)[:300]}

    # ═══════════════════════════════════════════════════════════════════
    # META ENGINE INTROSPECTION
    # ═══════════════════════════════════════════════════════════════════

    async def _get_meta_engine():
        """Safely resolve the Meta Engine singleton."""
        try:
            from services.meta_engine import meta_engine
            return meta_engine
        except Exception:
            return None

    @app.get("/meta/status")
    async def get_meta_status(credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        meta = await _get_meta_engine()
        if meta is None:
            return {"ok": False, "status": "unavailable", "message": "Meta Engine not initialized in this process."}
        try:
            return {"ok": True, "status": meta.get_status()}
        except Exception as exc:
            return {"ok": False, "status": "error", "error": str(exc)[:300]}

    @app.get("/meta/impact")
    async def get_meta_impact(credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        meta = await _get_meta_engine()
        if meta is None:
            return {"ok": False, "status": "unavailable"}
        try:
            return {"ok": True, "impact": meta.get_impact_assessment()}
        except Exception as exc:
            return {"ok": False, "status": "error", "error": str(exc)[:300]}

    @app.get("/meta/topology")
    async def get_meta_topology(credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        meta = await _get_meta_engine()
        if meta is None:
            return {"ok": False, "status": "unavailable"}
        try:
            return {"ok": True, "topology": meta.get_topology()}
        except Exception as exc:
            return {"ok": False, "status": "error", "error": str(exc)[:300]}

    @app.get("/meta/root-cause")
    async def get_meta_root_cause(credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        meta = await _get_meta_engine()
        if meta is None:
            return {"ok": False, "status": "unavailable"}
        try:
            return {"ok": True, "root_cause": meta.get_root_cause()}
        except Exception as exc:
            return {"ok": False, "status": "error", "error": str(exc)[:300]}

    @app.get("/meta/cascading")
    async def get_meta_cascading(credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        meta = await _get_meta_engine()
        if meta is None:
            return {"ok": False, "status": "unavailable"}
        try:
            return {"ok": True, "predictions": meta.get_cascading_predictions()}
        except Exception as exc:
            return {"ok": False, "status": "error", "error": str(exc)[:300]}

    @app.get("/meta/recovery-plans")
    async def get_meta_recovery_plans(credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        meta = await _get_meta_engine()
        if meta is None:
            return {"ok": False, "status": "unavailable"}
        try:
            return {"ok": True, "plans": meta.get_recovery_plans()}
        except Exception as exc:
            return {"ok": False, "status": "error", "error": str(exc)[:300]}

    @app.get("/meta/alerts")
    async def get_meta_alerts(credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        meta = await _get_meta_engine()
        if meta is None:
            return {"ok": False, "status": "unavailable"}
        try:
            return {"ok": True, "alerts": meta.get_alerts()}
        except Exception as exc:
            return {"ok": False, "status": "error", "error": str(exc)[:300]}

    @app.get("/meta/incidents")
    async def get_meta_incidents(credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        meta = await _get_meta_engine()
        if meta is None:
            return {"ok": False, "status": "unavailable"}
        try:
            return {"ok": True, "incidents": meta.get_incidents()}
        except Exception as exc:
            return {"ok": False, "status": "error", "error": str(exc)[:300]}

    @app.get("/meta/learning/report")
    async def get_meta_learning_report(credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        meta = await _get_meta_engine()
        if meta is None:
            return {"ok": False, "status": "unavailable"}
        try:
            return {"ok": True, "report": meta.get_learning_report()}
        except Exception as exc:
            return {"ok": False, "status": "error", "error": str(exc)[:300]}

    @app.get("/meta/learning/engine/{engine_name}")
    async def get_meta_engine_pattern(engine_name: str, credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        meta = await _get_meta_engine()
        if meta is None:
            return {"ok": False, "status": "unavailable"}
        try:
            return {"ok": True, "pattern": meta.get_engine_pattern(engine_name)}
        except Exception as exc:
            return {"ok": False, "status": "error", "error": str(exc)[:300]}

    @app.get("/meta/engine/{engine_name}")
    async def get_meta_engine_report(engine_name: str, credentials: Optional[HTTPAuthorizationCredentials] = None, request: Optional[Request] = None):
        await _require_auth(credentials, request)
        meta = await _get_meta_engine()
        if meta is None:
            return {"ok": False, "status": "unavailable"}
        try:
            return {"ok": True, "report": meta.get_engine_report(engine_name)}
        except Exception as exc:
            return {"ok": False, "status": "error", "error": str(exc)[:300]}

    return app


# ═══════════════════════════════════════════════════════════════════════════
# SINGLETON (Async)
# ═══════════════════════════════════════════════════════════════════════════

director: Optional[Director] = None
_lock = asyncio.Lock()


async def init_director(supabase_client=None, auto_start: bool = True) -> Director:
    """Initialize Director singleton."""
    global director

    async with _lock:
        if director is None:
            director = Director(supabase_client=supabase_client)

        if auto_start and not director.running:
            await director.start()

    return director


async def get_director() -> Optional[Director]:
    """Get Director singleton."""
    return director


async def shutdown_director() -> None:
    """Shutdown Director singleton."""
    global director

    if director is not None:
        await director.stop()
        director = None


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

    d = Director()

    if args.api:
        import uvicorn
        app = create_app(d)

        async def _startup():
            await d.start()

        @app.on_event("startup")
        async def _on_startup():
            await _startup()

        @app.on_event("shutdown")
        async def _on_shutdown():
            await d.stop()

        uvicorn.run(app, host=args.host, port=args.port)
    else:
        async def _run():
            await d.start()
            try:
                while d.running:
                    await asyncio.sleep(5)
            except KeyboardInterrupt:
                await d.stop()

        asyncio.run(_run())


if __name__ == "__main__":
    main()


# ═══════════════════════════════════════════════════════════════════════════
# EXPORTS
# ═══════════════════════════════════════════════════════════════════════════

__all__ = [
    "Director",
    "init_director",
    "get_director",
    "shutdown_director",
    "auto_discover_engines",
    "Authority",
    "Sector",
    "Status",
    "Health",
    "Risk",
    "Briefing",
    "CircuitState",
    "AsyncCircuitBreaker",
    "CircuitBreakerOpenError",
    "SupabaseErrorClassifier",
    "SystemTopology",
    "RootCauseAnalyzer",
    "ImpactAssessor",
    "CascadingFailurePredictor",
    "CorrelatedRecovery",
    "LearningLoop",
    "AdaptiveThresholds",
    "FunctionObserver",
]
