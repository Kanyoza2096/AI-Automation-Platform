"""Education domain models and completion-loop records."""
from datetime import datetime
from typing import Optional
from pydantic import Field
from models.base import KanyozaModel


class Student(KanyozaModel):
    id: str
    workspace_id: str
    full_name: str
    grade: str = ""
    guardian_name: Optional[str] = None
    guardian_phone: str = ""
    status: str = "active"
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)
    updated_at: Optional[datetime] = None


class Attendance(KanyozaModel):
    id: str
    workspace_id: str
    student_id: str
    date: str
    status: str = "present"
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class Assessment(KanyozaModel):
    id: str
    workspace_id: str
    student_id: str
    subject: str
    score: float
    max_score: float = 100.0
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class FeeRecord(KanyozaModel):
    id: str
    workspace_id: str
    student_id: str
    amount: float
    currency: str = "MWK"
    status: str = "recorded"
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class EducationIntervention(KanyozaModel):
    """Observed or planned intervention for a learner."""
    id: str
    workspace_id: str
    student_id: str
    intervention_type: str
    reason: str = ""
    status: str = "planned"
    owner: Optional[str] = None
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)
    updated_at: Optional[datetime] = None


class EducationOutcome(KanyozaModel):
    """Measured education outcome with supporting evidence."""
    id: str
    workspace_id: str
    student_id: Optional[str] = None
    metric: str
    value: float
    source: str = "education"
    evidence: dict = Field(default_factory=dict)
    observed_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class EducationParentMessageDelivery(KanyozaModel):
    """Durable outbound parent-message/provider lifecycle record."""
    id: str
    workspace_id: str
    student_id: str
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


class EducationParentMessageReconciliationEvidence(KanyozaModel):
    id: str
    workspace_id: str
    delivery_id: str
    provider: str = "twilio"
    provider_reference: str = ""
    attempt_number: int
    provider_status: str
    canonical_status: str
    terminal: bool = False
    verification_state: str = "provider_observed_nonterminal"
    observed: dict = Field(default_factory=dict)
    evidence: dict = Field(default_factory=dict)
    observed_at: datetime = Field(default_factory=KanyozaModel.utcnow)
