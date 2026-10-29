from datetime import datetime
from typing import Optional
from pydantic import Field
from models.base import KanyozaModel

class CommerceOrderNotificationDelivery(KanyozaModel):
    id: str
    workspace_id: str
    order_id: str
    customer_id: str
    to_number: str
    body: str
    idempotency_key: str
    provider: str = "twilio"
    provider_message_sid: Optional[str] = None
    provider_status: Optional[str] = None
    status: str = "pending"
    reconciliation_required: bool = True
    evidence: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)
    updated_at: Optional[datetime] = None

class CommerceOrderNotificationReconciliationEvidence(KanyozaModel):
    id: str
    workspace_id: str
    delivery_id: str
    provider: str
    provider_reference: str
    attempt_number: int
    provider_status: str
    canonical_status: str
    terminal: bool = False
    verification_state: str = "pending"
    observed: dict = Field(default_factory=dict)
    evidence: dict = Field(default_factory=dict)
    observed_at: datetime = Field(default_factory=KanyozaModel.utcnow)
