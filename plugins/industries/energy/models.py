"""Energy & Utilities domain models."""
from datetime import datetime
from typing import Optional
from pydantic import Field
from models.base import KanyozaModel


class UtilityAsset(KanyozaModel):
    id: str
    workspace_id: str
    name: str
    asset_type: str
    location: str = ""
    status: str = "operational"
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class MeterReading(KanyozaModel):
    id: str
    workspace_id: str
    asset_id: str
    metric: str
    value: float
    unit: str
    recorded_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class UtilityIncident(KanyozaModel):
    id: str
    workspace_id: str
    asset_id: str
    title: str
    severity: str = "medium"
    status: str = "open"
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)
    updated_at: Optional[datetime] = None


class UtilityWorkOrder(KanyozaModel):
    id: str
    workspace_id: str
    asset_id: str
    description: str
    priority: str = "medium"
    status: str = "requested"
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class CrewDispatchProviderBinding(KanyozaModel):
    id: str
    workspace_id: str
    provider: str = "jira"
    status: str = "active"
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class CrewDispatch(KanyozaModel):
    id: str
    workspace_id: str
    work_order_id: str
    crew_id: str
    provider: str = "jira"
    project_key: str
    issue_type: str
    status: str = "requested"
    provider_request_id: Optional[str] = None
    provider_issue_key: Optional[str] = None
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class CrewDispatchReconciliationEvidence(KanyozaModel):
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


class CrewDispatchOutcome(KanyozaModel):
    id: str
    workspace_id: str
    dispatch_id: str
    status: str = "recorded"
    evidence_ref: str = ""
    recorded_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class UtilityException(KanyozaModel):
    id: str
    workspace_id: str
    asset_id: str
    exception_type: str
    status: str = "open"
    severity: str = "medium"
    resolution: str = ""
    evidence_ref: str = ""
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class UtilityOutcome(KanyozaModel):
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
