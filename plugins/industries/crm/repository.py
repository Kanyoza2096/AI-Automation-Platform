"""CRM repository — Supabase + in-memory fallback."""

import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from .models import Contact, Deal, FollowUp, Meeting

logger = logging.getLogger("kanyoza.plugins.crm")


class CRMRepository:
    """Data access for CRM plugin."""

    def __init__(self, supabase_client=None):
        self.supabase = supabase_client
        self._in_memory: dict = {}

    # ── Contacts ─────────────────────────────────────────────────────────

    def add_contact(self, workspace_id: str, data: dict) -> Contact:
        contact = Contact(
            id=f"ctc-{uuid.uuid4().hex[:12]}",
            workspace_id=workspace_id,
            contact_name=data.get("contact_name", ""),
            phone=data.get("phone", ""),
            email=data.get("email"),
            company=data.get("company", ""),
            status=data.get("status", "lead"),
            source=data.get("source"),
            notes=data.get("notes"),
        )
        if self.supabase:
            try:
                self.supabase.table("crm_contacts").insert(contact.model_dump(mode="json")).execute()
            except Exception as e:
                logger.error("Supabase contact insert failed: %s", e)
        self._in_memory[contact.id] = contact
        return contact

    def find_contact_by_name(self, workspace_id: str, name: str) -> Optional[Contact]:
        name_lower = name.lower()
        if self.supabase:
            try:
                res = self.supabase.table("crm_contacts").select("*").eq("workspace_id", workspace_id).ilike("contact_name", f"%{name}%").limit(1).execute()
                if res.data:
                    return Contact(**res.data[0])
            except Exception as e:
                logger.error("Supabase contact lookup failed: %s", e)
        for c in self._in_memory.values():
            if isinstance(c, Contact) and name_lower in c.contact_name.lower():
                return c
        return None

    def find_contacts_by_status(self, workspace_id: str, status: str) -> List[Contact]:
        if self.supabase:
            try:
                res = self.supabase.table("crm_contacts").select("*").eq("workspace_id", workspace_id).eq("status", status).execute()
                return [Contact(**row) for row in (res.data or [])]
            except Exception as e:
                logger.error("Supabase status lookup failed: %s", e)
        return [c for c in self._in_memory.values() if isinstance(c, Contact) and c.workspace_id == workspace_id and c.status == status]

    # ── Deals ────────────────────────────────────────────────────────────

    def add_deal(self, workspace_id: str, data: dict) -> Deal:
        deal = Deal(
            id=f"deal-{uuid.uuid4().hex[:12]}",
            workspace_id=workspace_id,
            contact_id=data.get("contact_id", ""),
            contact_name=data.get("contact_name", ""),
            amount=float(data.get("amount", 0)),
            stage=data.get("stage", "lead"),
            deal_date=data.get("deal_date", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
            probability=data.get("probability", 20),
        )
        if self.supabase:
            try:
                self.supabase.table("crm_deals").insert(deal.model_dump(mode="json")).execute()
            except Exception as e:
                logger.error("Supabase deal insert failed: %s", e)
        self._in_memory[deal.id] = deal
        return deal

    # ── Follow-ups ───────────────────────────────────────────────────────

    def add_followup(self, workspace_id: str, data: dict) -> FollowUp:
        followup = FollowUp(
            id=f"flw-{uuid.uuid4().hex[:12]}",
            workspace_id=workspace_id,
            contact_id=data.get("contact_id", ""),
            contact_name=data.get("contact_name", ""),
            followup_date=data.get("followup_date", ""),
            notes=data.get("notes", ""),
            status=data.get("status", "scheduled"),
        )
        if self.supabase:
            try:
                self.supabase.table("crm_followups").insert(followup.model_dump(mode="json")).execute()
            except Exception as e:
                logger.error("Supabase followup insert failed: %s", e)
        self._in_memory[followup.id] = followup
        return followup

    # ── Meetings ─────────────────────────────────────────────────────────

    def add_meeting(self, workspace_id: str, data: dict) -> Meeting:
        meeting = Meeting(
            id=f"mtg-{uuid.uuid4().hex[:12]}",
            workspace_id=workspace_id,
            title=data.get("title", ""),
            description=data.get("description"),
            meeting_date=data.get("meeting_date", ""),
            start_time=data.get("start_time"),
            location=data.get("location", "Virtual"),
        )
        if self.supabase:
            try:
                self.supabase.table("crm_meetings").insert(meeting.model_dump(mode="json")).execute()
            except Exception as e:
                logger.error("Supabase meeting insert failed: %s", e)
        self._in_memory[meeting.id] = meeting
        return meeting
