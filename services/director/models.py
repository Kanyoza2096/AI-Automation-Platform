"""
KANYOZA DIRECTOR — Data Models (v5.5 complete build)

v5.5 CHANGES:
  - Proposal dataclass: the Director proposes changes, doesn't just act.
  - DirectorSignal dataclass: signals from detectors, candidates for
    proposals.
  - Status gains APPROVED_PENDING_EXECUTION for scheduled proposals.
  - Capability gains `proposal_capable: bool` — capabilities that can be
    proposed by the Director based on detected signals vs. capabilities
    that fire directly from decide().
  - Outcome predicates and verification windows fully wired.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

from .utils import (
    now, utcnow, iso, iso_now, sanitize_error_message, generate_correlation_id,
)

log = logging.getLogger("kanyoza.director")

try:
    from opentelemetry import trace
    tracer = trace.get_tracer("kanyoza.director", "5.5.0")
    HAS_TRACING = True
except ImportError:
    tracer = None
    HAS_TRACING = False

try:
    from council.integration import get_council
    HAS_COUNCIL = True
except ImportError:
    HAS_COUNCIL = False

try:
    from core.unified_bus import get_unified_bus
    HAS_EVENT_BUS = True
except ImportError:
    HAS_EVENT_BUS = False


class SupabaseErrorClassifier:
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
        return any(x in error_str for x in (
            'timeout', 'connection', 'network', 'dns',
            'reset', 'refused', 'unreachable', 'temporary',
        ))

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


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpenError(Exception):
    def __init__(self, name: str):
        self.name = name
        super().__init__(f"Circuit breaker '{name}' is OPEN")


class AsyncCircuitBreaker:
    def __init__(
        self,
        name: str = "default",
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
    ):
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
        except Exception:
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
                log.error(
                    "Circuit breaker '%s': CLOSED → OPEN (%d failures)",
                    self.name, self._failure_count,
                )


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
    APPROVED_PENDING_EXECUTION = "approved_pending_execution"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    FLAGGED = "flagged"
    CANCELLED = "cancelled"
    VERIFIED = "verified"
    INEFFECTIVE = "ineffective"
    BLOCKED = "blocked"


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


class ProposalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    EXPIRED = "expired"


@dataclass
class Capability:
    name: str
    sector: Sector
    description: str
    handler: Callable[[Dict[str, Any]], Any]
    authority: Authority = Authority.SUGGEST
    risk: Risk = Risk.MEDIUM
    enabled: bool = True
    target_param: Optional[str] = None
    outcome_predicate: Optional[Callable[[Dict[str, Any], Dict[str, Any], Dict[str, Any]], bool]] = None
    verification_window_seconds: int = 0
    # v5.5: whether the Director can propose this capability from a
    # detected signal. If False, only the dispatch table fires it.
    proposal_capable: bool = False
    # Proposal-backed tasks must use this explicit governed boundary. It must
    # delegate to the canonical Action Kernel and return its receipt contract;
    # the ordinary handler is deliberately not a fallback for approved proposals.
    governed_handoff: Optional[Callable[..., Any]] = None
    # Closed JSON-Schema-compatible input contract required before mission staging.
    # None deliberately means the capability is not eligible for staging.
    parameter_schema: Optional[Dict[str, Any]] = None
    # Explicit admission policy; only read-only review operations are enabled initially.
    mission_staging_mode: str = "disabled"


@dataclass
class Observation:
    source: str
    sector: Sector
    status: Health = Health.UNKNOWN
    metrics: Dict[str, Any] = field(default_factory=dict)
    issues: List[Dict[str, Any]] = field(default_factory=list)
    message: str = ""
    timestamp: datetime = field(default_factory=now)
    # Observer evidence is platform-wide unless an explicit workspace is supplied.
    workspace_id: Optional[str] = None
    scope_type: str = "platform"
    contract_version: str = "kdo.observer.v1"
    health_contract: str = "direct_observation"
    timestamp_valid: bool = True

    def __post_init__(self) -> None:
        if self.workspace_id and self.workspace_id.strip():
            self.workspace_id = self.workspace_id.strip()
            self.scope_type = "workspace"


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
    proposal_id: Optional[str] = None   # v5.5: link back to the proposal
    # Explicit tenant ownership for operator-authored mission tasks.
    # Legacy tasks remain unscoped and retain proposal-based visibility rules.
    workspace_id: Optional[str] = None
    # Preserve explicit database ownership when restoring/updating legacy rows.
    scope_type: str = "platform"
    # Optional mission-graph metadata. Empty defaults preserve legacy task behavior.
    depends_on: List[str] = field(default_factory=list)
    mission_id: Optional[str] = None
    success_criteria: List[Any] = field(default_factory=list)
    recovery_policy: str = "require_review"
    # Advisory ranking inputs. They never grant execution authority.
    impact_score: float = 50.0
    mission_priority: float = 50.0
    # Timestamp of the observation that justified this task; enables evidence-aware recurrence deduplication.
    evidence_observed_at: Optional[datetime] = None
    condition_fingerprint: Optional[str] = None

    def __post_init__(self) -> None:
        if self.workspace_id and self.workspace_id.strip():
            self.workspace_id = self.workspace_id.strip()
            self.scope_type = "workspace"


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
    # Evidence-aware decision audit fields. Defaults preserve legacy records.
    stage: str = "execution"
    observed_state: List[Dict[str, Any]] = field(default_factory=list)
    inferences: List[Dict[str, Any]] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    evidence_sources: List[Dict[str, Any]] = field(default_factory=list)
    evidence_state: str = "unknown"
    justification: str = ""
    action_taken: Dict[str, Any] = field(default_factory=dict)
    confidence_basis: Dict[str, Any] = field(default_factory=dict)


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
# v5.5 — PROPOSALS AND SIGNALS
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class DirectorSignal:
    """A pattern detected by a detector. Candidate for a proposal.

    Signals are the Director's way of noticing. Each detector produces
    signals; the proposal generator turns signals into actionable
    proposals; the human approves or rejects; approved proposals become
    tasks that execute through the Action Kernel; outcomes feed back to
    the learning loop.

    The full flow:
        detector → signal → proposal → approval → task → execution → outcome
    """
    id: str
    detector: str                     # "engagement_by_hour" | "load_forecast" | ...
    sector: Sector
    signal_type: str                  # "schedule_opportunity" | "scale_recommendation" | ...
    subject_type: str                 # "brand" | "worker_pool" | "content_series"
    subject_id: str                   # e.g. "brand_abc123"
    workspace_id: str
    confidence: float                 # 0..1
    rationale: str                    # human-readable why
    payload: Dict[str, Any]           # structured detail (metrics, samples, etc.)
    detected_at: datetime = field(default_factory=now)
    expires_at: Optional[datetime] = None   # signals can be stale
    consumed_by_proposal_id: Optional[str] = None


@dataclass
class Proposal:
    """A concrete suggestion from the Director to the human.

    A proposal contains: what was noticed, what would be done about it,
    the cost and risk, and a reference to the capability that would
    execute it if approved.
    """
    id: str
    workspace_id: str
    sector: Sector
    title: str                        # short one-line
    rationale: str                    # multi-paragraph explanation
    capability: str                   # which capability executes this
    params: Dict[str, Any]            # params to pass to the capability
    target_param_value: Optional[str] # the extracted target for learning
    expected_benefit: str             # "20% more engagement" | "$0.36/hr" | ...
    estimated_cost: str               # "$0.00" | "$0.36/hr" | "N/A"
    risk: Risk
    confidence: float
    signal_ids: List[str]             # which signals triggered this
    status: ProposalStatus = ProposalStatus.PENDING
    created_at: datetime = field(default_factory=now)
    expires_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    approved_by: Optional[str] = None
    rejected_reason: Optional[str] = None
    executed_task_id: Optional[str] = None
    outcome: Optional[str] = None     # "verified" | "ineffective" | "failed"


__all__ = [
    "SupabaseErrorClassifier", "CircuitState", "CircuitBreakerOpenError",
    "AsyncCircuitBreaker", "Authority", "Sector", "Status", "Health",
    "Risk", "Briefing", "ProposalStatus",
    "Capability", "Observation", "Task", "Decision", "Goal",
    "StrategicOutlook", "ResourceAllocation", "DependencyNode",
    "RootCauseAnalysis", "LearningSignal",
    "DirectorSignal", "Proposal",
    "HAS_TRACING", "HAS_COUNCIL", "HAS_EVENT_BUS", "tracer",
]
