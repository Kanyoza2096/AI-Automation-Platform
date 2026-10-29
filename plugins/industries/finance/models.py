"""Finance operations domain models.

Administrative finance operations only. External money movement remains
connector-deferred and must execute through the platform Action Kernel.
"""
from datetime import datetime
from typing import Optional
from pydantic import Field
from models.base import KanyozaModel


class Account(KanyozaModel):
    id: str
    workspace_id: str
    name: str
    account_type: str = "operating"
    currency: str = Field(default="MWK", min_length=3, max_length=3)
    status: str = "active"
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class Transaction(KanyozaModel):
    id: str
    workspace_id: str
    account_id: str
    amount: float = Field(gt=0)
    direction: str
    reference: str = ""
    status: str = "recorded"
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class Invoice(KanyozaModel):
    id: str
    workspace_id: str
    customer_ref: str
    amount: float = Field(gt=0)
    currency: str = Field(default="MWK", min_length=3, max_length=3)
    status: str = "issued"
    due_date: Optional[str] = None
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class Reconciliation(KanyozaModel):
    id: str
    workspace_id: str
    account_id: str
    period: str
    status: str = "open"
    expected_amount: float = Field(ge=0)
    observed_amount: float = Field(ge=0)
    variance: float = 0
    evidence_ref: str = ""
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class Payment(KanyozaModel):
    id: str
    workspace_id: str
    amount: float = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    reference: str
    idempotency_key: str
    provider: str = "stripe"
    provider_payment_intent_id: Optional[str] = None
    status: str = "uncertain"
    provider_status: Optional[str] = None
    reconciliation_required: bool = True
    evidence_ref: str = ""
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)
    updated_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class PaymentReconciliationEvidence(KanyozaModel):
    id: str
    workspace_id: str
    payment_id: str
    provider: str
    provider_reference: str
    attempt_number: int = Field(gt=0)
    provider_status: str
    canonical_status: str
    terminal: bool = False
    verification_state: str
    observed: dict = Field(default_factory=dict)
    evidence: dict = Field(default_factory=dict)
    recorded_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class FinancialOutcome(KanyozaModel):
    id: str
    workspace_id: str
    reference_type: str
    reference_id: str
    outcome_type: str
    status: str = "recorded"
    amount: Optional[float] = Field(default=None, ge=0)
    evidence_ref: str = ""
    recorded_at: datetime = Field(default_factory=KanyozaModel.utcnow)

class StatementDelivery(KanyozaModel):
    id: str
    workspace_id: str
    account_id: str
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

class StatementReconciliationEvidence(KanyozaModel):
    id: str
    workspace_id: str
    statement_delivery_id: str
    provider: str = "twilio"
    provider_reference: str = ""
    attempt_number: int = Field(gt=0)
    provider_status: str
    canonical_status: str
    terminal: bool = False
    verification_state: str
    observed: dict = Field(default_factory=dict)
    evidence: dict = Field(default_factory=dict)
    recorded_at: datetime = Field(default_factory=KanyozaModel.utcnow)
