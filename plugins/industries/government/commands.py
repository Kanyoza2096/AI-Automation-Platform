"""Governed Government / Public Services commands."""


class GovernmentCommands:
    def __init__(self, repository, redis_client=None):
        self.repo = repository
        self.redis = redis_client

    def list_all(self):
        return [
            {"intent": "create_service", "execution_mode": "action_kernel", "governed_action_type": "government_create_service"},
            {"intent": "get_service", "execution_mode": "read_only"},
            {"intent": "create_service_request", "execution_mode": "action_kernel", "governed_action_type": "government_create_service_request"},
            {"intent": "get_service_request", "execution_mode": "read_only"},
            {"intent": "update_service_request", "execution_mode": "action_kernel", "governed_action_type": "government_update_service_request"},
            {"intent": "create_permit_case", "execution_mode": "action_kernel", "governed_action_type": "government_create_permit_case"},
            {"intent": "get_permit_case", "execution_mode": "read_only"},
            {"intent": "create_case", "execution_mode": "action_kernel", "governed_action_type": "government_create_case"},
            {"intent": "get_case", "execution_mode": "read_only"},
            {"intent": "record_resolution", "execution_mode": "action_kernel", "governed_action_type": "government_record_resolution"},
            {"intent": "get_resolution", "execution_mode": "read_only"},
            {"intent": "record_outcome", "execution_mode": "action_kernel", "governed_action_type": "government_record_outcome"},
            {"intent": "get_outcome", "execution_mode": "read_only"},
            {"intent": "notify_applicant", "execution_mode": "action_kernel", "governed_action_type": "government_notify_applicant"},
            {"intent": "dispatch_field_service", "execution_mode": "action_kernel", "governed_action_type": "government_dispatch_field_service"},
        ]

    def handle(self, command, params, workspace_id):
        handler = getattr(self, f"_{command}", None)
        if not handler:
            return {"ok": False, "workspace_id": workspace_id, "error": f"Unknown government command: {command}"}
        return handler(params or {}, workspace_id)

    def _wrap(self, fn, params, ws):
        try:
            obj = fn(ws, params)
            return {"ok": True, "workspace_id": ws, "data": obj.model_dump(mode="json")}
        except (ValueError, TypeError, RuntimeError) as exc:
            return {"ok": False, "workspace_id": ws, "error": str(exc)}

    def _create_service(self, p, ws): return self._wrap(self.repo.create_service, p, ws)
    def _create_service_request(self, p, ws): return self._wrap(self.repo.create_request, p, ws)
    def _create_permit_case(self, p, ws): return self._wrap(self.repo.create_permit, p, ws)
    def _create_case(self, p, ws): return self._wrap(self.repo.create_case, p, ws)
    def _record_resolution(self, p, ws): return self._wrap(self.repo.record_resolution, p, ws)
    def _record_outcome(self, p, ws): return self._wrap(self.repo.record_outcome, p, ws)

    def _get_service(self, p, ws): return self._read(self.repo.get_service, p.get("service_id"), ws, "Public service not found")
    def _get_service_request(self, p, ws): return self._read(self.repo.get_request, p.get("request_id"), ws, "Service request not found")
    def _get_permit_case(self, p, ws): return self._read(self.repo.get_permit, p.get("permit_case_id"), ws, "Permit case not found")
    def _get_case(self, p, ws): return self._read(self.repo.get_case, p.get("case_id"), ws, "Public case not found")
    def _get_resolution(self, p, ws): return self._read(self.repo.get_resolution, p.get("resolution_id"), ws, "Service resolution not found")
    def _get_outcome(self, p, ws): return self._read(self.repo.get_outcome, p.get("outcome_id"), ws, "Public service outcome not found")

    @staticmethod
    def _read(fn, object_id, ws, message):
        obj = fn(ws, str(object_id or ""))
        return {"ok": obj is not None, "workspace_id": ws, "data": obj.model_dump(mode="json") if obj else None, "error": None if obj else message}

    def _update_service_request(self, p, ws):
        try:
            obj = self.repo.update_request(ws, str(p.get("request_id", "")), str(p.get("status", "")).lower())
            return {"ok": obj is not None, "workspace_id": ws, "data": obj.model_dump(mode="json") if obj else None, "error": None if obj else "Service request not found"}
        except (ValueError, RuntimeError) as exc:
            return {"ok": False, "workspace_id": ws, "error": str(exc)}

    @staticmethod
    def _deferred_external(p, ws, action_type):
        return {"ok": True, "workspace_id": ws, "status": "action_required", "execution_mode": "action_kernel", "action_type": action_type, "parameters": {k: v for k, v in p.items() if k != "workspace_id"}, "message": "External public-service side effect is connector-deferred; no provider operation occurs inside the sector pack"}

    def _notify_applicant(self, p, ws): return self._deferred_external(p, ws, "government_notify_applicant")
    def _dispatch_field_service(self, p, ws): return self._deferred_external(p, ws, "government_dispatch_field_service")
