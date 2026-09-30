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
