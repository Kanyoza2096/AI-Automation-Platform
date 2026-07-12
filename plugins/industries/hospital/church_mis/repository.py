"""Church MIS repository — Supabase + in-memory fallback."""

import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from .models import Patient, Attendance, Donation, Event

logger = logging.getLogger("kanyoza.plugins.hospital")


class HospitalMISRepository:
    """Data access for Church MIS plugin."""

    def __init__(self, supabase_client=None):
        self.supabase = supabase_client
        self._in_memory: dict = {}

    # ── Patients ──────────────────────────────────────────────────────────

    def add_patient(self, workspace_id: str, data: dict) -> Patient:
        patient = Patient(
            id=f"mem-{uuid.uuid4().hex[:12]}",
            workspace_id=workspace_id,
            full_name=data.get("full_name", ""),
            phone=data.get("phone", ""),
            email=data.get("email"),
            address=data.get("address"),
            department=data.get("department", "congregation"),
            role=data.get("role", "patient"),
        )
        if self.supabase:
            try:
                self.supabase.table("hospital_patients").insert(
                    patient.model_dump(mode="json")
                ).execute()
            except Exception as e:
                logger.error("Supabase patient insert failed: %s", e)
        self._in_memory[patient.id] = patient
        return patient

    def find_patient_by_name(self, workspace_id: str, name: str) -> Optional[Patient]:
        name_lower = name.lower()
        if self.supabase:
            try:
                res = (
                    self.supabase.table("hospital_patients")
                    .select("*")
                    .eq("workspace_id", workspace_id)
                    .ilike("full_name", f"%{name}%")
                    .limit(1)
                    .execute()
                )
                if res.data:
                    return Patient(**res.data[0])
            except Exception as e:
                logger.error("Supabase patient lookup failed: %s", e)
        for m in self._in_memory.values():
            if isinstance(m, Patient) and name_lower in m.full_name.lower():
                return m
        return None

    def find_patients_by_department(self, workspace_id: str, department: str) -> List[Patient]:
        if self.supabase:
            try:
                res = (
                    self.supabase.table("hospital_patients")
                    .select("*")
                    .eq("workspace_id", workspace_id)
                    .eq("department", department)
                    .execute()
                )
                return [Patient(**row) for row in (res.data or [])]
            except Exception as e:
                logger.error("Supabase department lookup failed: %s", e)
        return [
            m for m in self._in_memory.values()
            if isinstance(m, Patient)
            and m.workspace_id == workspace_id
            and m.department == department
        ]

    # ── Attendance ───────────────────────────────────────────────────────

    def record_attendance(
        self, workspace_id: str, service_date: str, patient_ids: list[str],
        patient_names: list[str], service_type: str = "sunday_service",
    ) -> list[Attendance]:
        records = []
        for i, mid in enumerate(patient_ids):
            att = Attendance(
                id=f"att-{uuid.uuid4().hex[:12]}",
                workspace_id=workspace_id,
                patient_id=mid,
                patient_name=patient_names[i] if i < len(patient_names) else "",
                service_date=service_date,
                service_type=service_type,
                present=True,
            )
            if self.supabase:
                try:
                    self.supabase.table("hospital_vitals").insert(
                        att.model_dump(mode="json")
                    ).execute()
                except Exception as e:
                    logger.error("Supabase attendance insert failed: %s", e)
            self._in_memory[att.id] = att
            records.append(att)
        return records

    # ── Donations ────────────────────────────────────────────────────────

    def add_donation(self, workspace_id: str, data: dict) -> Donation:
        donation = Donation(
            id=f"don-{uuid.uuid4().hex[:12]}",
            workspace_id=workspace_id,
            donor_id=data.get("donor_id", ""),
            donor_name=data.get("donor_name", ""),
            amount=float(data.get("amount", 0)),
            currency=data.get("currency", "MWK"),
            purpose=data.get("purpose", "tithe"),
            payment_method=data.get("payment_method", "cash"),
            donation_date=data.get("donation_date", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
        )
        if self.supabase:
            try:
                self.supabase.table("hospital_billing").insert(
                    donation.model_dump(mode="json")
                ).execute()
            except Exception as e:
                logger.error("Supabase donation insert failed: %s", e)
        self._in_memory[donation.id] = donation
        return donation

    # ── Events ───────────────────────────────────────────────────────────

    def add_event(self, workspace_id: str, data: dict) -> Event:
        event = Event(
            id=f"evt-{uuid.uuid4().hex[:12]}",
            workspace_id=workspace_id,
            title=data.get("title", ""),
            description=data.get("description"),
            event_date=data.get("event_date", ""),
            start_time=data.get("start_time"),
            location=data.get("location"),
        )
        if self.supabase:
            try:
                self.supabase.table("hospital_events").insert(
                    event.model_dump(mode="json")
                ).execute()
            except Exception as e:
                logger.error("Supabase event insert failed: %s", e)
        self._in_memory[event.id] = event
        return event
