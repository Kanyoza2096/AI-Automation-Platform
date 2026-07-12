"""Church MIS — natural language command handlers."""

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("kanyoza.plugins.crm.commands")


class CRMMISCommands:
    """Handles NL commands for church management."""

    def __init__(self, repository, redis_client=None):
        self.repo = repository
        self.redis = redis_client

    def list_all(self) -> list[dict]:
        """Return all commands for the command executor registry."""
        return [
            {
                "intent": "add_contact",
                "description": "Register a new church contact",
                "params": ["name", "phone"],
                "example": "Add contact John Doe phone 0888123456",
                "category": "contacts",
            },
            {
                "intent": "get_contacts",
                "description": "List contacts by department or all contacts",
                "params": ["department"],
                "example": "Show all choir contacts",
                "category": "contacts",
            },
            {
                "intent": "record_attendance",
                "description": "Record service attendance for contacts",
                "params": ["service_date", "contacts"],
                "example": "Record attendance for Sunday service John Mary Peter",
                "category": "attendance",
            },
            {
                "intent": "add_donation",
                "description": "Record a donation or tithe",
                "params": ["amount", "donor", "purpose"],
                "example": "Add donation 50000 from John Doe purpose tithe",
                "category": "donations",
            },
            {
                "intent": "add_event",
                "description": "Create a church event",
                "params": ["title", "date", "time", "location"],
                "example": "Add event Prayer Meeting on 2026-07-20 at 17:00 location Main Hall",
                "category": "events",
            },
            {
                "intent": "send_announcement",
                "description": "Send WhatsApp announcement to contacts",
                "params": ["message", "department"],
                "example": "Announce service time change to choir contacts",
                "category": "communication",
            },
        ]

    def handle(self, command: str, params: dict, workspace_id: str = "default") -> dict:
        """Dispatch a command to the correct handler."""
        handlers = {
            "add_contact": self._add_contact,
            "get_contacts": self._get_contacts,
            "record_attendance": self._record_attendance,
            "add_donation": self._add_donation,
            "add_event": self._add_event,
            "send_announcement": self._send_announcement,
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
            "full_name": name,
            "phone": params.get("phone", ""),
            "email": params.get("email"),
            "address": params.get("address"),
            "department": params.get("department", "congregation"),
            "role": params.get("role", "contact"),
        })

        return {
            "ok": True,
            "message": f"✅ Contact '{contact.full_name}' registered successfully",
            "data": {"contact_id": contact.id, "name": contact.full_name},
        }

    def _get_contacts(self, params: dict, workspace_id: str) -> dict:
        department = params.get("department", "").strip().lower()

        if department:
            contacts = self.repo.find_contacts_by_department(workspace_id, department)
        else:
            contacts = self.repo.find_contacts_by_department(workspace_id, "congregation")

        if not contacts:
            return {
                "ok": True,
                "message": f"No contacts found{' in ' + department if department else ''}.",
                "data": {"contacts": [], "count": 0},
            }

        contact_list = [{"id": m.id, "name": m.full_name, "phone": m.phone, "department": m.department} for m in contacts]

        return {
            "ok": True,
            "message": f"Found {len(contacts)} contact(s){' in ' + department if department else ''}.",
            "data": {"contacts": contact_list, "count": len(contacts)},
        }

    def _record_attendance(self, params: dict, workspace_id: str) -> dict:
        service_date = params.get("service_date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        contact_names_str = params.get("contacts", "")
        service_type = params.get("service_type", "sunday_service")

        if not contact_names_str:
            return {"ok": False, "error": "Please specify which contacts attended"}

        names = [n.strip() for n in contact_names_str.split(",") if n.strip()]

        contact_ids = []
        contact_names_found = []
        not_found = []

        for name in names:
            contact = self.repo.find_contact_by_name(workspace_id, name)
            if contact:
                contact_ids.append(contact.id)
                contact_names_found.append(contact.full_name)
            else:
                not_found.append(name)

        if not contact_ids:
            return {"ok": False, "error": f"No contacts found for: {', '.join(not_found)}"}

        records = self.repo.record_attendance(
            workspace_id, service_date, contact_ids, contact_names_found, service_type,
        )

        msg = f"✅ Attendance recorded for {len(records)} contact(s) on {service_date}"
        if not_found:
            msg += f". Not found: {', '.join(not_found)}"

        return {
            "ok": True,
            "message": msg,
            "data": {"recorded": len(records), "not_found": not_found},
        }

    def _add_donation(self, params: dict, workspace_id: str) -> dict:
        amount = params.get("amount", 0)
        donor_name = params.get("donor", "").strip()
        purpose = params.get("purpose", "tithe")

        if not amount or float(amount) <= 0:
            return {"ok": False, "error": "Valid donation amount is required"}
        if not donor_name:
            return {"ok": False, "error": "Donor name is required"}

        # Try to find the donor as a contact
        donor_id = ""
        contact = self.repo.find_contact_by_name(workspace_id, donor_name)
        if contact:
            donor_id = contact.id
            donor_name = contact.full_name

        donation = self.repo.add_donation(workspace_id, {
            "donor_id": donor_id,
            "donor_name": donor_name,
            "amount": float(amount),
            "purpose": purpose,
            "payment_method": params.get("payment_method", "cash"),
            "donation_date": params.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
        })

        return {
            "ok": True,
            "message": f"✅ Donation of {donation.amount} {donation.currency} recorded from {donor_name} ({purpose})",
            "data": {"donation_id": donation.id, "amount": donation.amount},
        }

    def _add_event(self, params: dict, workspace_id: str) -> dict:
        title = params.get("title", "").strip()
        if not title:
            return {"ok": False, "error": "Event title is required"}

        event = self.repo.add_event(workspace_id, {
            "title": title,
            "description": params.get("description"),
            "event_date": params.get("date", ""),
            "start_time": params.get("time"),
            "location": params.get("location"),
        })

        return {
            "ok": True,
            "message": f"✅ Event '{event.title}' created",
            "data": {"event_id": event.id, "title": event.title},
        }

    def _send_announcement(self, params: dict, workspace_id: str) -> dict:
        message = params.get("message", "").strip()
        department = params.get("department", "").strip().lower()

        if not message:
            return {"ok": False, "error": "Announcement message is required"}

        if department:
            contacts = self.repo.find_contacts_by_department(workspace_id, department)
        else:
            contacts = self.repo.find_contacts_by_department(workspace_id, "congregation")

        if not contacts:
            return {"ok": False, "error": f"No contacts found to send announcement to"}

        # In production, this would call the WhatsApp connector
        phone_numbers = [m.phone for m in contacts if m.phone]

        return {
            "ok": True,
            "message": f"📢 Announcement queued for {len(phone_numbers)} contact(s){' in ' + department if department else ''}",
            "data": {
                "recipients": len(phone_numbers),
                "message": message[:100],
                "note": "WhatsApp sending will be implemented in the connector layer",
            },
        }
