"""Agriculture / Farm Operations domain models."""
from datetime import datetime
from typing import Optional
from pydantic import Field as PydanticField
from models.base import KanyozaModel


class Farm(KanyozaModel):
    id: str
    workspace_id: str
    name: str
    farm_type: str
    status: str = "active"
    created_at: datetime = PydanticField(default_factory=KanyozaModel.utcnow)


class Field(KanyozaModel):
    id: str
    workspace_id: str
    farm_id: str
    name: str
    area: float
    area_unit: str = "hectare"
    status: str = "available"
    created_at: datetime = PydanticField(default_factory=KanyozaModel.utcnow)


class CropCycle(KanyozaModel):
    id: str
    workspace_id: str
    field_id: str
    crop: str
    season: str
    status: str = "planned"
    planted_at: Optional[datetime] = None
    expected_harvest_at: Optional[datetime] = None
    created_at: datetime = PydanticField(default_factory=KanyozaModel.utcnow)
    updated_at: Optional[datetime] = None


class FarmActivity(KanyozaModel):
    id: str
    workspace_id: str
    field_id: str
    activity_type: str
    notes: str = ""
    performed_at: datetime = PydanticField(default_factory=KanyozaModel.utcnow)


class Harvest(KanyozaModel):
    id: str
    workspace_id: str
    crop_cycle_id: str
    quantity: float
    unit: str
    quality_grade: str = "standard"
    recorded_at: datetime = PydanticField(default_factory=KanyozaModel.utcnow)


class FieldException(KanyozaModel):
    id: str
    workspace_id: str
    field_id: str
    exception_type: str
    status: str = "open"
    severity: str = "medium"
    resolution: str = ""
    evidence_ref: str = ""
    created_at: datetime = PydanticField(default_factory=KanyozaModel.utcnow)


class AgriculturalOutcome(KanyozaModel):
    id: str
    workspace_id: str
    reference_type: str
    reference_id: str
    outcome_type: str
    status: str = "recorded"
    metric: str = ""
    value: Optional[float] = None
    unit: str = ""
    evidence_ref: str = ""
    recorded_at: datetime = PydanticField(default_factory=KanyozaModel.utcnow)
