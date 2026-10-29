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

class FieldServiceProviderBinding(KanyozaModel):
    id: str
    workspace_id: str
    provider: str = "jira"
    status: str = "active"
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class FieldServiceDispatch(KanyozaModel):
    id: str
    workspace_id: str
    request_id: str
    team_id: str
    provider: str = "jira"
    project_key: str
    issue_type: str
    status: str = "requested"
    provider_request_id: Optional[str] = None
    provider_issue_key: Optional[str] = None
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class FieldServiceReconciliationEvidence(KanyozaModel):
    id: str
    workspace_id: str
    dispatch_id: str
    attempt_number: int
    status: str
    terminal: bool = False
    observed: dict = Field(default_factory=dict)
    verification_state: str = "pending"
    evidence: dict = Field(default_factory=dict)
    observed_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class FieldServiceOutcome(KanyozaModel):
    id: str
    workspace_id: str
    dispatch_id: str
    status: str = "recorded"
    evidence_ref: str = ""
    recorded_at: datetime = Field(default_factory=KanyozaModel.utcnow)
