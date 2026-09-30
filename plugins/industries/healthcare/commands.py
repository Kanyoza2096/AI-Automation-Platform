"""Governed healthcare administration commands."""


class HealthcareCommands:
    def __init__(self, repository, redis_client=None):
        self.repo = repository
        self.redis = redis_client

    def list_all(self):
        return [
            {"intent": "add_patient", "execution_mode": "action_kernel", "governed_action_type": "healthcare_add_patient"},
            {"intent": "get_patient", "execution_mode": "read_only"},
            {"intent": "schedule_appointment", "execution_mode": "action_kernel", "governed_action_type": "healthcare_schedule_appointment"},
            {"intent": "get_appointment", "execution_mode": "read_only"},
            {"intent": "update_appointment_status", "execution_mode": "action_kernel", "governed_action_type": "healthcare_update_appointment_status"},
            {"intent": "open_encounter", "execution_mode": "action_kernel", "governed_action_type": "healthcare_open_encounter"},
            {"intent": "create_invoice", "execution_mode": "action_kernel", "governed_action_type": "healthcare_create_invoice"},
            {"intent": "record_care_plan", "execution_mode": "action_kernel", "governed_action_type": "healthcare_record_care_plan"},
            {"intent": "update_care_plan_status", "execution_mode": "action_kernel", "governed_action_type": "healthcare_update_care_plan_status"},
            {"intent": "record_outcome", "execution_mode": "action_kernel", "governed_action_type": "healthcare_record_outcome"},
            {"intent": "send_patient_message", "execution_mode": "action_kernel", "governed_action_type": "healthcare_send_patient_message"},
        ]

    def handle(self, command, params, workspace_id):
        handler = getattr(self, f"_{command}", None)
        if not handler:
            return {"ok": False, "workspace_id": workspace_id, "error": f"Unknown healthcare command: {command}"}
        return handler(params, workspace_id)

    def _add_patient(self, p, ws):
        name = str(p.get("full_name", "")).strip()
        if not name:
            return {"ok": False, "workspace_id": ws, "error": "Patient name is required"}
        obj = self.repo.add_patient(ws, {"full_name": name, "phone": str(p.get("phone", "")), "external_ref": p.get("external_ref"), "status": p.get("status", "active")})
        return {"ok": True, "workspace_id": ws, "data": {"patient_id": obj.id, "name": obj.full_name}}

    def _get_patient(self, p, ws):
        obj = self.repo.get_patient(ws, str(p.get("patient_id", "")))
        if obj is None:
            return {"ok": False, "workspace_id": ws, "error": "Patient not found"}
        return {"ok": True, "workspace_id": ws, "data": obj.model_dump(mode="json")}

    def _schedule_appointment(self, p, ws):
        if not p.get("patient_id") or not p.get("scheduled_for"):
            return {"ok": False, "workspace_id": ws, "error": "patient_id and scheduled_for are required"}
        if self.repo.get_patient(ws, str(p["patient_id"])) is None:
            return {"ok": False, "workspace_id": ws, "error": "Patient not found"}
        obj = self.repo.create_appointment(ws, {"patient_id": str(p["patient_id"]), "scheduled_for": str(p["scheduled_for"]), "department": str(p.get("department", "general"))})
        return {"ok": True, "workspace_id": ws, "data": {"appointment_id": obj.id, "status": obj.status}}

    def _get_appointment(self, p, ws):
        obj = self.repo.get_appointment(ws, str(p.get("appointment_id", "")))
        if obj is None:
            return {"ok": False, "workspace_id": ws, "error": "Appointment not found"}
        return {"ok": True, "workspace_id": ws, "data": obj.model_dump(mode="json")}

    def _update_appointment_status(self, p, ws):
        status = str(p.get("status", "")).lower()
        if status not in {"scheduled", "confirmed", "checked_in", "completed", "cancelled", "no_show"}:
            return {"ok": False, "workspace_id": ws, "error": "Invalid appointment status"}
        obj = self.repo.update_appointment_status(ws, str(p.get("appointment_id", "")), status)
        if obj is None:
            return {"ok": False, "workspace_id": ws, "error": "Appointment not found"}
        return {"ok": True, "workspace_id": ws, "data": {"appointment_id": obj.id, "status": obj.status}}

    def _open_encounter(self, p, ws):
        patient_id = str(p.get("patient_id", ""))
        if self.repo.get_patient(ws, patient_id) is None:
            return {"ok": False, "workspace_id": ws, "error": "Patient not found"}
        obj = self.repo.create_encounter(ws, {"patient_id": patient_id, "appointment_id": p.get("appointment_id")})
        return {"ok": True, "workspace_id": ws, "data": {"encounter_id": obj.id, "status": obj.status}}

    def _create_invoice(self, p, ws):
        patient_id = str(p.get("patient_id", ""))
        amount = float(p.get("amount", 0))
        if self.repo.get_patient(ws, patient_id) is None:
            return {"ok": False, "workspace_id": ws, "error": "Patient not found"}
        if amount <= 0:
            return {"ok": False, "workspace_id": ws, "error": "Invoice amount must be positive"}
        obj = self.repo.create_invoice(ws, {"patient_id": patient_id, "amount": amount, "currency": p.get("currency", "MWK")})
        return {"ok": True, "workspace_id": ws, "data": {"invoice_id": obj.id, "amount": obj.amount, "status": obj.status}}

    def _send_patient_message(self, p, ws):
        message = str(p.get("message", "")).strip()
        if not message:
            return {"ok": False, "workspace_id": ws, "error": "Message is required"}
        return {"ok": True, "workspace_id": ws, "status": "action_required", "execution_mode": "action_kernel", "action_type": "healthcare_send_patient_message", "message": "Patient messaging will be implemented in the connector layer", "data": {"patient_id": p.get("patient_id"), "message": message[:200]}}

    def _record_care_plan(self, p, ws):
        patient_id = str(p.get("patient_id", ""))
        if self.repo.get_patient(ws, patient_id) is None:
            return {"ok": False, "workspace_id": ws, "error": "Patient not found"}
        plan = self.repo.record_care_plan(ws, p)
        return {"ok": True, "workspace_id": ws, "status": "recorded", "execution_mode": "action_kernel", "data": plan.model_dump(mode="json")}

    def _update_care_plan_status(self, p, ws):
        status = str(p.get("status", "")).strip().lower()
        if status not in {"planned", "active", "completed", "cancelled"}:
            return {"ok": False, "workspace_id": ws, "error": "Invalid care plan status"}
        plan = self.repo.update_care_plan_status(ws, str(p.get("plan_id", "")), status)
        return {"ok": bool(plan), "workspace_id": ws, "execution_mode": "action_kernel", "data": plan.model_dump(mode="json") if plan else None, "error": None if plan else "Care plan not found"}

    def _record_outcome(self, p, ws):
        outcome = self.repo.record_outcome(ws, p)
        return {"ok": True, "workspace_id": ws, "status": "observed", "execution_mode": "action_kernel", "data": outcome.model_dump(mode="json")}
