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
