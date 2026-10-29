"""Healthcare repository with durable Supabase boundary and test fallback."""
import uuid
from datetime import datetime, timezone
from .models import Patient, Appointment, Encounter, Invoice, HealthcareCarePlan, HealthcareOutcome, HealthcarePatientMessageDelivery, HealthcarePatientMessageReconciliationEvidence


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


    # -- governed patient-message lifecycle -------------------------------
    def create_patient_message_delivery(self, workspace_id, data):
        delivery = HealthcarePatientMessageDelivery(
            id=f"hc-msg-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id,
            patient_id=str(data.get("patient_id", "")), to_number=str(data.get("to_number", "")).strip(),
            body=str(data.get("body", "")), idempotency_key=str(data.get("idempotency_key", "")).strip(),
            provider=str(data.get("provider", "twilio")), provider_message_sid=data.get("provider_message_sid"),
            provider_status=str(data.get("provider_status", "unknown")), status=str(data.get("status", "pending")),
            reconciliation_required=bool(data.get("reconciliation_required", True)), evidence=dict(data.get("evidence") or {}))
        if not delivery.patient_id or not delivery.to_number or not delivery.body or not delivery.idempotency_key:
            raise ValueError("patient_id, to_number, body and idempotency_key are required")
        if self.supabase: self.supabase.table("healthcare_patient_message_deliveries").insert(delivery.model_dump(mode="json")).execute()
        self._memory[delivery.id] = delivery
        return delivery

    def get_patient_message_delivery(self, workspace_id, delivery_id):
        obj=self._memory.get(delivery_id)
        if isinstance(obj, HealthcarePatientMessageDelivery) and obj.workspace_id == workspace_id: return obj
        if self.supabase:
            res=self.supabase.table("healthcare_patient_message_deliveries").select("*").eq("workspace_id",workspace_id).eq("id",delivery_id).limit(1).execute()
            if res.data: return HealthcarePatientMessageDelivery(**res.data[0])
        return None

    def get_patient_message_delivery_by_idempotency_key(self, workspace_id, key):
        if self.supabase:
            res=self.supabase.table("healthcare_patient_message_deliveries").select("*").eq("workspace_id",workspace_id).eq("idempotency_key",key).limit(1).execute()
            if res.data:
                row=HealthcarePatientMessageDelivery(**res.data[0]); self._memory[row.id]=row; return row
        return next((x for x in self._memory.values() if isinstance(x,HealthcarePatientMessageDelivery) and x.workspace_id==workspace_id and x.idempotency_key==key),None)

    def update_patient_message_delivery(self, workspace_id, delivery_id, **changes):
        delivery=self.get_patient_message_delivery(workspace_id,delivery_id)
        if delivery is None: raise ValueError("patient message delivery not found in workspace")
        updated=delivery.model_copy(update={**changes,"updated_at":datetime.now(timezone.utc)})
        if self.supabase: self.supabase.table("healthcare_patient_message_deliveries").update(updated.model_dump(mode="json")).eq("workspace_id",workspace_id).eq("id",delivery_id).execute()
        self._memory[delivery_id]=updated; return updated

    def list_reconciliation_required_patient_message_deliveries(self, workspace_id=None, limit=100):
        if self.supabase:
            q=self.supabase.table("healthcare_patient_message_deliveries").select("*").eq("reconciliation_required",True).order("created_at").limit(limit)
            if workspace_id is not None: q=q.eq("workspace_id",workspace_id)
            return [HealthcarePatientMessageDelivery(**r) for r in (q.execute().data or [])]
        rows=[x for x in self._memory.values() if isinstance(x,HealthcarePatientMessageDelivery) and x.reconciliation_required and (workspace_id is None or x.workspace_id==workspace_id)]
        return sorted(rows,key=lambda x:x.created_at)[:limit]

    def record_patient_message_reconciliation_evidence(self, workspace_id, data):
        delivery_id=str(data.get("delivery_id",""))
        if self.get_patient_message_delivery(workspace_id,delivery_id) is None: raise ValueError("patient message delivery not found in workspace")
        row=HealthcarePatientMessageReconciliationEvidence(id=f"hc-msg-rec-{uuid.uuid4().hex[:12]}",workspace_id=workspace_id,delivery_id=delivery_id,provider=str(data.get("provider","twilio")),provider_reference=str(data.get("provider_reference","")),attempt_number=int(data.get("attempt_number",0)),provider_status=str(data.get("provider_status","unknown")),canonical_status=str(data.get("canonical_status","uncertain")),terminal=bool(data.get("terminal",False)),verification_state=str(data.get("verification_state","provider_observed_nonterminal")),observed=dict(data.get("observed") or {}),evidence=dict(data.get("evidence") or {}))
        if self.supabase: self.supabase.table("healthcare_patient_message_reconciliation_evidence").insert(row.model_dump(mode="json")).execute()
        self._memory[row.id]=row; return row

    def list_patient_message_reconciliation_evidence(self, workspace_id, delivery_id, limit=100):
        if self.supabase:
            res=self.supabase.table("healthcare_patient_message_reconciliation_evidence").select("*").eq("workspace_id",workspace_id).eq("delivery_id",delivery_id).order("attempt_number",desc=True).limit(limit).execute()
            return [HealthcarePatientMessageReconciliationEvidence(**r) for r in (res.data or [])]
        rows=[x for x in self._memory.values() if isinstance(x,HealthcarePatientMessageReconciliationEvidence) and x.workspace_id==workspace_id and x.delivery_id==delivery_id]
        return sorted(rows,key=lambda x:x.attempt_number,reverse=True)[:limit]
