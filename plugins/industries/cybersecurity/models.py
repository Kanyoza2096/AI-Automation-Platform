"""Cybersecurity Operations completion-domain models."""
from datetime import datetime
from typing import Optional
from pydantic import Field
from models.base import KanyozaModel


class Asset(KanyozaModel):
    id: str
    workspace_id: str
    name: str
    asset_type: str = "service"
    environment: str = "production"
    criticality: str = "medium"
    status: str = "active"
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class SecurityFinding(KanyozaModel):
    id: str
    workspace_id: str
    asset_id: str
    title: str
    severity: str = "medium"
    status: str = "open"
    source: str = "manual"
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)
    updated_at: Optional[datetime] = None


class SecurityAlert(KanyozaModel):
    id: str
    workspace_id: str
    asset_id: Optional[str] = None
    title: str
    severity: str = "medium"
    status: str = "open"
    source: str = "monitoring"
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)
    updated_at: Optional[datetime] = None


class SecurityIncident(KanyozaModel):
    id: str
    workspace_id: str
    title: str
    severity: str = "medium"
    status: str = "open"
    source: str = "security"
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)
    updated_at: Optional[datetime] = None


class SecurityResponseOutcome(KanyozaModel):
    id: str
    workspace_id: str
    action_type: str
    target_id: str
    status: str
    provider_reference: Optional[str] = None
    evidence: dict = Field(default_factory=dict)
    observed_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class SecurityOutcome(KanyozaModel):
    id: str
    workspace_id: str
    resource_type: str
    resource_id: Optional[str] = None
    metric: str
    value: float
    source: str = "security"
    evidence: dict = Field(default_factory=dict)
    observed_at: datetime = Field(default_factory=KanyozaModel.utcnow)
