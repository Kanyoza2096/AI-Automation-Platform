"""IT Operations domain models."""
from datetime import datetime
from typing import Optional
from pydantic import Field
from models.base import KanyozaModel

class Service(KanyozaModel):
    id: str
    workspace_id: str
    name: str
    environment: str = "production"
    status: str = "healthy"
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)

class Incident(KanyozaModel):
    id: str
    workspace_id: str
    service_id: str
    title: str
    severity: str = "medium"
    status: str = "open"
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)
    updated_at: Optional[datetime] = None

class Deployment(KanyozaModel):
    id: str
    workspace_id: str
    service_id: str
    version: str
    environment: str = "production"
    status: str = "requested"
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)

class IncidentUpdate(KanyozaModel):
    id: str
    workspace_id: str
    incident_id: str
    status: str
    note: str = ""
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)

class DeploymentOutcome(KanyozaModel):
    id: str
    workspace_id: str
    deployment_id: str
    status: str
    provider_reference: Optional[str] = None
    evidence: dict = Field(default_factory=dict)
    observed_at: datetime = Field(default_factory=KanyozaModel.utcnow)

class ServiceOutcome(KanyozaModel):
    id: str
    workspace_id: str
    service_id: str
    metric: str
    value: float
    source: str
    evidence: dict = Field(default_factory=dict)
    observed_at: datetime = Field(default_factory=KanyozaModel.utcnow)
