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

class ServiceProviderBinding(KanyozaModel):
    id: str
    workspace_id: str
    service_id: str
    provider: str
    provider_service_id: str
    environment: str = "production"
    status: str = "active"
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class DeploymentVersionReference(KanyozaModel):
    id: str
    workspace_id: str
    service_id: str
    version: str
    provider: str
    provider_version_ref: str
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)


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


class DeploymentReconciliationEvidence(KanyozaModel):
    id: str
    workspace_id: str
    deployment_id: str
    service_id: str
    provider: str
    provider_service_id: str
    provider_reference: str
    attempt_number: int
    status: str
    terminal: bool = False
    observed: dict = Field(default_factory=dict)
    verification_state: str = "pending"
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


class ServiceRemediation(KanyozaModel):
    id: str
    workspace_id: str
    service_id: str
    provider: str = "github"
    owner: str
    repo: str
    workflow_id: str
    ref: str
    reason: str
    status: str = "requested"
    provider_request_id: Optional[str] = None
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class ServiceRemediationReconciliationEvidence(KanyozaModel):
    id: str
    workspace_id: str
    remediation_id: str
    service_id: str
    provider: str
    owner: str
    repo: str
    workflow_id: str
    ref: str
    attempt_number: int
    status: str
    terminal: bool = False
    observed: dict = Field(default_factory=dict)
    verification_state: str = "pending"
    evidence: dict = Field(default_factory=dict)
    observed_at: datetime = Field(default_factory=KanyozaModel.utcnow)
