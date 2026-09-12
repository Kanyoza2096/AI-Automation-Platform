"""Pydantic schemas for API validation."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ProposalRequest(BaseModel):
    """Request to create a proposal."""
    action_name: str = Field(min_length=1, max_length=100)
    action_params: Dict[str, Any] = Field(default_factory=dict)
    source_engine: str = Field(min_length=1, max_length=50)
    description: str = Field(min_length=1, max_length=500)
    proposal_type: str = Field(default="general", max_length=50)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class VoteResponse(BaseModel):
    """Vote response schema."""
    member: str
    role: str
    vote: str
    confidence: float
    reason: str
    weight: float
    reliability: float
    relevance: float
    effective_influence: float


class DeliberationResponse(BaseModel):
    """Full deliberation response."""
    proposal_id: str
    decision: str
    approve_ratio: float
    reject_ratio: float
    quorum_ratio: float
    confidence: float
    votes: List[VoteResponse]
    dissent: List[Dict[str, Any]]
    warnings: List[str]
    risk_level: str
    tier_used: int
    early_terminated: bool
    fallback_used: bool
    deliberation_time_ms: float
    correlation_id: str
    voted_at: str


class OutcomeRequest(BaseModel):
    """Request to record outcome."""
    proposal_id: str
    decision: str
    actual_outcome: str
    success_metric: float = Field(default=0.5, ge=0.0, le=1.0)
    notes: str = ""