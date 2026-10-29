from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field

SCHEMA = "KDO.BusinessDecisionIntelligence/v1"


class DecisionDisposition(str, Enum):
    RECOMMEND = "RECOMMEND"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    REVIEW = "REVIEW"
    BLOCK = "BLOCK"
    NO_DECISION = "NO_DECISION"


class DecisionRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class DecisionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA
    decision_id: str = Field(..., min_length=1)
    workspace_id: str = Field(..., min_length=1)
    objective_id: Optional[str] = None
    question: str = ""
    disposition: DecisionDisposition
    recommendation: str = ""
    rationale: str = ""
    risk: DecisionRisk = DecisionRisk.UNKNOWN
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_refs: List[Dict[str, Any]] = Field(default_factory=list)
    constraint_refs: List[str] = Field(default_factory=list)
    drift_refs: List[str] = Field(default_factory=list)
    option_refs: List[str] = Field(default_factory=list)
    candidate_id: Optional[str] = None
    evaluation_id: Optional[str] = None
    recommendation_id: Optional[str] = None
    audit_id: Optional[str] = None
    action_type: Optional[str] = None
    action_authority_granted: bool = False
    governance_reason: str = ""
    provenance: Dict[str, Any] = Field(default_factory=dict)


class BusinessDecisionIntelligence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA
    workspace_id: str
    decisions: List[DecisionRecord] = Field(default_factory=list)
    coverage: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)

    def decision(self, decision_id: str) -> Optional[DecisionRecord]:
        return next((item for item in self.decisions if item.decision_id == decision_id), None)
