"""Church MIS — natural language command handlers."""

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("kanyoza.plugins.hospital.commands")


class HospitalMISCommands:
    """Handles NL commands for church management."""

    def __init__(self, repository, redis_client=None):
        self.repo = repository
        self.redis = redis_client

    def list_all(self) -> list[dict]:
        """Return all commands for the command executor registry."""
        return [
            {
                "intent": "add_patient",
                "description": "Register a new church patient",
                "params": ["name", "phone"],
                "example": "Add patient John Doe phone 0888123456",
                "category": "patients",
            },
            {
                "intent": "get_patients",
                "description": "List patients by department or all patients",
                "params": ["department"],
                "example": "Show all choir patients",
                "category": "patients",
            },
            {
                "intent": "record_attendance",
                "description": "Record service attendance for patients",
                "params": ["service_date", "patients"],
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
                "description": "Send WhatsApp announcement to patients",
                "params": ["message", "department"],
                "example": "Announce service time change to choir patients",
                "category": "communication",
            },
        ]

    def handle(self, command: str, params: dict, workspace_id: str = "default") -> dict:
        """Dispatch a command to the correct handler."""
        handlers = {
            "add_patient": self._add_patient,
            "get_patients": self._get_patients,
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

    def _add_patient(self, params: dict, workspace_id: str) -> dict:
        name = params.get("name", "").strip()
        if not name:
            return {"ok": False, "error": "Patient name is required"}

        patient = self.repo.add_patient(workspace_id, {
            "full_name": name,
            "phone": params.get("phone", ""),
            "email": params.get("email"),
            "address": params.get("address"),
            "department": params.get("department", "congregation"),
            "role": params.get("role", "patient"),
        })

        return {
            "ok": True,
            "message": f"✅ Patient '{patient.full_name}' registered successfully",
            "data": {"patient_id": patient.id, "name": patient.full_name},
        }

    def _get_patients(self, params: dict, workspace_id: str) -> dict:
        department = params.get("department", "").strip().lower()

        if department:
            patients = self.repo.find_patients_by_department(workspace_id, department)
        else:
            patients = self.repo.find_patients_by_department(workspace_id, "congregation")

        if not patients:
            return {
                "ok": True,
                "message": f"No patients found{' in ' + department if department else ''}.",
                "data": {"patients": [], "count": 0},
            }

        patient_list = [{"id": m.id, "name": m.full_name, "phone": m.phone, "department": m.department} for m in patients]

        return {
            "ok": True,
            "message": f"Found {len(patients)} patient(s){' in ' + department if department else ''}.",
            "data": {"patients": patient_list, "count": len(patients)},
        }

    def _record_attendance(self, params: dict, workspace_id: str) -> dict:
        service_date = params.get("service_date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        patient_names_str = params.get("patients", "")
        service_type = params.get("service_type", "sunday_service")

        if not patient_names_str:
            return {"ok": False, "error": "Please specify which patients attended"}

        names = [n.strip() for n in patient_names_str.split(",") if n.strip()]

        patient_ids = []
        patient_names_found = []
        not_found = []

        for name in names:
            patient = self.repo.find_patient_by_name(workspace_id, name)
            if patient:
                patient_ids.append(patient.id)
                patient_names_found.append(patient.full_name)
            else:
                not_found.append(name)

        if not patient_ids:
            return {"ok": False, "error": f"No patients found for: {', '.join(not_found)}"}

        records = self.repo.record_attendance(
            workspace_id, service_date, patient_ids, patient_names_found, service_type,
        )

        msg = f"✅ Attendance recorded for {len(records)} patient(s) on {service_date}"
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

        # Try to find the donor as a patient
        donor_id = ""
        patient = self.repo.find_patient_by_name(workspace_id, donor_name)
        if patient:
            donor_id = patient.id
            donor_name = patient.full_name

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
            patients = self.repo.find_patients_by_department(workspace_id, department)
        else:
            patients = self.repo.find_patients_by_department(workspace_id, "congregation")

        if not patients:
            return {"ok": False, "error": f"No patients found to send announcement to"}

        # In production, this would call the WhatsApp connector
        phone_numbers = [m.phone for m in patients if m.phone]

        return {
            "ok": True,
            "message": f"📢 Announcement queued for {len(phone_numbers)} patient(s){' in ' + department if department else ''}",
            "data": {
                "recipients": len(phone_numbers),
                "message": message[:100],
                "note": "WhatsApp sending will be implemented in the connector layer",
            },
        }
