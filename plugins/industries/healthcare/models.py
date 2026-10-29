"""Administrative healthcare domain models; clinical decisions remain outside this pack."""
from datetime import datetime
from typing import Optional
from pydantic import Field
from models.base import KanyozaModel


class Patient(KanyozaModel):
    id: str
    workspace_id: str
    full_name: str
    phone: str = ""
    external_ref: Optional[str] = None
    status: str = "active"
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class Appointment(KanyozaModel):
    id: str
    workspace_id: str
    patient_id: str
    scheduled_for: str
    department: str = "general"
    status: str = "scheduled"
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class Encounter(KanyozaModel):
    id: str
    workspace_id: str
    patient_id: str
    appointment_id: Optional[str] = None
    status: str = "open"
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class HealthcareCarePlan(KanyozaModel):
    """Administrative coordination plan; clinical decisions remain outside this pack."""
    id: str
    workspace_id: str
    patient_id: str
    plan_type: str = "follow_up"
    status: str = "planned"
    owner: Optional[str] = None
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)
    updated_at: Optional[datetime] = None


class HealthcareOutcome(KanyozaModel):
    """Measured administrative/operational healthcare outcome with evidence."""
    id: str
    workspace_id: str
    patient_id: Optional[str] = None
    metric: str
    value: float
    source: str = "healthcare"
    evidence: dict = Field(default_factory=dict)
    observed_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class Invoice(KanyozaModel):
    id: str
    workspace_id: str
    patient_id: str
    amount: float
    currency: str = "MWK"
    status: str = "open"
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class HealthcarePatientMessageDelivery(KanyozaModel):
    """Durable outbound patient-message/provider lifecycle record."""
    id: str
    workspace_id: str
    patient_id: str
    to_number: str
    body: str
    idempotency_key: str
    provider: str = "twilio"
    provider_message_sid: Optional[str] = None
    provider_status: str = "unknown"
    status: str = "pending"
    reconciliation_required: bool = True
    evidence: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)
    updated_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class HealthcarePatientMessageReconciliationEvidence(KanyozaModel):
    id: str
    workspace_id: str
    delivery_id: str
    provider: str = "twilio"
    provider_reference: str = ""
    attempt_number: int
    provider_status: str
    canonical_status: str
    terminal: bool = False
    verification_state: str
    observed: dict = Field(default_factory=dict)
    evidence: dict = Field(default_factory=dict)
    observed_at: datetime = Field(default_factory=KanyozaModel.utcnow)
