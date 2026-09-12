"""Canonical goal/intent contract for organism planning.

This complements the existing ConditionEngine goals rather than replacing
that engine.  It defines the planning vocabulary and authority boundary.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Optional
import uuid


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
