"""CRM — natural language command handlers."""

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("kanyoza.plugins.crm.commands")


class CRMCommands:
    """Handles NL commands for customer relationship management."""

    def __init__(self, repository, redis_client=None):
        self.repo = repository
        self.redis = redis_client

    def list_all(self) -> list[dict]:
        """Return NL commands with an explicit execution classification.

        Consequential commands must name their Action Kernel contract. Commands
        without that declaration are intentionally not executable through the
        generic AI command surface.
        """
        return [
            {
                "intent": "add_contact",
                "description": "Add a new contact or lead",
                "params": ["name", "phone"],
                "example": "Add contact Jane Doe phone 0888123456 company Acme Corp",
                "category": "contacts",
                "execution_mode": "action_kernel",
                "governed_action_type": "crm_add_contact",
            },
            {
                "intent": "get_contacts",
                "description": "List contacts by status or all contacts",
                "params": ["status"],
                "example": "Show all leads",
                "category": "contacts",
                "execution_mode": "read_only",
            },
            {
                "intent": "add_deal",
                "description": "Record a sales deal or opportunity",
                "params": ["amount", "contact", "stage"],
                "example": "Add deal 500000 from Jane Doe stage negotiation",
                "category": "deals",
                "execution_mode": "action_kernel",
                "governed_action_type": "crm_add_deal",
            },
            {
                "intent": "schedule_followup",
                "description": "Schedule a follow-up with a contact",
                "params": ["contact", "date", "notes"],
                "example": "Schedule follow-up with Jane Doe on 2026-07-20 about proposal",
                "category": "activities",
                "execution_mode": "action_kernel",
                "governed_action_type": "crm_schedule_followup",
            },
            {
                "intent": "add_meeting",
                "description": "Create a meeting or call",
                "params": ["title", "date", "time", "location"],
                "example": "Add meeting Product Demo on 2026-07-20 at 14:00 location Zoom",
                "category": "activities",
                "execution_mode": "action_kernel",
                "governed_action_type": "crm_add_meeting",
            },
            {
                "intent": "send_message",
                "description": "Prepare a message to contacts; sending is handled by governed communication actions",
                "params": ["message", "status"],
                "example": "Prepare message 'New product launch' for all leads",
                "category": "communication",
                "execution_mode": "read_only",
            },
        ]

    def handle(self, command: str, params: dict, workspace_id: str = "default") -> dict:
        """Dispatch a command to the correct handler."""
        handlers = {
            "add_contact": self._add_contact,
            "get_contacts": self._get_contacts,
            "add_deal": self._add_deal,
            "schedule_followup": self._schedule_followup,
            "update_contact_status": self._update_contact_status,
            "update_deal_stage": self._update_deal_stage,
            "add_meeting": self._add_meeting,
            "send_message": self._send_message,
        }

        handler = handlers.get(command)
        if not handler:
            return {"ok": False, "error": f"Unknown command: {command}"}

        try:
            return handler(params, workspace_id)
        except Exception as e:
            logger.error("Command %s failed: %s", command, e)
            return {"ok": False, "error": str(e)[:200]}

    # ── Handlers ─────────────────────────────────────────────────────────

    def _add_contact(self, params: dict, workspace_id: str) -> dict:
        name = params.get("name", "").strip()
        if not name:
            return {"ok": False, "error": "Contact name is required"}

        contact = self.repo.add_contact(workspace_id, {
            "contact_name": name,
            "phone": params.get("phone", ""),
            "email": params.get("email"),
            "company": params.get("company", ""),
            "status": params.get("status", "lead"),
        })

        return {
            "ok": True,
            "message": f"✅ Contact '{contact.contact_name}' added successfully",
            "data": {"contact_id": contact.id, "name": contact.contact_name},
        }

    def _get_contacts(self, params: dict, workspace_id: str) -> dict:
        status = params.get("status", "").strip().lower()

        if status:
            contacts = self.repo.find_contacts_by_status(workspace_id, status)
        else:
            contacts = self.repo.find_contacts_by_status(workspace_id, "lead")

        if not contacts:
            return {
                "ok": True,
                "message": f"No contacts found{' with status ' + status if status else ''}.",
                "data": {"contacts": [], "count": 0},
            }

        contact_list = [{"id": c.id, "name": c.contact_name, "phone": c.phone, "company": c.company, "status": c.status} for c in contacts]

        return {
            "ok": True,
            "message": f"Found {len(contacts)} contact(s){' with status ' + status if status else ''}.",
            "data": {"contacts": contact_list, "count": len(contacts)},
        }

    def _add_deal(self, params: dict, workspace_id: str) -> dict:
        amount = params.get("amount", 0)
        contact_name = params.get("contact", "").strip()
        stage = params.get("stage", "lead")

        if not amount or float(amount) <= 0:
            return {"ok": False, "error": "Valid deal amount is required"}
        if not contact_name:
            return {"ok": False, "error": "Contact name is required"}

        contact = self.repo.find_contact_by_name(workspace_id, contact_name)
        contact_id = contact.id if contact else ""
        contact_name = contact.contact_name if contact else contact_name

        deal = self.repo.add_deal(workspace_id, {
            "contact_id": contact_id,
            "contact_name": contact_name,
            "amount": float(amount),
            "stage": stage,
            "deal_date": params.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
        })

        return {
            "ok": True,
            "message": f"✅ Deal of MWK {deal.amount:,.0f} recorded for {contact_name} ({stage})",
            "data": {"deal_id": deal.id, "amount": deal.amount},
        }

    def _update_contact_status(self, params: dict, workspace_id: str) -> dict:
        contact_id = str(params.get("contact_id") or "").strip()
        status = str(params.get("status") or "").strip().lower()
        if not contact_id or not status:
            return {"ok": False, "error": "contact_id and status are required"}
        contact = self.repo.update_contact_status(workspace_id, contact_id, status)
        if not contact:
            return {"ok": False, "error": "Contact not found"}
        return {"ok": True, "message": f"Contact status updated to {status}", "data": {"contact_id": contact.id, "name": contact.contact_name, "status": contact.status}}

    def _update_deal_stage(self, params: dict, workspace_id: str) -> dict:
        deal_id = str(params.get("deal_id") or "").strip()
        stage = str(params.get("stage") or "").strip().lower()
        if not deal_id or not stage:
            return {"ok": False, "error": "deal_id and stage are required"}
        deal = self.repo.update_deal_stage(workspace_id, deal_id, stage)
        if not deal:
            return {"ok": False, "error": "Deal not found"}
        return {"ok": True, "message": f"Deal stage updated to {stage}", "data": {"deal_id": deal.id, "contact_id": deal.contact_id, "contact": deal.contact_name, "stage": deal.stage, "amount": deal.amount}}

    def _schedule_followup(self, params: dict, workspace_id: str) -> dict:
        contact_name = params.get("contact", "").strip()
        date = params.get("date", "")
        notes = params.get("notes", "")

        if not contact_name:
            return {"ok": False, "error": "Contact name is required"}
        if not date:
            return {"ok": False, "error": "Follow-up date is required"}

        contact = self.repo.find_contact_by_name(workspace_id, contact_name)
        contact_id = contact.id if contact else ""
        contact_name = contact.contact_name if contact else contact_name

        followup = self.repo.add_followup(workspace_id, {
            "contact_id": contact_id,
            "contact_name": contact_name,
            "followup_date": date,
            "notes": notes,
            "status": "scheduled",
        })

        return {
            "ok": True,
            "message": f"📅 Follow-up scheduled with {contact_name} on {date}",
            "data": {"followup_id": followup.id, "contact": contact_name, "date": date},
        }

    def _add_meeting(self, params: dict, workspace_id: str) -> dict:
        title = params.get("title", "").strip()
        if not title:
            return {"ok": False, "error": "Meeting title is required"}

        meeting = self.repo.add_meeting(workspace_id, {
            "title": title,
            "description": params.get("description"),
            "meeting_date": params.get("date", ""),
            "start_time": params.get("time"),
            "location": params.get("location", "Virtual"),
        })

        return {
            "ok": True,
            "message": f"✅ Meeting '{meeting.title}' scheduled",
            "data": {"meeting_id": meeting.id, "title": meeting.title},
        }

    def _send_message(self, params: dict, workspace_id: str) -> dict:
        message = params.get("message", "").strip()
        status = params.get("status", "").strip().lower()

        if not message:
            return {"ok": False, "error": "Message content is required"}

        if status:
            contacts = self.repo.find_contacts_by_status(workspace_id, status)
        else:
            contacts = self.repo.find_contacts_by_status(workspace_id, "lead")

        if not contacts:
            return {"ok": False, "error": f"No contacts found to send message to"}

        phone_numbers = [c.phone for c in contacts if c.phone]

        return {
            "ok": True,
            "message": f"📢 Message queued for {len(phone_numbers)} contact(s){' with status ' + status if status else ''}",
            "data": {
                "recipients": len(phone_numbers),
                "message": message[:100],
                "note": "WhatsApp/Email sending will be implemented in the connector layer",
            },
        }
