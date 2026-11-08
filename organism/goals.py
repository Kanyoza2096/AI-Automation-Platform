"""Canonical goal/intent contract for organism planning.

This complements the existing ConditionEngine goals rather than replacing
that engine. It also defines bounded, evidence-grounded goal proposals.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Mapping, Optional
import hashlib
import re
import uuid

from organism.perception import Observation, ObservationKind


class GoalPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class GoalState(str, Enum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    AT_RISK = "at_risk"
    ACHIEVED = "achieved"
    FAILED = "failed"
    PAUSED = "paused"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Goal:
    """Tenant-scoped desired state with explicit constraints and authority."""

    workspace_id: str
    title: str
    success_condition: str
    priority: GoalPriority = GoalPriority.NORMAL
    deadline: Optional[datetime] = None
    constraints: Mapping[str, Any] = field(default_factory=dict)
    budget: Mapping[str, float] = field(default_factory=dict)
    authority: str = "suggest"
    state: GoalState = GoalState.PROPOSED
    progress: float = 0.0
    goal_id: str = field(default_factory=lambda: f"goal_{uuid.uuid4().hex[:16]}")
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not str(self.workspace_id).strip() or not str(self.title).strip():
            raise ValueError("workspace_id and title are required")
        if not str(self.success_condition).strip():
            raise ValueError("success_condition is required")
        if not 0.0 <= float(self.progress) <= 1.0:
            raise ValueError("progress must be between 0 and 1")
        if self.deadline is not None and self.deadline.tzinfo is None:
            raise ValueError("deadline must be timezone-aware")
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        if self.authority not in {"suggest", "execute", "publish"}:
            raise ValueError("authority must be suggest, execute, or publish")

    def is_overdue(self, now: Optional[datetime] = None) -> bool:
        if self.deadline is None or self.state in {GoalState.ACHIEVED, GoalState.CANCELLED}:
            return False
        return (now or datetime.now(timezone.utc)) >= self.deadline

    def with_progress(self, progress: float, *, state: Optional[GoalState] = None) -> "Goal":
        bounded = max(0.0, min(1.0, float(progress)))
        next_state = state or (GoalState.ACHIEVED if bounded >= 1.0 else self.state)
        return Goal(**{**self.__dict__, "progress": bounded, "state": next_state})


@dataclass(frozen=True)
class GoalProposal:
    """Evidence-backed desired-state proposal; never an execution grant."""

    proposal_id: str
    workspace_id: str
    title: str
    success_condition: str
    source_event_ids: tuple[str, ...]
    priority: GoalPriority
    risk_level: str
    expected_outcome: str
    created_at: datetime
    expires_at: datetime
    authority: str = "suggest"
    status: str = "proposed"

    def __post_init__(self) -> None:
        if not self.proposal_id or not self.workspace_id.strip():
            raise ValueError("proposal_id and workspace_id are required")
        if not self.title.strip() or not self.success_condition.strip():
            raise ValueError("title and success_condition are required")
        if not self.expected_outcome.strip():
            raise ValueError("expected_outcome is required")
        if not self.source_event_ids or len(self.source_event_ids) > 8:
            raise ValueError("one to eight source_event_ids are required")
        if any(not item.strip() for item in self.source_event_ids):
            raise ValueError("source_event_ids cannot contain blank values")
        if len(set(self.source_event_ids)) != len(self.source_event_ids):
            raise ValueError("source_event_ids must be unique")
        if self.risk_level not in {"low", "medium", "high", "critical"}:
            raise ValueError("unsupported risk_level")
        if self.authority != "suggest":
            raise ValueError("event-derived goal proposals must remain suggest-only")
        if self.status != "proposed":
            raise ValueError("new goal proposals must start as proposed")
        if self.created_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("proposal timestamps must be timezone-aware")
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be later than created_at")


def derive_goal_proposal(
    observation: Observation,
    *,
    now: Optional[datetime] = None,
    ttl: timedelta = timedelta(hours=1),
) -> Optional[GoalProposal]:
    """Derive a deterministic suggestion from a trusted degraded-service event.

    This intentionally narrow first rule accepts only service_heartbeat and
    engine_alert health observations with explicit degraded status and event
    provenance. It does not call an LLM, activate a goal, or execute remediation.
    """
    if not isinstance(observation, Observation) or observation.kind != ObservationKind.HEALTH:
        return None
    if observation.source not in {
        "unified_event:service_heartbeat",
        "unified_event:engine_alert",
    }:
        return None
    if observation.confidence < 0.75 or observation.source_trust < 0.75:
        return None
    if not isinstance(observation.value, Mapping):
        return None

    status = str(observation.value.get("status") or "").strip().lower()
    if status not in {"degraded", "unhealthy", "down", "critical"}:
        return None

    event_id = str(observation.metadata.get("event_id") or "").strip()
    if not event_id or len(event_id) > 160:
        return None
    raw_service = (
        observation.value.get("service")
        or observation.value.get("service_name")
        or observation.value.get("component")
    )
    if not isinstance(raw_service, str):
        return None
    service = re.sub(r"[^A-Za-z0-9_.:-]+", "-", raw_service.strip())[:80].strip("-")
    if not service:
        return None

    created = now or datetime.now(timezone.utc)
    if created.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if ttl <= timedelta(0):
        raise ValueError("ttl must be positive")

    proposal_key = f"{observation.workspace_id}:{event_id}:service-health-recovery"
    proposal_id = "gprop_" + hashlib.sha256(proposal_key.encode("utf-8")).hexdigest()[:20]
    priority = GoalPriority.CRITICAL if status in {"down", "critical"} else GoalPriority.HIGH
    risk_level = "high" if status in {"down", "critical"} else "medium"
    title = f"Restore health of {service}"
    success = f"A subsequent trusted heartbeat reports {service} as healthy."
    return GoalProposal(
        proposal_id=proposal_id,
        workspace_id=observation.workspace_id,
        title=title,
        success_condition=success,
        source_event_ids=(event_id,),
        priority=priority,
        risk_level=risk_level,
        expected_outcome=success,
        created_at=created,
        expires_at=created + ttl,
    )
