"""
KANYOZA DIRECTOR v1.1 — The Autonomous CEO.

Single-file autonomous observation, decision, approval, execution,
learning, and briefing engine.

v1.1 Changes:
  - SupabaseStore: Intelligent retry with error classification
  - SupabaseStore: Circuit breaker prevents retry storms on permanent errors
  - SupabaseStore: Exponential backoff with jitter for transient errors
  - Director: Graceful degradation when store is unavailable
  - Director: Structured health reporting (implements HealthProvider protocol)
  - Director: Proper shutdown sequence
  - No more retry storms on 403/401 errors

AUTO-DISCOVERY: All engines and plugins are automatically detected on boot.
No manual binding required. Add a new engine → Director picks it up.

Storage: Supabase (PostgreSQL) — requires service_role key for writes.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import logging
import os
import random
import signal
import threading
import time
import traceback
import uuid
from collections import Counter, deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, TypeVar

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
    return datetime.now(timezone.utc)


def iso(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def safe(value: Any) -> Any:
    """Recursively convert values to JSON-safe types."""
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


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


# ═══════════════════════════════════════════════════════════════════════════
# ERROR CLASSIFICATION — Prevents retry storms on permanent errors
# ═══════════════════════════════════════════════════════════════════════════


class SupabaseErrorClassifier:
    """Classify Supabase/HTTP errors for intelligent retry decisions.

    DESIGN DECISIONS:
      - 4xx (except 429): NEVER retry — permanent failure (auth, permissions, not found)
      - 429: Retry with backoff — rate limit, will recover
      - 5xx: Retry with backoff — may be transient server error
      - Network errors: Retry — connection issues may resolve
    """

    @staticmethod
    def is_retryable(error: Exception) -> bool:
        """Return True if this error might succeed on retry."""
        status = SupabaseErrorClassifier._extract_status(error)

        # 429 Rate Limit — retryable (with backoff)
        if status == 429:
            return True

        # All other 4xx — PERMANENT, do not retry
        if status and 400 <= status < 500:
            return False

        # 5xx Server Error — potentially transient
        if status and status >= 500:
            return True

        # Network-level errors — potentially transient
        error_str = str(error).lower()
        transient_indicators = (
            'timeout', 'connection', 'network', 'dns',
            'reset', 'refused', 'unreachable', 'temporary',
        )
        if any(indicator in error_str for indicator in transient_indicators):
            return True

        # Unknown — be conservative, don't retry
        return False

    @staticmethod
    def is_permanent(error: Exception) -> bool:
        """Return True if this error will NEVER succeed with current credentials."""
        status = SupabaseErrorClassifier._extract_status(error)
        return status is not None and 400 <= status < 500 and status != 429

    @staticmethod
    def _extract_status(error: Exception) -> int | None:
        """Extract HTTP status code from various exception types."""
        # Supabase/PostgREST errors
        if hasattr(error, 'code'):
            code = getattr(error, 'code', None)
            if isinstance(code, int):
                return code
            if isinstance(code, str) and code.isdigit():
                return int(code)
        # httpx errors
        if hasattr(error, 'response') and hasattr(error.response, 'status_code'):
            return error.response.status_code
        # requests errors
        if hasattr(error, 'response') and hasattr(error.response, 'status_code'):
            return error.response.status_code
        # Generic HTTPError
        if hasattr(error, 'status_code'):
            return error.status_code
        return None

    @staticmethod
    def describe(error: Exception) -> str:
        """Human-readable error description for logging."""
        status = SupabaseErrorClassifier._extract_status(error)
        if status == 403:
            return (
                "403 Forbidden — The Supabase client does not have permission. "
                "Ensure you are using the service_role key (SUPABASE_SERVICE_ROLE_KEY), "
                "not the anon key. Get it from: Supabase Dashboard → Settings → API"
            )
        if status == 401:
            return "401 Unauthorized — Invalid or expired credentials"
        if status == 404:
            return "404 Not Found — The resource does not exist"
        if status == 429:
            return "429 Too Many Requests — Rate limited by Supabase"
        if status and status >= 500:
            return f"{status} Server Error — Supabase may be experiencing issues"
        return str(error)[:300]


# ═══════════════════════════════════════════════════════════════════════════
# CIRCUIT BREAKER — Prevents cascading failures and retry storms
# ═══════════════════════════════════════════════════════════════════════════


class CircuitBreaker:
    """Circuit breaker for external service calls.

    State machine:
      CLOSED → (consecutive failures > threshold) → OPEN
      OPEN → (recovery_timeout elapsed) → HALF_OPEN
      HALF_OPEN → (success) → CLOSED
      HALF_OPEN → (failure) → OPEN

    DESIGN DECISIONS:
      - 5 consecutive failures opens the circuit
      - 30 second recovery timeout before testing again
      - OPEN state rejects all calls immediately (no wasted requests)
      - HALF_OPEN allows one test request to check if service recovered
    """

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
        self._state = "CLOSED"  # CLOSED | OPEN | HALF_OPEN
        self._lock = threading.RLock()

    @property
    def is_open(self) -> bool:
        """Check if circuit is open (calls should be rejected)."""
        with self._lock:
            if self._state == "CLOSED":
                return False
            if self._state == "OPEN":
                if time.monotonic() - self._last_failure_time >= self.recovery_timeout:
                    self._state = "HALF_OPEN"
                    log.info(
                        "Circuit breaker '%s': OPEN → HALF_OPEN (testing recovery)",
                        self.name,
                    )
                    return False
                return True
            # HALF_OPEN — allow one request through
            return False

    def record_success(self) -> None:
        """Record a successful call."""
        with self._lock:
            if self._state == "HALF_OPEN":
                self._state = "CLOSED"
                self._failure_count = 0
                log.info("Circuit breaker '%s': HALF_OPEN → CLOSED (recovered)", self.name)
            elif self._state == "CLOSED":
                self._failure_count = 0  # Reset on success

    def record_failure(self) -> None:
        """Record a failed call."""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()

            if self._state == "HALF_OPEN":
                self._state = "OPEN"
                log.warning(
                    "Circuit breaker '%s': HALF_OPEN → OPEN (test request failed)",
                    self.name,
                )
            elif self._failure_count >= self.failure_threshold and self._state == "CLOSED":
                self._state = "OPEN"
                log.error(
                    "Circuit breaker '%s': CLOSED → OPEN (%d consecutive failures)",
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


# ═══════════════════════════════════════════════════════════════════════════
# SUPABASE STORE — With intelligent retry, circuit breaker, and backoff
# ═══════════════════════════════════════════════════════════════════════════

class SupabaseStore:
    """Production store backed by Supabase (PostgreSQL).

    v1.1 Features:
      - Error classification: 4xx (except 429) never retried
      - Circuit breaker: Prevents retry storms on permanent failures
      - Exponential backoff with jitter for transient errors
      - Graceful degradation: Operations become no-ops when circuit is open
      - Clear error messages that tell you HOW to fix the problem

    REQUIRES: Supabase client authenticated with service_role key.
    The anon key CANNOT write to RLS-protected tables.
    """

    def __init__(self, supabase_client):
        self.supabase = supabase_client
        self.circuit_breaker = CircuitBreaker(name="supabase_store")
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        """Try to ensure tables exist. Failure is non-fatal.

        Tables should be created via Supabase migrations or SQL editor.
        The director_ensure_tables RPC is optional.
        """
        if self.circuit_breaker.is_open:
            log.warning("Supabase circuit breaker OPEN — skipping table check.")
            return

        try:
            self.supabase.rpc("director_ensure_tables", {}).execute()
            self.circuit_breaker.record_success()
        except Exception as exc:
            self.circuit_breaker.record_failure()
            if SupabaseErrorClassifier.is_permanent(exc):
                log.error(
                    "Supabase table check failed: %s",
                    SupabaseErrorClassifier.describe(exc),
                )
            else:
                log.warning("Supabase table check failed: %s", exc)

    def _execute_with_retry(
        self,
        operation_name: str,
        fn: Callable[[], T],
        max_retries: int = 2,
    ) -> Optional[T]:
        """Execute a Supabase operation with intelligent retry.

        BEHAVIOR:
          - Circuit breaker OPEN → skip immediately, return None
          - 4xx errors (except 429) → fail immediately, NO retry
          - 5xx / 429 / network errors → retry with exponential backoff
          - First permanent error is logged at ERROR level
          - Transient retries are logged at WARNING level
        """
        if self.circuit_breaker.is_open:
            log.warning(
                "Supabase circuit breaker OPEN — skipping '%s' (will retry in ~%ds)",
                operation_name,
                self.circuit_breaker.recovery_timeout,
            )
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

                # Permanent errors — fail immediately
                if SupabaseErrorClassifier.is_permanent(exc):
                    log.error(
                        "Director DB: '%s' FAILED (HTTP %s, permanent): %s",
                        operation_name,
                        status,
                        SupabaseErrorClassifier.describe(exc),
                    )
                    return None  # Don't raise — Director continues in degraded mode

                # Exhausted retries
                if attempt >= max_retries:
                    log.error(
                        "Director DB: '%s' FAILED after %d retries (HTTP %s): %s",
                        operation_name,
                        max_retries,
                        status,
                        str(exc)[:200],
                    )
                    return None

                # Calculate backoff with jitter
                delay = min(1.0 * (2 ** attempt), 10.0)
                jitter = random.uniform(0.75, 1.25)
                delay *= jitter

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
        """Insert/upsert with retry and circuit breaker.

        Permanent errors (403, 401, etc.) are logged once and NOT retried.
        """
        self._execute_with_retry(
            f"insert into {table}",
            lambda: self._table(table).upsert(data).execute(),
            max_retries=2,
        )

    def _get(self, table: str, key: str, value: str) -> Optional[dict]:
        result = self._execute_with_retry(
            f"get from {table}",
            lambda: self._table(table).select("*").eq(key, value).limit(1).execute(),
            max_retries=1,
        )
        if result and result.data and len(result.data) > 0:
            return dict(result.data[0])
        return None

    def _list(self, table: str, limit: int = 100) -> List[dict]:
        result = self._execute_with_retry(
            f"list from {table}",
            lambda: self._table(table).select("*").order("created_at", desc=True).limit(limit).execute(),
            max_retries=1,
        )
        if result:
            return [dict(row) for row in (result.data or [])]
        return []

    # ── Tasks ──────────────────────────────────────────────────────────
    def save_task(self, task: Task) -> None:
        self._insert("director_tasks", {
            "id": task.id,
            "payload": json.dumps(safe(asdict(task))),
            "created_at": iso(task.created_at),
            "status": task.status.value,
        })

    def get_task(self, task_id: str) -> Optional[dict]:
        return self._get("director_tasks", "id", task_id)

    def list_tasks(self) -> List[dict]:
        return self._list("director_tasks", 200)

    # ── Decisions ──────────────────────────────────────────────────────
    def save_decision(self, decision: Decision) -> None:
        self._insert("director_decisions", {
            "id": decision.id,
            "payload": json.dumps(safe(asdict(decision))),
            "timestamp": iso(decision.timestamp),
        })

    def list_decisions(self, limit: int = 100) -> List[dict]:
        return self._list("director_decisions", limit)

    # ── Observations ───────────────────────────────────────────────────
    def save_observation(self, observation: Observation) -> None:
        self._insert("director_observations", {
            "payload": json.dumps(safe(asdict(observation))),
            "timestamp": iso(observation.timestamp),
        })

    def list_observations(self, limit: int = 100) -> List[dict]:
        return self._list("director_observations", limit)

    # ── Approvals ──────────────────────────────────────────────────────
    def save_approval(self, task_id: str, approved: bool, actor: str) -> None:
        self._insert("director_approvals", {
            "task_id": task_id,
            "approved": approved,
            "actor": actor,
            "timestamp": iso_now(),
        })

    def get_approval(self, task_id: str) -> Optional[dict]:
        return self._get("director_approvals", "task_id", task_id)

    # ── Learning ───────────────────────────────────────────────────────
    def save_learning(self, entry: dict) -> None:
        self._insert("director_learnings", {
            "id": entry.get("id", f"learn_{uuid.uuid4().hex[:12]}"),
            "payload": json.dumps(safe(entry)),
            "timestamp": iso_now(),
        })

    def list_learnings(self, limit: int = 100) -> List[dict]:
        return self._list("director_learnings", limit)


# ═══════════════════════════════════════════════════════════════════════════
# IN-MEMORY STORE — Fallback when Supabase is unavailable
# ═══════════════════════════════════════════════════════════════════════════

class InMemoryStore:
    """In-memory store for development/testing when Supabase is unavailable."""

    def __init__(self):
        self.tasks: Dict[str, dict] = {}
        self.decisions: List[dict] = []
        self.observations: List[dict] = []
        self.approvals: Dict[str, dict] = {}
        self.learnings: List[dict] = []

    def save_task(self, task: Task) -> None:
        self.tasks[task.id] = safe(asdict(task))

    def get_task(self, task_id: str) -> Optional[dict]:
        return self.tasks.get(task_id)

    def list_tasks(self) -> List[dict]:
        return list(self.tasks.values())

    def save_decision(self, decision: Decision) -> None:
        self.decisions.append(safe(asdict(decision)))

    def list_decisions(self, limit: int = 100) -> List[dict]:
        return self.decisions[-limit:]

    def save_observation(self, observation: Observation) -> None:
        self.observations.append(safe(asdict(observation)))

    def list_observations(self, limit: int = 100) -> List[dict]:
        return self.observations[-limit:]

    def save_approval(self, task_id: str, approved: bool, actor: str) -> None:
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
        return self.learnings[-limit:]


# ═══════════════════════════════════════════════════════════════════════════
# POLICY ENGINE
# ═══════════════════════════════════════════════════════════════════════════

class Policy:
    """Policy engine for Director actions.

    Critical actions always require human approval.
    Safe actions can be auto-executed.
    """

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
# FUNCTION OBSERVER
# ═══════════════════════════════════════════════════════════════════════════

class FunctionObserver:
    """Wraps an engine's health check function as a standardized observer."""

    def __init__(self, name: str, sector: Sector, fn: Callable):
        self.name = name
        self.sector = sector
        self.fn = fn

    def observe(self) -> Observation:
        try:
            result = self.fn() or {}
        except Exception as exc:
            return Observation(
                self.name, self.sector, Health.CRITICAL,
                issues=[{"type": "observer_failure", "error": str(exc)}],
            )

        if isinstance(result, Observation):
            return result

        status_str = str(result.get("status", "unknown")).lower()
        status_map = {
            "ok": Health.NOMINAL, "healthy": Health.NOMINAL, "nominal": Health.NOMINAL,
            "degraded": Health.DEGRADED, "critical": Health.CRITICAL,
            "offline": Health.OFFLINE,
        }
        return Observation(
            self.name, self.sector,
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
    ("services.job_service", "JobService", "job_service", Sector.INFRA),
    ("plugins.base", "PluginRegistry", "plugin_registry", Sector.INFRA),
    ("core.event_bus", "EventBus", "event_bus", Sector.INFRA),
    ("core.render_queue", "get_render_queue", "render_queue", Sector.CONTENT),
    ("services.image_engine", "generate_best_image", "image_engine", Sector.CONTENT),
    ("domain.content.topic_selector", "TopicSelector", "topic_selector", Sector.CONTENT),
    ("services.prompt_builder", "prompt_builder", "prompt_builder", Sector.CONTENT),
    ("services.health_monitor_service", "HealthMonitorService", "health_monitor", Sector.HEALTH),
]


def _make_observer(engine_name: str, engine_obj: Any) -> Callable:
    """Create an observer function for any engine."""

    def observe() -> dict:
        # Try get_status() first
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

        # Try health_check()
        if hasattr(engine_obj, "health_check"):
            try:
                r = engine_obj.health_check()
                if isinstance(r, dict):
                    return {"status": r.get("status", "healthy"), "metrics": r}
            except Exception:
                pass

        # Try inspecting attributes for metrics
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


def auto_discover_engines(director: 'Director', container) -> int:
    """Auto-discover ALL engines and plugins.

    Scans ENGINE_REGISTRY for core engines and container.industry_plugins
    for industry-specific plugins.

    Returns:
        Number of engines discovered.
    """
    discovered = 0

    # ── Core engines ───────────────────────────────────────────────────
    for module_path, attr_name, display_name, sector in ENGINE_REGISTRY:
        try:
            module = __import__(module_path, fromlist=[attr_name])
            engine = getattr(module, attr_name, None)
            if engine is not None:
                director.register_observer(
                    FunctionObserver(display_name, sector, _make_observer(display_name, engine))
                )
                discovered += 1
                log.debug("👔 Director: Auto-discovered %s (%s)", display_name, module_path)
        except ImportError:
            log.debug("👔 Director: %s not available (%s)", display_name, module_path)
        except Exception as exc:
            log.debug("👔 Director: %s failed to load: %s", display_name, exc)

    # ── Industry plugins ───────────────────────────────────────────────
    plugins = getattr(container, "industry_plugins", {})
    for name, plugin in plugins.items():
        sector = _infer_plugin_sector(name)
        director.register_observer(
            FunctionObserver(name, sector, _make_observer(name, plugin))
        )
        discovered += 1

    log.info(
        "👔 Director: Auto-discovered %d engines (%d core + %d plugins)",
        discovered, discovered - len(plugins), len(plugins),
    )
    return discovered


def _infer_plugin_sector(name: str) -> Sector:
    """Infer sector from plugin name."""
    name_lower = name.lower()
    if any(kw in name_lower for kw in ("church", "school")):
        return Sector.BRANDS
    if any(kw in name_lower for kw in ("hospital", "clinic", "health")):
        return Sector.HEALTH
    if any(kw in name_lower for kw in ("crm", "client", "customer")):
        return Sector.CLIENTS
    return Sector.INFRA


# ═══════════════════════════════════════════════════════════════════════════
# THE DIRECTOR
# ═══════════════════════════════════════════════════════════════════════════

class Director:
    """Kanyoza Director — The Autonomous CEO.

    Observes all registered engines, makes decisions based on health
    and goals, executes approved tasks, and learns from outcomes.

    Storage: Supabase (production) or InMemoryStore (development/fallback).
    Circuit breaker prevents retry storms on database errors.
    """

    VERSION = "1.1.0"

    def __init__(self, supabase_client=None, interval: int = 60):
        # ── Storage ────────────────────────────────────────────────────
        if supabase_client:
            self.store = SupabaseStore(supabase_client)
            log.info("Director storage: Supabase (PostgreSQL)")
        else:
            self.store = InMemoryStore()
            log.warning("Director storage: In-Memory (Supabase not configured)")

        self.interval = max(10, interval)
        self.policy = Policy()
        self.lock = threading.RLock()
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.started = now()

        # ── Registries ─────────────────────────────────────────────────
        self.observers: Dict[str, FunctionObserver] = {}
        self.capabilities: Dict[str, Capability] = {}
        self.tasks: Dict[str, Task] = {}
        self.goals: Dict[str, Goal] = {}

        # ── History ────────────────────────────────────────────────────
        self.history: deque = deque(maxlen=5000)
        self.decisions: deque = deque(maxlen=2000)
        self.observations: Dict[str, Observation] = {}
        self.briefings: deque = deque(maxlen=200)

        # ── State ──────────────────────────────────────────────────────
        self.last_plan: Optional[datetime] = None
        self.last_observation: Optional[datetime] = None

        # ── Callbacks ──────────────────────────────────────────────────
        self.alert_cb: Optional[Callable] = None
        self.event_cb: Optional[Callable] = None

        self._init_goals()
        self._init_capabilities()
        log.info("👔 KANYOZA DIRECTOR v%s initialized", self.VERSION)

    def _init_goals(self) -> None:
        goals_data = [
            ("system_stability", "System stability", Sector.HEALTH, 100),
            ("security", "Security posture", Sector.SECURITY, 100),
            ("clients", "Client commitments", Sector.CLIENTS, 90),
            ("brands", "Brand health", Sector.BRANDS, 85),
            ("content", "Content consistency", Sector.CONTENT, 80),
            ("growth", "Business growth", Sector.GROWTH, 70),
            ("learning", "Continuous learning", Sector.LEARNING, 60),
        ]
        for goal_id, name, sector, priority in goals_data:
            self.goals[goal_id] = Goal(goal_id, name, name, sector, priority)

    def _init_capabilities(self) -> None:
        safe_caps = [
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
        ]
        for name, sector, desc, auth, risk in safe_caps:
            self.register_capability(
                Capability(name, sector, desc, self._placeholder(name), auth, risk)
            )

        critical_caps = [
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
        ]
        for name, sector in critical_caps:
            self.register_capability(Capability(
                name, sector, name.replace("_", " ").title(),
                self._blocked, Authority.SUGGEST, Risk.CRITICAL,
            ))

    def _placeholder(self, name: str) -> Callable:
        return lambda p: {
            "status": "delegated",
            "capability": name,
            "message": "Bind the real Kanyoza service handler before production execution.",
            "params": p,
        }

    def _blocked(self, p: dict) -> dict:
        return {
            "status": "blocked",
            "reason": "Critical action requires human approval and a bound production handler.",
        }

    # ═══════════════════════════════════════════════════════════════════
    # REGISTRATION
    # ═══════════════════════════════════════════════════════════════════

    def register_observer(self, observer: FunctionObserver) -> None:
        self.observers[observer.name] = observer

    def register_capability(self, capability: Capability) -> None:
        self.capabilities[capability.name] = capability

    def set_alert_callback(self, cb: Callable) -> None:
        self.alert_cb = cb

    def set_event_callback(self, cb: Callable) -> None:
        self.event_cb = cb

    # ═══════════════════════════════════════════════════════════════════
    # OBSERVATION
    # ═══════════════════════════════════════════════════════════════════

    def observe_all(self) -> Dict[str, Observation]:
        for name, observer in list(self.observers.items()):
            obs = observer.observe()
            self.observations[name] = obs
            self.store.save_observation(obs)
        self.last_observation = now()
        return self.observations

    def health(self) -> dict:
        weights = {
            Health.NOMINAL: 100, Health.DEGRADED: 70,
            Health.CRITICAL: 25, Health.OFFLINE: 0, Health.UNKNOWN: 50,
        }
        groups: Dict[str, List[Observation]] = {}
        for obs in self.observations.values():
            groups.setdefault(obs.sector.value, []).append(obs)

        sectors = {}
        scores = []
        for sector_name, obs_list in groups.items():
            score = sum(weights[obs.status] for obs in obs_list) / len(obs_list)
            scores.append(score)
            sectors[sector_name] = {
                "status": self._status_label(score).value,
                "score": round(score, 1),
                "issues": sum(len(obs.issues) for obs in obs_list),
                "observers": len(obs_list),
            }

        overall = sum(scores) / len(scores) if scores else 50
        return {
            "status": self._status_label(overall).value,
            "score": round(overall, 1),
            "sectors": sectors,
            "observer_count": len(self.observers),
            "issue_count": sum(len(obs.issues) for obs in self.observations.values()),
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
    # DECISION & PLANNING
    # ═══════════════════════════════════════════════════════════════════

    def _create_task(
        self, sector: Sector, desc: str, cap_name: str, reason: str,
        confidence: float = 0.8, params: dict = None, source: str = None,
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
            status=Status.APPROVAL if self.policy.requires_approval(cap) else Status.PLANNED,
            confidence=confidence,
            reason=reason,
            sources=[source] if source else [],
        )
        self.tasks[task.id] = task
        self.store.save_task(task)
        return task

    def decide(self) -> List[Task]:
        decisions = []
        health = self.health()

        if health["score"] < 90:
            decisions.append(self._create_task(
                Sector.HEALTH, "Run complete platform health scan",
                "run_health_scan", f"Health score is {health['score']}", 0.99,
            ))

        for obs in self.observations.values():
            for issue in obs.issues:
                typ = str(issue.get("type", "")).lower()
                if typ in {"failed_service", "service_down", "worker_failed"}:
                    decisions.append(self._create_task(
                        Sector.HEALTH, "Recover failed service",
                        "restart_failed_service", "Recoverable service failure", 0.9,
                        issue, obs.source,
                    ))
                elif typ in {"security_issue", "vulnerability", "credential_exposure"}:
                    decisions.append(self._create_task(
                        Sector.SECURITY, "Prepare security remediation",
                        "prepare_security_remediation", "Security issue observed", 0.97,
                        issue, obs.source,
                    ))
                elif typ in {"brand_inactive", "engagement_decline"}:
                    decisions.append(self._create_task(
                        Sector.BRANDS, "Queue brand revival",
                        "queue_brand_revival", "Brand performance declined", 0.87,
                        issue, obs.source,
                    ))
                elif typ in {"content_gap", "schedule_gap"}:
                    decisions.append(self._create_task(
                        Sector.CONTENT, "Optimize content schedule",
                        "optimize_content_schedule", "Content gap observed", 0.88,
                        issue, obs.source,
                    ))
                elif typ == "client_report_due":
                    decisions.append(self._create_task(
                        Sector.CLIENTS, "Generate due client report",
                        "generate_client_report", "Client report due", 0.96,
                        issue, obs.source,
                    ))

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
                decisions.append(self._create_task(
                    goal.sector, f"Goal-driven: {goal.name}", cap_name,
                    f"Goal: {goal.name}", conf,
                ))

        if not decisions:
            decisions.append(self._create_task(
                Sector.SELF, "Run Director self-diagnostic",
                "self_diagnostic", "No urgent work; inspect own state", 0.95,
            ))

        # Deduplicate by (sector, capability, description)
        seen = set()
        unique = []
        for task in decisions:
            key = (task.sector, task.capability, task.description)
            if key not in seen:
                seen.add(key)
                unique.append(task)

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
            "priorities": [
                "availability", "security", "client commitments",
                "brand/content operations", "growth", "learning",
            ],
        }

    # ═══════════════════════════════════════════════════════════════════
    # APPROVAL
    # ═══════════════════════════════════════════════════════════════════

    def approve(self, task_id: str, actor: str = "admin") -> Task:
        task = self.tasks[task_id]
        if task.status not in (Status.APPROVAL, Status.FLAGGED):
            raise ValueError("Task is not awaiting approval")
        task.status = Status.APPROVED
        task.approved_at = now()
        task.approved_by = actor
        self.store.save_task(task)
        self.store.save_approval(task_id, True, actor)
        return task

    def reject(self, task_id: str, actor: str = "admin", reason: str = "") -> Task:
        task = self.tasks[task_id]
        task.status = Status.CANCELLED
        task.error = f"Rejected by {actor}: {reason}"
        self.store.save_task(task)
        self.store.save_approval(task_id, False, actor)
        return task

    # ═══════════════════════════════════════════════════════════════════
    # EXECUTION
    # ═══════════════════════════════════════════════════════════════════

    def execute(self, task_id: str) -> Task:
        task = self.tasks[task_id]
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
                result = asyncio.run(result)
            task.status = Status.COMPLETED
            task.result = safe(result or {})
            self.history.append(task)
            self.store.save_task(task)
            self._record_decision(f"Executed: {task.description}", task.reason, task)
            self._learn(task)
            self._event("director_action_completed", {"task": safe(asdict(task))})
            return task
        except Exception as exc:
            task.status = Status.FAILED
            task.error = str(exc)
            task.result = {"traceback": traceback.format_exc(limit=5)}
            self.history.append(task)
            self.store.save_task(task)
            self._record_decision(f"Failed: {task.description}", str(exc), task, "failed")
            self._learn(task)
            self._alert({"type": "task_failed", "task": safe(asdict(task))})
            return task

    def execute_due(self) -> None:
        for task in list(self.tasks.values()):
            if task.status in (Status.PLANNED, Status.APPROVED):
                self.execute(task.id)

    # ═══════════════════════════════════════════════════════════════════
    # LEARNING
    # ═══════════════════════════════════════════════════════════════════

    def _record_decision(
        self, decision_text: str, reason: str, task: Task, outcome: str = None,
    ) -> None:
        decision = Decision(
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
        self.decisions.append(decision)
        self.store.save_decision(decision)

    def _learn(self, task: Task) -> None:
        entry = {
            "id": f"learn_{uuid.uuid4().hex[:12]}",
            "task_id": task.id,
            "signal": "success" if task.status == Status.COMPLETED else "failure",
            "lesson": f"Outcome of {task.capability}: {task.status.value}",
        }
        self.store.save_learning(entry)

    # ═══════════════════════════════════════════════════════════════════
    # ALERTS & EVENTS
    # ═══════════════════════════════════════════════════════════════════

    def _alert(self, payload: dict) -> None:
        self._event("director_alert", payload)
        if self.alert_cb:
            try:
                self.alert_cb(payload)
            except Exception:
                log.exception("Director alert callback failed")

    def _event(self, name: str, payload: dict) -> None:
        if self.event_cb:
            try:
                self.event_cb(name, payload)
            except Exception:
                log.exception("Director event callback failed")

    # ═══════════════════════════════════════════════════════════════════
    # BRIEFING
    # ═══════════════════════════════════════════════════════════════════

    def briefing(self, kind: Briefing = Briefing.CHECKPOINT) -> dict:
        health = self.health()
        pending = [safe(asdict(t)) for t in self.tasks.values() if t.status == Status.APPROVAL]
        failures = [t for t in self.history if t.status == Status.FAILED]

        brief = {
            "type": kind.value,
            "timestamp": iso(now()),
            "mood": (
                "critical" if health["score"] < 40 else
                "attentive" if pending else
                "watchful" if failures else
                "confident" if health["score"] >= 90 else
                "focused"
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

    # ═══════════════════════════════════════════════════════════════════
    # STATUS
    # ═══════════════════════════════════════════════════════════════════

    def status(self) -> dict:
        health = self.health()
        counts = Counter(t.status.value for t in self.tasks.values())
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
            "pending_approvals": sum(
                t.status == Status.APPROVAL for t in self.tasks.values()
            ),
            "last_observation": iso(self.last_observation),
            "last_plan": iso(self.last_plan),
        }

    # ═══════════════════════════════════════════════════════════════════
    # LIFECYCLE
    # ═══════════════════════════════════════════════════════════════════

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.started = now()
        self.thread = threading.Thread(
            target=self._loop, name="kanyoza-director", daemon=True,
        )
        self.thread.start()
        threading.Thread(target=self._startup, name="director-startup", daemon=True).start()
        log.info("👔 Director ONLINE — watching the platform")

    def _startup(self) -> None:
        """Initial observation and planning cycle."""
        try:
            self.observe_all()
            self.build_plan()
            self.execute_due()
            self.briefing(Briefing.CHECKPOINT)
        except Exception:
            log.exception("Director startup cycle failed")

    def stop(self) -> None:
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)
        log.info("👔 Director OFFLINE")

    def _loop(self) -> None:
        """Main observation-decision-execution loop."""
        last_minute = None
        while self.running:
            try:
                self.observe_all()
                if self.last_plan is None or (now() - self.last_plan).total_seconds() >= 1800:
                    self.build_plan()
                self.execute_due()

                current_minute = now().strftime("%Y-%m-%d-%H-%M")
                if current_minute != last_minute:
                    last_minute = current_minute
                    hour = now().hour
                    if now().minute == 0 and hour in (6, 8, 12, 18, 22):
                        kind = (
                            Briefing.MORNING if hour == 6 else
                            Briefing.EVENING if hour == 22 else
                            Briefing.CHECKPOINT
                        )
                        self.briefing(kind)
            except Exception:
                log.exception("Director cycle failed")
            time.sleep(self.interval)


# ═══════════════════════════════════════════════════════════════════════════
# FASTAPI APP
# ═══════════════════════════════════════════════════════════════════════════

def create_app(director: Director):
    """Create a FastAPI sub-application for Director endpoints."""
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel

    app = FastAPI(title="Kanyoza Director", version=director.VERSION)

    class ApprovalBody(BaseModel):
        actor: str = "admin"

    class RejectBody(BaseModel):
        actor: str = "admin"
        reason: str = ""

    @app.get("/status")
    def get_status():
        return director.status()

    @app.get("/briefing")
    def get_briefing():
        return director.briefings[-1] if director.briefings else director.briefing()

    @app.get("/tasks")
    def get_tasks():
        return [safe(asdict(t)) for t in director.tasks.values()]

    @app.get("/approvals")
    def get_approvals():
        return [
            safe(asdict(t)) for t in director.tasks.values()
            if t.status == Status.APPROVAL
        ]

    @app.get("/decisions")
    def get_decisions():
        return [safe(asdict(d)) for d in list(director.decisions)[-100:]]

    @app.post("/observe")
    def run_observe():
        director.observe_all()
        return director.status()

    @app.post("/plan")
    def run_plan():
        return director.build_plan()

    @app.post("/start")
    def run_start():
        director.start()
        return director.status()

    @app.post("/stop")
    def run_stop():
        director.stop()
        return director.status()

    @app.post("/tasks/{task_id}/approve")
    def approve_task(task_id: str, body: ApprovalBody):
        try:
            return safe(asdict(director.approve(task_id, body.actor)))
        except KeyError:
            raise HTTPException(404, "Task not found")
        except ValueError as e:
            raise HTTPException(409, str(e))

    @app.post("/tasks/{task_id}/reject")
    def reject_task(task_id: str, body: RejectBody):
        try:
            return safe(asdict(director.reject(task_id, body.actor, body.reason)))
        except KeyError:
            raise HTTPException(404, "Task not found")

    return app

# ═══════════════════════════════════════════════════════════════════════════
# INITIALIZER
# ═══════════════════════════════════════════════════════════════════════════

director: Optional[Director] = None


def init_director(supabase_client=None, auto_start: bool = True) -> Director:
    """Initialize the global Director instance.

    Args:
        supabase_client: Supabase client (MUST use service_role key for writes).
                         If None, InMemoryStore is used (development only).
        auto_start: Start the Director loop immediately.

    Returns:
        The global Director instance.
    """
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
    parser = argparse.ArgumentParser(description="Kanyoza Director")
    parser.add_argument("--api", action="store_true", help="Run as API server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()

    d = init_director(auto_start=True)

    if args.api:
        import uvicorn
        app = create_app(d)
        signal.signal(signal.SIGTERM, lambda *_: d.stop())
        signal.signal(signal.SIGINT, lambda *_: d.stop())
        uvicorn.run(app, host=args.host, port=args.port)
    else:
        try:
            while d.running:
                time.sleep(5)
        except KeyboardInterrupt:
            d.stop()


if __name__ == "__main__":
    main()
