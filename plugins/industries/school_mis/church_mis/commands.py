"""Church MIS — natural language command handlers."""

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("kanyoza.plugins.school_mis.commands")


class SchoolMISCommands:
    """Handles NL commands for church management."""

    def __init__(self, repository, redis_client=None):
        self.repo = repository
        self.redis = redis_client

    def list_all(self) -> list[dict]:
        """Return all commands for the command executor registry."""
        return [
            {
                "intent": "add_student",
                "description": "Register a new church student",
                "params": ["name", "phone"],
                "example": "Add student John Doe phone 0888123456",
                "category": "students",
            },
            {
                "intent": "get_students",
                "description": "List students by department or all students",
                "params": ["department"],
                "example": "Show all choir students",
                "category": "students",
            },
            {
                "intent": "record_attendance",
                "description": "Record service attendance for students",
                "params": ["service_date", "students"],
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
                "description": "Send WhatsApp announcement to students",
                "params": ["message", "department"],
                "example": "Announce service time change to choir students",
                "category": "communication",
            },
        ]

    def handle(self, command: str, params: dict, workspace_id: str = "default") -> dict:
        """Dispatch a command to the correct handler."""
        handlers = {
            "add_student": self._add_student,
            "get_students": self._get_students,
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

    def _add_student(self, params: dict, workspace_id: str) -> dict:
        name = params.get("name", "").strip()
        if not name:
            return {"ok": False, "error": "Student name is required"}

        student = self.repo.add_student(workspace_id, {
            "full_name": name,
            "phone": params.get("phone", ""),
            "email": params.get("email"),
            "address": params.get("address"),
            "department": params.get("department", "congregation"),
            "role": params.get("role", "student"),
        })

        return {
            "ok": True,
            "message": f"✅ Student '{student.full_name}' registered successfully",
            "data": {"student_id": student.id, "name": student.full_name},
        }

    def _get_students(self, params: dict, workspace_id: str) -> dict:
        department = params.get("department", "").strip().lower()

        if department:
            students = self.repo.find_students_by_department(workspace_id, department)
        else:
            students = self.repo.find_students_by_department(workspace_id, "congregation")

        if not students:
            return {
                "ok": True,
                "message": f"No students found{' in ' + department if department else ''}.",
                "data": {"students": [], "count": 0},
            }

        student_list = [{"id": m.id, "name": m.full_name, "phone": m.phone, "department": m.department} for m in students]

        return {
            "ok": True,
            "message": f"Found {len(students)} student(s){' in ' + department if department else ''}.",
            "data": {"students": student_list, "count": len(students)},
        }

    def _record_attendance(self, params: dict, workspace_id: str) -> dict:
        service_date = params.get("service_date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        student_names_str = params.get("students", "")
        service_type = params.get("service_type", "sunday_service")

        if not student_names_str:
            return {"ok": False, "error": "Please specify which students attended"}

        names = [n.strip() for n in student_names_str.split(",") if n.strip()]

        student_ids = []
        student_names_found = []
        not_found = []

        for name in names:
            student = self.repo.find_student_by_name(workspace_id, name)
            if student:
                student_ids.append(student.id)
                student_names_found.append(student.full_name)
            else:
                not_found.append(name)

        if not student_ids:
            return {"ok": False, "error": f"No students found for: {', '.join(not_found)}"}

        records = self.repo.record_attendance(
            workspace_id, service_date, student_ids, student_names_found, service_type,
        )

        msg = f"✅ Attendance recorded for {len(records)} student(s) on {service_date}"
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

        # Try to find the donor as a student
        donor_id = ""
        student = self.repo.find_student_by_name(workspace_id, donor_name)
        if student:
            donor_id = student.id
            donor_name = student.full_name

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
            students = self.repo.find_students_by_department(workspace_id, department)
        else:
            students = self.repo.find_students_by_department(workspace_id, "congregation")

        if not students:
            return {"ok": False, "error": f"No students found to send announcement to"}

        # In production, this would call the WhatsApp connector
        phone_numbers = [m.phone for m in students if m.phone]

        return {
            "ok": True,
            "message": f"📢 Announcement queued for {len(phone_numbers)} student(s){' in ' + department if department else ''}",
            "data": {
                "recipients": len(phone_numbers),
                "message": message[:100],
                "note": "WhatsApp sending will be implemented in the connector layer",
            },
        }
