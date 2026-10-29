"""Marketing & Media completion-domain models."""
from datetime import datetime
from typing import Optional
from pydantic import Field
from models.base import KanyozaModel


class Campaign(KanyozaModel):
    id: str
    workspace_id: str
    name: str
    objective_id: str
    status: str = Field(default="draft")
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    budget: float = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)
    updated_at: Optional[datetime] = None


class ContentSchedule(KanyozaModel):
    id: str
    workspace_id: str
    campaign_id: str
    candidate_id: str
    platform: str
    scheduled_at: datetime
    status: str = Field(default="scheduled")
    decision_id: str
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)
    updated_at: Optional[datetime] = None


class MarketingOutcome(KanyozaModel):
    id: str
    workspace_id: str
    campaign_id: str
    candidate_id: Optional[str] = None
    metric: str
    value: float
    observed_at: datetime = Field(default_factory=KanyozaModel.utcnow)
    source: str = "analytics"
    evidence: dict = Field(default_factory=dict)
