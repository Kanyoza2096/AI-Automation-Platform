"""Healthcare repository with durable Supabase boundary and test fallback."""
import uuid
from .models import Patient, Appointment, Encounter, Invoice, HealthcareCarePlan, HealthcareOutcome


class HealthcareRepository:
    TABLES = {
        Patient: "healthcare_patients",
        Appointment: "healthcare_appointments",
        Encounter: "healthcare_encounters",
        Invoice: "healthcare_invoices",
        HealthcareCarePlan: "healthcare_care_plans",
        HealthcareOutcome: "healthcare_outcomes",
    }

    def __init__(self, supabase_client=None):
        self.supabase = supabase_client
        self._memory = {}

    def _put(self, obj):
        if self.supabase:
            self.supabase.table(self.TABLES[type(obj)]).insert(obj.model_dump(mode="json")).execute()
        self._memory[obj.id] = obj
        return obj

    def _get(self, cls, workspace_id, object_id):
        obj = self._memory.get(object_id)
        if isinstance(obj, cls) and obj.workspace_id == workspace_id:
            return obj
        if self.supabase:
            res = self.supabase.table(self.TABLES[cls]).select("*").eq("workspace_id", workspace_id).eq("id", object_id).limit(1).execute()
            if res.data:
                return cls(**res.data[0])
        return None

    def add_patient(self, workspace_id, data):
        return self._put(Patient(id=f"pat-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, **data))

    def get_patient(self, workspace_id, patient_id):
        return self._get(Patient, workspace_id, patient_id)

    def create_appointment(self, workspace_id, data):
        return self._put(Appointment(id=f"apt-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, **data))

    def get_appointment(self, workspace_id, appointment_id):
        return self._get(Appointment, workspace_id, appointment_id)

    def create_encounter(self, workspace_id, data):
        return self._put(Encounter(id=f"enc-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, **data))

    def update_appointment_status(self, workspace_id, appointment_id, status):
        obj = self._get(Appointment, workspace_id, appointment_id)
        if obj is None:
            return None
        updated = obj.model_copy(update={"status": status})
        self._memory[updated.id] = updated
        if self.supabase:
            self.supabase.table(self.TABLES[Appointment]).update({"status": status}).eq("workspace_id", workspace_id).eq("id", appointment_id).execute()
        return updated

    def create_invoice(self, workspace_id, data):
        return self._put(Invoice(id=f"inv-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, **data))

    def record_care_plan(self, workspace_id, data):
        plan = HealthcareCarePlan(
            id=f"plan-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id,
            patient_id=str(data.get("patient_id", "")),
            plan_type=str(data.get("plan_type", "follow_up")).strip().lower(),
            status=str(data.get("status", "planned")).strip().lower(),
            owner=data.get("owner"),
        )
        if not plan.patient_id:
            raise ValueError("patient_id is required")
        return self._put(plan)

    def update_care_plan_status(self, workspace_id, plan_id, status):
        obj = self._get(HealthcareCarePlan, workspace_id, plan_id)
        if obj is None:
            return None
        updated = obj.model_copy(update={"status": status})
        self._memory[updated.id] = updated
        if self.supabase:
            self.supabase.table(self.TABLES[HealthcareCarePlan]).update({"status": status}).eq("workspace_id", workspace_id).eq("id", plan_id).execute()
        return updated

    def record_outcome(self, workspace_id, data):
        outcome = HealthcareOutcome(
            id=f"out-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id,
            patient_id=data.get("patient_id"), metric=str(data.get("metric", "")).strip(),
            value=float(data.get("value", 0)), source=str(data.get("source", "healthcare")).strip() or "healthcare",
            evidence=dict(data.get("evidence") or {}),
        )
        if not outcome.metric:
            raise ValueError("metric is required")
        return self._put(outcome)
