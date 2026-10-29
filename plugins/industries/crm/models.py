"""CRM data models."""

from datetime import datetime
from typing import Optional
from pydantic import Field
from models.base import KanyozaModel


class Contact(KanyozaModel):
    """A CRM contact or lead."""
    id: str
    workspace_id: str
    contact_name: str
    phone: str = Field(default="")
    email: Optional[str] = Field(default=None)
    company: Optional[str] = Field(default=None)
    status: str = Field(default="lead")
    source: Optional[str] = Field(default=None)
    notes: Optional[str] = Field(default=None)
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)
    updated_at: Optional[datetime] = Field(default=None)


class Deal(KanyozaModel):
    """Sales deal or opportunity."""
    id: str
    workspace_id: str
    contact_id: str = Field(default="")
    contact_name: str = Field(default="")
    amount: float = Field(default=0)
    stage: str = Field(default="lead")
    deal_date: str = Field(default="")
    expected_close_date: Optional[str] = Field(default=None)
    probability: int = Field(default=20)
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class FollowUp(KanyozaModel):
    """Scheduled follow-up activity."""
    id: str
    workspace_id: str
    contact_id: str = Field(default="")
    contact_name: str = Field(default="")
    followup_date: str
    notes: str = Field(default="")
    status: str = Field(default="scheduled")
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class Meeting(KanyozaModel):
    """Meeting or call."""
    id: str
    workspace_id: str
    title: str
    description: Optional[str] = Field(default=None)
    meeting_date: str
    start_time: Optional[str] = Field(default=None)
    location: str = Field(default="Virtual")
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class CRMActivity(KanyozaModel):
    """Unified customer touchpoint/activity for the completion loop."""
    id: str
    workspace_id: str
    contact_id: Optional[str] = None
    deal_id: Optional[str] = None
    activity_type: str
    status: str = "completed"
    subject: str = ""
    notes: str = ""
    occurred_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class CustomerOutcome(KanyozaModel):
    """Observed CRM/customer outcome with evidence."""
    id: str
    workspace_id: str
    contact_id: Optional[str] = None
    deal_id: Optional[str] = None
    metric: str
    value: float
    source: str = "crm"
    evidence: dict = Field(default_factory=dict)
    observed_at: datetime = Field(default_factory=KanyozaModel.utcnow)

class CRMMessageDelivery(KanyozaModel):
    """Durable outbound CRM message/provider lifecycle record."""
    id: str
    workspace_id: str
    contact_id: str
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


class CRMMessageReconciliationEvidence(KanyozaModel):
    """Durable provider observations for an outbound CRM message."""
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
