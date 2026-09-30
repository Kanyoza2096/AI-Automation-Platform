"""Education repository with durable Supabase boundary and test fallback."""
import uuid
from datetime import datetime, timezone
from .models import Student, Attendance, Assessment, FeeRecord, EducationIntervention, EducationOutcome


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
