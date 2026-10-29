"""
Council domain models — shared types for deliberation.

Phase 0: previously imported as council.models but the module was missing.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class DecisionState(str, Enum):
    APPROVED = "approved"
    APPROVED_WITH_WARNINGS = "approved_with_warnings"
    REJECTED = "rejected"
    BLOCKED = "blocked"
    NEEDS_REVISION = "needs_revision"
    PENDING = "pending"
    ESCALATED = "escalated"


class RiskLevel(str, Enum):
    TRIVIAL = "trivial"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class VetoType(str, Enum):
    NONE = "none"
    SOFT = "soft"
    HARD = "hard"


@dataclass
class Proposal:
    action_name: str
    source_engine: str = "unknown"
    description: str = ""
    proposal_type: str = "general"
    action_params: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    risk_level: RiskLevel = RiskLevel.MEDIUM
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if isinstance(self.risk_level, str):
            try:
                self.risk_level = RiskLevel(self.risk_level.lower())
            except ValueError:
                self.risk_level = RiskLevel.MEDIUM


@dataclass
class PersonaVote:
    member: str
    role: str
    vote: str  # approve | reject | abstain
    confidence: float
    reason: str
    weight: float = 1.0
    reliability: float = 0.5
    veto_type: VetoType = VetoType.NONE
    evidence: Optional[Dict[str, Any]] = None
    evidence_quality: float = 0.5
    relevance: float = 1.0


@dataclass
class DeliberationResult:
    proposal_id: str
    decision: DecisionState
    approve_ratio: float
    reject_ratio: float
    quorum_ratio: float
    confidence: float
    votes: List[PersonaVote] = field(default_factory=list)
    dissent: List[Any] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.MEDIUM
    tier_used: int = 0
    deliberation_time_ms: float = 0.0
    fallback_used: bool = False
    early_terminated: bool = False


@dataclass
class OutcomeRecord:
    proposal_id: str
    decision: DecisionState
    actual_outcome: str
    success_metric: float = 0.5
    notes: str = ""
    recorded_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
