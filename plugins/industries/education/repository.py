"""Education repository with durable Supabase boundary and test fallback."""
import uuid
from datetime import datetime, timezone
from .models import Student, Attendance, Assessment, FeeRecord, EducationIntervention, EducationOutcome, EducationParentMessageDelivery, EducationParentMessageReconciliationEvidence


class EducationRepository:
    def __init__(self, supabase_client=None):
        self.supabase = supabase_client
        self._memory = {}

    def _put(self, table, obj):
        if self.supabase:
            self.supabase.table(table).insert(obj.model_dump(mode="json")).execute()
        self._memory[obj.id] = obj
        return obj

    def add_student(self, workspace_id, data):
        return self._put("education_students", Student(id=f"stu-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, **data))

    def get_student(self, workspace_id, student_id):
        obj = self._memory.get(student_id)
        if isinstance(obj, Student) and obj.workspace_id == workspace_id:
            return obj
        if self.supabase:
            res = self.supabase.table("education_students").select("*").eq("workspace_id", workspace_id).eq("id", student_id).limit(1).execute()
            if res.data:
                return Student(**res.data[0])
        return None

    def update_student_status(self, workspace_id, student_id, status):
        now = datetime.now(timezone.utc)
        if self.supabase:
            res = self.supabase.table("education_students").update({"status": status, "updated_at": now.isoformat()}).eq("workspace_id", workspace_id).eq("id", student_id).select("*").limit(1).execute()
            if res.data:
                student = Student(**res.data[0])
                self._memory[student.id] = student
                return student
        obj = self._memory.get(student_id)
        if isinstance(obj, Student) and obj.workspace_id == workspace_id:
            updated = obj.model_copy(update={"status": status, "updated_at": now})
            self._memory[student_id] = updated
            return updated
        return None

    def record_attendance(self, workspace_id, data):
        return self._put("education_attendance", Attendance(id=f"att-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, **data))

    def add_assessment(self, workspace_id, data):
        return self._put("education_assessments", Assessment(id=f"asm-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, **data))

    def record_fee(self, workspace_id, data):
        return self._put("education_fees", FeeRecord(id=f"fee-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, **data))

    def record_intervention(self, workspace_id, data):
        intervention = EducationIntervention(
            id=f"int-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id,
            student_id=str(data.get("student_id", "")),
            intervention_type=str(data.get("intervention_type", "support")).strip().lower(),
            reason=str(data.get("reason", "")), status=str(data.get("status", "planned")).strip().lower(),
            owner=data.get("owner"),
        )
        if not intervention.student_id or not intervention.intervention_type:
            raise ValueError("student_id and intervention_type are required")
        return self._put("education_interventions", intervention)

    def record_outcome(self, workspace_id, data):
        outcome = EducationOutcome(
            id=f"out-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id,
            student_id=data.get("student_id"), metric=str(data.get("metric", "")).strip(),
            value=float(data.get("value", 0)), source=str(data.get("source", "education")).strip() or "education",
            evidence=dict(data.get("evidence") or {}),
        )
        if not outcome.metric:
            raise ValueError("metric is required")
        return self._put("education_outcomes", outcome)


    # -- governed parent-message lifecycle ---------------------------------
    def create_parent_message_delivery(self, workspace_id, data):
        delivery = EducationParentMessageDelivery(id=f"edu-msg-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id,
            student_id=str(data.get("student_id", "")), to_number=str(data.get("to_number", "")).strip(),
            body=str(data.get("body", "")), idempotency_key=str(data.get("idempotency_key", "")).strip(),
            provider=str(data.get("provider", "twilio")), provider_message_sid=data.get("provider_message_sid"),
            provider_status=str(data.get("provider_status", "unknown")), status=str(data.get("status", "pending")),
            reconciliation_required=bool(data.get("reconciliation_required", True)), evidence=dict(data.get("evidence") or {}))
        if not delivery.student_id or not delivery.to_number or not delivery.body or not delivery.idempotency_key:
            raise ValueError("student_id, to_number, body and idempotency_key are required")
        if self.supabase:
            self.supabase.table("education_parent_message_deliveries").insert(delivery.model_dump(mode="json")).execute()
        self._memory[delivery.id] = delivery
        return delivery

    def get_parent_message_delivery(self, workspace_id, delivery_id):
        obj=self._memory.get(delivery_id)
        if isinstance(obj, EducationParentMessageDelivery) and obj.workspace_id == workspace_id: return obj
        if self.supabase:
            res=self.supabase.table("education_parent_message_deliveries").select("*").eq("workspace_id",workspace_id).eq("id",delivery_id).limit(1).execute()
            if res.data: return EducationParentMessageDelivery(**res.data[0])
        return None

    def get_parent_message_delivery_by_idempotency_key(self, workspace_id, key):
        if self.supabase:
            res=self.supabase.table("education_parent_message_deliveries").select("*").eq("workspace_id",workspace_id).eq("idempotency_key",key).limit(1).execute()
            if res.data:
                row=EducationParentMessageDelivery(**res.data[0]); self._memory[row.id]=row; return row
        return next((x for x in self._memory.values() if isinstance(x,EducationParentMessageDelivery) and x.workspace_id==workspace_id and x.idempotency_key==key),None)

    def update_parent_message_delivery(self, workspace_id, delivery_id, **changes):
        delivery=self.get_parent_message_delivery(workspace_id,delivery_id)
        if delivery is None: raise ValueError("parent message delivery not found in workspace")
        updated=delivery.model_copy(update={**changes,"updated_at":datetime.now(timezone.utc)})
        if self.supabase:
            self.supabase.table("education_parent_message_deliveries").update(updated.model_dump(mode="json")).eq("workspace_id",workspace_id).eq("id",delivery_id).execute()
        self._memory[delivery_id]=updated; return updated

    def list_reconciliation_required_parent_message_deliveries(self, workspace_id=None, limit=100):
        if self.supabase:
            q=self.supabase.table("education_parent_message_deliveries").select("*").eq("reconciliation_required",True).order("created_at").limit(limit)
            if workspace_id is not None: q=q.eq("workspace_id",workspace_id)
            return [EducationParentMessageDelivery(**r) for r in (q.execute().data or [])]
        rows=[x for x in self._memory.values() if isinstance(x,EducationParentMessageDelivery) and x.reconciliation_required and (workspace_id is None or x.workspace_id==workspace_id)]
        return sorted(rows,key=lambda x:x.created_at)[:limit]

    def record_parent_message_reconciliation_evidence(self, workspace_id, data):
        delivery_id=str(data.get("delivery_id",""))
        if self.get_parent_message_delivery(workspace_id,delivery_id) is None: raise ValueError("parent message delivery not found in workspace")
        row=EducationParentMessageReconciliationEvidence(id=f"edu-msg-rec-{uuid.uuid4().hex[:12]}",workspace_id=workspace_id,delivery_id=delivery_id,provider=str(data.get("provider","twilio")),provider_reference=str(data.get("provider_reference","")),attempt_number=int(data.get("attempt_number",0)),provider_status=str(data.get("provider_status","unknown")),canonical_status=str(data.get("canonical_status","uncertain")),terminal=bool(data.get("terminal",False)),verification_state=str(data.get("verification_state","provider_observed_nonterminal")),observed=dict(data.get("observed") or {}),evidence=dict(data.get("evidence") or {}))
        if self.supabase: self.supabase.table("education_parent_message_reconciliation_evidence").insert(row.model_dump(mode="json")).execute()
        self._memory[row.id]=row; return row

    def list_parent_message_reconciliation_evidence(self, workspace_id, delivery_id, limit=100):
        if self.supabase:
            res=self.supabase.table("education_parent_message_reconciliation_evidence").select("*").eq("workspace_id",workspace_id).eq("delivery_id",delivery_id).order("attempt_number",desc=True).limit(limit).execute()
            return [EducationParentMessageReconciliationEvidence(**r) for r in (res.data or [])]
        rows=[x for x in self._memory.values() if isinstance(x,EducationParentMessageReconciliationEvidence) and x.workspace_id==workspace_id and x.delivery_id==delivery_id]
        return sorted(rows,key=lambda x:x.attempt_number,reverse=True)[:limit]
