"""Governed Energy & Utilities commands."""


class EnergyCommands:
    def __init__(self, repository, redis_client=None):
        self.repo = repository
        self.redis = redis_client

    def list_all(self):
        return [
            {"intent": "create_asset", "execution_mode": "action_kernel", "governed_action_type": "energy_create_asset"},
            {"intent": "get_asset", "execution_mode": "read_only"},
            {"intent": "record_meter", "execution_mode": "action_kernel", "governed_action_type": "energy_record_meter"},
            {"intent": "get_meter", "execution_mode": "read_only"},
            {"intent": "create_incident", "execution_mode": "action_kernel", "governed_action_type": "energy_create_incident"},
            {"intent": "get_incident", "execution_mode": "read_only"},
            {"intent": "update_incident", "execution_mode": "action_kernel", "governed_action_type": "energy_update_incident"},
            {"intent": "create_work_order", "execution_mode": "action_kernel", "governed_action_type": "energy_create_work_order"},
            {"intent": "get_work_order", "execution_mode": "read_only"},
            {"intent": "record_exception", "execution_mode": "action_kernel", "governed_action_type": "energy_record_exception"},
            {"intent": "get_exception", "execution_mode": "read_only"},
            {"intent": "record_outcome", "execution_mode": "action_kernel", "governed_action_type": "energy_record_outcome"},
            {"intent": "get_outcome", "execution_mode": "read_only"},
            {"intent": "dispatch_crew", "execution_mode": "action_kernel", "governed_action_type": "energy_dispatch_crew"},
            {"intent": "issue_remote_command", "execution_mode": "action_kernel", "governed_action_type": "energy_issue_remote_command"},
        ]

    def handle(self, command, params, workspace_id):
        handler = getattr(self, f"_{command}", None)
        if not handler:
            return {"ok": False, "workspace_id": workspace_id, "error": f"Unknown energy command: {command}"}
        return handler(params, workspace_id)

    def _wrap(self, fn, params, ws):
        try:
            obj = fn(ws, params)
            return {"ok": True, "workspace_id": ws, "data": obj.model_dump(mode="json")}
        except (ValueError, TypeError) as exc:
            return {"ok": False, "workspace_id": ws, "error": str(exc)}

    def _create_asset(self, p, ws): return self._wrap(self.repo.create_asset, p, ws)
    def _record_meter(self, p, ws): return self._wrap(self.repo.record_meter, p, ws)
    def _create_incident(self, p, ws): return self._wrap(self.repo.create_incident, p, ws)
    def _create_work_order(self, p, ws): return self._wrap(self.repo.create_work_order, p, ws)
    def _record_exception(self, p, ws): return self._wrap(self.repo.record_exception, p, ws)
    def _record_outcome(self, p, ws): return self._wrap(self.repo.record_outcome, p, ws)

    def _get_asset(self, p, ws): return self._read(self.repo.get_asset, p.get("asset_id"), ws, "Energy asset not found")
    def _get_meter(self, p, ws): return self._read(self.repo.get_meter, p.get("meter_reading_id"), ws, "Meter reading not found")
    def _get_incident(self, p, ws): return self._read(self.repo.get_incident, p.get("incident_id"), ws, "Incident not found")
    def _get_work_order(self, p, ws): return self._read(self.repo.get_work_order, p.get("work_order_id"), ws, "Work order not found")
    def _get_exception(self, p, ws): return self._read(self.repo.get_exception, p.get("exception_id"), ws, "Utility exception not found")
    def _get_outcome(self, p, ws): return self._read(self.repo.get_outcome, p.get("outcome_id"), ws, "Utility outcome not found")

    @staticmethod
    def _read(fn, object_id, ws, message):
        obj = fn(ws, str(object_id or ""))
        return {"ok": obj is not None, "workspace_id": ws, "data": obj.model_dump(mode="json") if obj else None, "error": None if obj else message}

    def _update_incident(self, p, ws):
        try:
            obj = self.repo.update_incident(ws, str(p.get("incident_id", "")), str(p.get("status", "")).lower())
            return {"ok": obj is not None, "workspace_id": ws, "data": obj.model_dump(mode="json") if obj else None, "error": None if obj else "Incident not found"}
        except ValueError as exc:
            return {"ok": False, "workspace_id": ws, "error": str(exc)}

    @staticmethod
    def _deferred_external(p, ws, action_type):
        return {"ok": True, "workspace_id": ws, "status": "action_required", "execution_mode": "action_kernel", "action_type": action_type, "parameters": {k: v for k, v in p.items() if k != "workspace_id"}, "message": "External utility side effect is connector-deferred; no provider operation occurs inside the sector pack"}

    def _dispatch_crew(self, p, ws): return self._deferred_external(p, ws, "energy_dispatch_crew")
    def _issue_remote_command(self, p, ws): return self._deferred_external(p, ws, "energy_issue_remote_command")
