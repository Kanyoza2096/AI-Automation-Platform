from __future__ import annotations
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field
from domain.business_activation.facts import FactType

class DiscoveryStatus(str, Enum):
    ACTIVE = 'ACTIVE'
    READY = 'READY'
    HANDOFF = 'HANDOFF'

class DiscoveryGap(BaseModel):
    model_config = ConfigDict(extra='forbid')
    gap_id: str = Field(default_factory=lambda: f'gap-{uuid4().hex}')
    fact_type: FactType
    reason: str = Field(..., min_length=1, max_length=1000)
    requested_evidence: list[str] = Field(default_factory=list)
    resolved: bool = False

class DiscoveryTurn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    turn_id: str = Field(default_factory=lambda: f'turn-{uuid4().hex}')
    role: str = Field(..., pattern=r'^(agent|business|system)$')
    text: str = Field(..., min_length=1, max_length=12000)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    extraction_id: Optional[str] = None
    confirmed_fact_ids: list[str] = Field(default_factory=list)

class DiscoverySession(BaseModel):
    model_config = ConfigDict(extra='forbid')
    session_id: str = Field(default_factory=lambda: f'discovery-{uuid4().hex}')
    workspace_id: str
    status: DiscoveryStatus = DiscoveryStatus.ACTIVE
    turns: list[DiscoveryTurn] = Field(default_factory=list)
    gaps: list[DiscoveryGap] = Field(default_factory=list)
    requested_scope: list[FactType] = Field(default_factory=list, max_length=20)
    # Explainable planning metadata produced by R13-B. These are orchestration
    # hints only; they are not business truth and do not replace Knowledge Plane facts.
    planner_reasons: list[str] = Field(default_factory=list, max_length=100)
    detected_profiles: list[str] = Field(default_factory=list, max_length=50)
    completed_fact_types: list[FactType] = Field(default_factory=list)
    pending_extraction_id: Optional[str] = None
    pending_fact_ids: list[str] = Field(default_factory=list)
    current_gap_id: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class StartDiscoveryRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    workspace_id: str = Field(..., min_length=1, max_length=128)
    business_description: str = Field(..., min_length=1, max_length=12000)
    requested_scope: list[FactType] = Field(default_factory=list, max_length=20)

class ContinueDiscoveryRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    workspace_id: str = Field(..., min_length=1, max_length=128)
    session_id: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1, max_length=12000)
    confirm_fact_ids: list[str] = Field(default_factory=list, max_length=100)
    confirm_pending: bool = False
