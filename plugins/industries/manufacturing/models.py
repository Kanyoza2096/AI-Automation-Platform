"""Manufacturing / Industrial Operations completion-domain models."""
from datetime import datetime
from typing import Optional
from pydantic import Field
from models.base import KanyozaModel


class WorkCenter(KanyozaModel):
    id: str
    workspace_id: str
    name: str
    location: str = ""
    status: str = "available"
    capacity_units: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class ProductionOrder(KanyozaModel):
    id: str
    workspace_id: str
    reference: str
    product_sku: str
    quantity: int = Field(gt=0)
    work_center_id: Optional[str] = None
    status: str = "planned"
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)
    updated_at: Optional[datetime] = None


class QualityInspection(KanyozaModel):
    id: str
    workspace_id: str
    production_order_id: str
    result: str = "pending"
    defect_count: int = Field(default=0, ge=0)
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class MaintenanceWorkOrder(KanyozaModel):
    id: str
    workspace_id: str
    work_center_id: str
    description: str
    priority: str = "medium"
    status: str = "requested"
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)
    updated_at: Optional[datetime] = None


class MaterialConsumption(KanyozaModel):
    id: str
    workspace_id: str
    production_order_id: str
    material_sku: str
    quantity: float = Field(gt=0)
    unit: str = "unit"
    status: str = "recorded"
    evidence_ref: str = ""
    recorded_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class ManufacturingOutcome(KanyozaModel):
    id: str
    workspace_id: str
    production_order_id: str
    metric: str
    value: float = Field(ge=0)
    planned_quantity: Optional[float] = Field(default=None, ge=0)
    produced_quantity: Optional[float] = Field(default=None, ge=0)
    scrap_quantity: Optional[float] = Field(default=None, ge=0)
    status: str = "recorded"
    evidence_ref: str = ""
    recorded_at: datetime = Field(default_factory=KanyozaModel.utcnow)
