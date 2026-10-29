"""Governed Education domain commands."""


class EducationCommands:
    def __init__(self, repository, redis_client=None):
        self.repo = repository
        self.redis = redis_client

    def list_all(self):
        return [
            {"intent": "add_student", "execution_mode": "action_kernel", "governed_action_type": "education_add_student"},
            {"intent": "get_student", "execution_mode": "read_only"},
            {"intent": "update_student_status", "execution_mode": "action_kernel", "governed_action_type": "education_update_student_status"},
            {"intent": "record_attendance", "execution_mode": "action_kernel", "governed_action_type": "education_record_attendance"},
            {"intent": "record_assessment", "execution_mode": "action_kernel", "governed_action_type": "education_record_assessment"},
            {"intent": "record_fee", "execution_mode": "action_kernel", "governed_action_type": "education_record_fee"},
            {"intent": "record_intervention", "execution_mode": "action_kernel", "governed_action_type": "education_record_intervention"},
            {"intent": "record_outcome", "execution_mode": "action_kernel", "governed_action_type": "education_record_outcome"},
            {"intent": "send_parent_message", "execution_mode": "action_kernel", "governed_action_type": "education_send_parent_message"},
        ]

    def handle(self, command, params, workspace_id):
        handler = getattr(self, f"_{command}", None)
        if not handler:
            return {"ok": False, "error": f"Unknown command: {command}"}
        return handler(params, workspace_id)

    def _add_student(self, p, ws):
        name = str(p.get("full_name", "")).strip()
        if not name:
            return {"ok": False, "error": "Student name is required"}
        s = self.repo.add_student(ws, {"full_name": name, "grade": p.get("grade", ""), "guardian_name": p.get("guardian_name"), "guardian_phone": p.get("guardian_phone", "")})
        return {"ok": True, "workspace_id": ws, "data": {"student_id": s.id, "name": s.full_name}}

    def _get_student(self, p, ws):
        s = self.repo.get_student(ws, p.get("student_id", ""))
        if not s:
            return {"ok": False, "workspace_id": ws, "error": "Student not found"}
        return {"ok": True, "workspace_id": ws, "data": s.model_dump(mode="json")}

    def _update_student_status(self, p, ws):
        status = str(p.get("status", "")).strip().lower()
        if not status:
            return {"ok": False, "workspace_id": ws, "error": "Student status is required"}
        s = self.repo.update_student_status(ws, str(p.get("student_id", "")), status)
        return {"ok": bool(s), "workspace_id": ws, "data": s.model_dump(mode="json") if s else None, "error": None if s else "Student not found", "execution_mode": "action_kernel"}

    def _record_attendance(self, p, ws):
        r = self.repo.record_attendance(ws, {"student_id": p.get("student_id", ""), "date": p.get("date", ""), "status": p.get("status", "present")})
        return {"ok": True, "workspace_id": ws, "data": {"attendance_id": r.id}, "execution_mode": "action_kernel"}

    def _record_assessment(self, p, ws):
        r = self.repo.add_assessment(ws, {"student_id": p.get("student_id", ""), "subject": p.get("subject", ""), "score": float(p.get("score", 0)), "max_score": float(p.get("max_score", 100))})
        return {"ok": True, "workspace_id": ws, "data": {"assessment_id": r.id}, "execution_mode": "action_kernel"}

    def _record_fee(self, p, ws):
        r = self.repo.record_fee(ws, {"student_id": p.get("student_id", ""), "amount": float(p.get("amount", 0)), "currency": p.get("currency", "MWK")})
        return {"ok": True, "workspace_id": ws, "data": {"fee_id": r.id}, "execution_mode": "action_kernel"}

    def _record_intervention(self, p, ws):
        intervention = self.repo.record_intervention(ws, p)
        return {"ok": True, "workspace_id": ws, "status": "recorded", "execution_mode": "action_kernel", "data": intervention.model_dump(mode="json")}

    def _record_outcome(self, p, ws):
        outcome = self.repo.record_outcome(ws, p)
        return {"ok": True, "workspace_id": ws, "status": "observed", "execution_mode": "action_kernel", "data": outcome.model_dump(mode="json")}

    def _send_parent_message(self, p, ws):
        if not str(p.get("message", "")).strip():
            return {"ok": False, "workspace_id": ws, "error": "Message is required"}
        return {"ok": True, "workspace_id": ws, "status": "action_required", "execution_mode": "action_kernel", "message": "Parent messaging will be implemented in the connector layer", "data": {"student_id": p.get("student_id"), "message": str(p["message"])[:200]}}
