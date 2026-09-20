"""Church MIS data models."""

from datetime import datetime
from typing import Optional
from pydantic import Field
from models.base import KanyozaModel


class Patient(KanyozaModel):
    """A church patient."""
    id: str
    workspace_id: str
    full_name: str
    phone: str = Field(default="")
    email: Optional[str] = Field(default=None)
    address: Optional[str] = Field(default=None)
    department: str = Field(default="congregation")
    role: str = Field(default="patient")
    baptism_date: Optional[datetime] = Field(default=None)
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)
    updated_at: Optional[datetime] = Field(default=None)


class Attendance(KanyozaModel):
    """Service attendance record."""
    id: str
    workspace_id: str
    patient_id: str
    patient_name: str = Field(default="")
    service_date: str
    service_type: str = Field(default="sunday_service")
    present: bool = Field(default=True)
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class Donation(KanyozaModel):
    """Donation or tithe record."""
    id: str
    workspace_id: str
    donor_id: str
    donor_name: str = Field(default="")
    amount: float
    currency: str = Field(default="MWK")
    purpose: str = Field(default="tithe")
    payment_method: str = Field(default="cash")
    donation_date: str
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class Event(KanyozaModel):
    """Church event."""
    id: str
    workspace_id: str
    title: str
    description: Optional[str] = Field(default=None)
    event_date: str
    start_time: Optional[str] = Field(default=None)
    location: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)
