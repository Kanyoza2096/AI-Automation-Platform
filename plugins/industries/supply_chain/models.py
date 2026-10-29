"""Supply Chain / Logistics domain models."""
from datetime import datetime
from typing import Optional
from pydantic import Field
from models.base import KanyozaModel


class Shipment(KanyozaModel):
    id: str
    workspace_id: str
    reference: str
    origin: str
    destination: str
    carrier: Optional[str] = None
    status: str = "planned"
    tracking_reference: Optional[str] = None
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)
    updated_at: Optional[datetime] = None


class WarehouseItem(KanyozaModel):
    id: str
    workspace_id: str
    sku: str
    warehouse: str
    quantity: int = Field(default=0, ge=0)
    reorder_point: int = Field(default=0, ge=0)
    status: str = "available"
    updated_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class PurchaseOrder(KanyozaModel):
    id: str
    workspace_id: str
    supplier_ref: str
    reference: str
    total_amount: float = Field(gt=0)
    currency: str = Field(default="MWK", min_length=3, max_length=3)
    status: str = "draft"
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)
    updated_at: Optional[datetime] = None


class ShipmentException(KanyozaModel):
    id: str
    workspace_id: str
    shipment_id: str
    exception_type: str
    status: str = "open"
    severity: str = "medium"
    resolution: str = ""
    evidence_ref: str = ""
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class LogisticsOutcome(KanyozaModel):
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
