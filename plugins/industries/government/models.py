"""Government / Public Services domain models."""
from datetime import datetime
from typing import Optional
from pydantic import Field
from models.base import KanyozaModel


class PublicService(KanyozaModel):
    id: str
    workspace_id: str
    name: str
    category: str
    status: str = "active"
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class ServiceRequest(KanyozaModel):
    id: str
    workspace_id: str
    service_id: str
    title: str
    priority: str = "medium"
    status: str = "submitted"
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)
    updated_at: Optional[datetime] = None


class PermitCase(KanyozaModel):
    id: str
    workspace_id: str
    service_id: str
    applicant_ref: str
    permit_type: str
    status: str = "submitted"
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)
    updated_at: Optional[datetime] = None


class PublicCase(KanyozaModel):
    id: str
    workspace_id: str
    service_id: str
    title: str
    status: str = "open"
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class ServiceResolution(KanyozaModel):
    id: str
    workspace_id: str
    request_id: str
    status: str = "recorded"
    resolution: str = ""
    evidence_ref: str = ""
    resolved_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class PublicServiceOutcome(KanyozaModel):
    id: str
    workspace_id: str
    reference_type: str
    reference_id: str
    outcome_type: str
    status: str = "recorded"
    metric: str = ""
    value: Optional[float] = None
    evidence_ref: str = ""
    recorded_at: datetime = Field(default_factory=KanyozaModel.utcnow)
