"""
Church MIS — Pydantic v2 domain models.

All request/response schemas for the Church Management Information System.
Follows the same pattern as products/converter/models.py.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


# =========================================================================
# Member
# =========================================================================

class MemberCreate(BaseModel):
    """Payload to register a new church member."""
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    email: Optional[str] = None
    phone: Optional[str] = None
    gender: Optional[str] = Field(None, pattern=r"^(male|female|other)$")
    date_of_birth: Optional[date] = None
    address: Optional[str] = None
    membership_date: Optional[date] = None
    notes: Optional[str] = None


class MemberUpdate(BaseModel):
    """Partial update for an existing member — all fields optional."""
    first_name: Optional[str] = Field(None, min_length=1, max_length=100)
    last_name: Optional[str] = Field(None, min_length=1, max_length=100)
    email: Optional[str] = None
    phone: Optional[str] = None
    gender: Optional[str] = Field(None, pattern=r"^(male|female|other)$")
    date_of_birth: Optional[date] = None
    address: Optional[str] = None
    membership_date: Optional[date] = None
    status: Optional[str] = Field(None, pattern=r"^(active|inactive|deceased|transferred)$")
    notes: Optional[str] = None


class MemberResponse(BaseModel):
    """Member as returned by the API."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    first_name: str
    last_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    gender: Optional[str] = None
    date_of_birth: Optional[date] = None
    address: Optional[str] = None
    membership_date: Optional[date] = None
    status: str = "active"
    notes: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# =========================================================================
# Group / Ministry
# =========================================================================

class GroupCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    description: Optional[str] = None
    group_type: str = Field(
        "ministry",
        pattern=r"^(ministry|department|cell_group|committee|choir|other)$",
    )
    leader_id: Optional[int] = None


class GroupUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=150)
    description: Optional[str] = None
    group_type: Optional[str] = Field(
        None,
        pattern=r"^(ministry|department|cell_group|committee|choir|other)$",
    )
    leader_id: Optional[int] = None
    is_active: Optional[bool] = None


class GroupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: Optional[str] = None
    group_type: str
    leader_id: Optional[int] = None
    is_active: bool = True
    created_at: Optional[datetime] = None


class GroupMemberAdd(BaseModel):
    member_id: int
    role: str = Field(
        "member",
        pattern=r"^(member|leader|assistant_leader|secretary|treasurer)$",
    )


# =========================================================================
# Attendance
# =========================================================================

class AttendanceCreate(BaseModel):
    service_date: date
    service_type: str = Field(
        "sunday_service",
        pattern=r"^(sunday_service|midweek_service|prayer_meeting|bible_study|youth_service|special_event|other)$",
    )
    member_id: Optional[int] = None
    head_count: Optional[int] = Field(None, ge=0)
    notes: Optional[str] = None
    recorded_by: Optional[str] = None


class AttendanceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    service_date: date
    service_type: str
    member_id: Optional[int] = None
    head_count: Optional[int] = None
    notes: Optional[str] = None
    recorded_by: Optional[str] = None
    created_at: Optional[datetime] = None


# =========================================================================
# Tithes & Offerings
# =========================================================================

class TitheCreate(BaseModel):
    member_id: Optional[int] = None
    amount: Decimal = Field(..., gt=0)
    currency: str = Field("MWK", max_length=5)
    contribution_type: str = Field(
        "tithe",
        pattern=r"^(tithe|offering|special_offering|building_fund|missions|donation|other)$",
    )
    payment_method: str = Field(
        "cash",
        pattern=r"^(cash|mobile_money|bank_transfer|cheque|other)$",
    )
    reference: Optional[str] = None
    contribution_date: Optional[date] = None
    notes: Optional[str] = None
    recorded_by: Optional[str] = None


class TitheResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    member_id: Optional[int] = None
    amount: Decimal
    currency: str
    contribution_type: str
    payment_method: str
    reference: Optional[str] = None
    contribution_date: Optional[date] = None
    notes: Optional[str] = None
    recorded_by: Optional[str] = None
    created_at: Optional[datetime] = None


class TitheSummary(BaseModel):
    """Aggregated giving summary for a period."""
    total_amount: Decimal = Decimal("0")
    currency: str = "MWK"
    contribution_count: int = 0
    by_type: dict = {}
    period_start: Optional[date] = None
    period_end: Optional[date] = None


# =========================================================================
# Events
# =========================================================================

class EventCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    event_type: str = Field(
        "service",
        pattern=r"^(service|wedding|funeral|conference|retreat|outreach|meeting|celebration|other)$",
    )
    start_date: datetime
    end_date: Optional[datetime] = None
    location: Optional[str] = None
    organizer: Optional[str] = None


class EventUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    event_type: Optional[str] = Field(
        None,
        pattern=r"^(service|wedding|funeral|conference|retreat|outreach|meeting|celebration|other)$",
    )
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    location: Optional[str] = None
    organizer: Optional[str] = None
    status: Optional[str] = Field(None, pattern=r"^(scheduled|ongoing|completed|cancelled)$")


class EventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: Optional[str] = None
    event_type: str
    start_date: datetime
    end_date: Optional[datetime] = None
    location: Optional[str] = None
    organizer: Optional[str] = None
    status: str = "scheduled"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# =========================================================================
# Reports / Dashboard
# =========================================================================

class ChurchDashboard(BaseModel):
    """Combined overview stats returned by /reports/dashboard."""
    total_members: int = 0
    active_members: int = 0
    total_groups: int = 0
    total_events: int = 0
    upcoming_events: int = 0
    total_giving: Decimal = Decimal("0")
    giving_this_month: Decimal = Decimal("0")
    avg_attendance: float = 0.0
    currency: str = "MWK"


class PaginatedResponse(BaseModel):
    """Wraps any list endpoint with pagination metadata."""
    data: List[dict] = []
    total: int = 0
    page: int = 1
    per_page: int = 25
    pages: int = 0
