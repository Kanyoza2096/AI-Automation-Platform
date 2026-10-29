"""Governed Agriculture / Farm Operations commands."""


class AgricultureCommands:
    def __init__(self, repository, redis_client=None):
        self.repo = repository
        self.redis = redis_client

    def list_all(self):
        return [
            {"intent": "create_farm", "execution_mode": "action_kernel", "governed_action_type": "agriculture_create_farm"},
            {"intent": "get_farm", "execution_mode": "read_only"},
            {"intent": "create_field", "execution_mode": "action_kernel", "governed_action_type": "agriculture_create_field"},
            {"intent": "get_field", "execution_mode": "read_only"},
            {"intent": "create_crop_cycle", "execution_mode": "action_kernel", "governed_action_type": "agriculture_create_crop_cycle"},
            {"intent": "get_crop_cycle", "execution_mode": "read_only"},
            {"intent": "update_crop_cycle", "execution_mode": "action_kernel", "governed_action_type": "agriculture_update_crop_cycle"},
            {"intent": "record_activity", "execution_mode": "action_kernel", "governed_action_type": "agriculture_record_activity"},
            {"intent": "get_activity", "execution_mode": "read_only"},
            {"intent": "record_harvest", "execution_mode": "action_kernel", "governed_action_type": "agriculture_record_harvest"},
            {"intent": "get_harvest", "execution_mode": "read_only"},
            {"intent": "dispatch_field_task", "execution_mode": "action_kernel", "governed_action_type": "agriculture_dispatch_field_task"},
            {"intent": "request_input_delivery", "execution_mode": "action_kernel", "governed_action_type": "agriculture_request_input_delivery"},
            {"intent": "record_exception", "execution_mode": "action_kernel", "governed_action_type": "agriculture_record_exception"},
            {"intent": "get_exception", "execution_mode": "read_only"},
            {"intent": "record_outcome", "execution_mode": "action_kernel", "governed_action_type": "agriculture_record_outcome"},
            {"intent": "get_outcome", "execution_mode": "read_only"},
        ]

    def handle(self, command, params, workspace_id):
        handler = getattr(self, f"_{command}", None)
        if not handler:
            return {"ok": False, "workspace_id": workspace_id, "error": f"Unknown agriculture command: {command}"}
        return handler(params or {}, workspace_id)

    def _wrap(self, fn, params, ws):
        try:
            obj = fn(ws, params)
            return {"ok": True, "workspace_id": ws, "data": obj.model_dump(mode="json")}
        except (ValueError, TypeError, RuntimeError) as exc:
            return {"ok": False, "workspace_id": ws, "error": str(exc)}

    def _create_farm(self, p, ws): return self._wrap(self.repo.create_farm, p, ws)
    def _create_field(self, p, ws): return self._wrap(self.repo.create_field, p, ws)
    def _create_crop_cycle(self, p, ws): return self._wrap(self.repo.create_crop_cycle, p, ws)
    def _record_activity(self, p, ws): return self._wrap(self.repo.record_activity, p, ws)
    def _record_harvest(self, p, ws): return self._wrap(self.repo.record_harvest, p, ws)
    def _record_exception(self, p, ws): return self._wrap(self.repo.record_exception, p, ws)
    def _record_outcome(self, p, ws): return self._wrap(self.repo.record_outcome, p, ws)

    def _get_farm(self, p, ws): return self._read(self.repo.get_farm, p.get("farm_id"), ws, "Farm not found")
    def _get_field(self, p, ws): return self._read(self.repo.get_field, p.get("field_id"), ws, "Field not found")
    def _get_crop_cycle(self, p, ws): return self._read(self.repo.get_crop_cycle, p.get("crop_cycle_id"), ws, "Crop cycle not found")
    def _get_activity(self, p, ws): return self._read(self.repo.get_activity, p.get("activity_id"), ws, "Farm activity not found")
    def _get_harvest(self, p, ws): return self._read(self.repo.get_harvest, p.get("harvest_id"), ws, "Harvest not found")
    def _get_exception(self, p, ws): return self._read(self.repo.get_exception, p.get("exception_id"), ws, "Field exception not found")
    def _get_outcome(self, p, ws): return self._read(self.repo.get_outcome, p.get("outcome_id"), ws, "Agricultural outcome not found")

    @staticmethod
    def _read(fn, object_id, ws, message):
        obj = fn(ws, str(object_id or ""))
        return {"ok": obj is not None, "workspace_id": ws, "data": obj.model_dump(mode="json") if obj else None, "error": None if obj else message}

    def _update_crop_cycle(self, p, ws):
        try:
            obj = self.repo.update_crop_cycle(ws, str(p.get("crop_cycle_id", "")), str(p.get("status", "")).lower())
            return {"ok": obj is not None, "workspace_id": ws, "data": obj.model_dump(mode="json") if obj else None, "error": None if obj else "Crop cycle not found"}
        except (ValueError, RuntimeError) as exc:
            return {"ok": False, "workspace_id": ws, "error": str(exc)}

    @staticmethod
    def _deferred_external(p, ws, action_type):
        return {"ok": True, "workspace_id": ws, "status": "action_required", "execution_mode": "action_kernel", "action_type": action_type, "parameters": {k: v for k, v in p.items() if k != "workspace_id"}, "message": "External agriculture side effect is connector-deferred; no provider operation occurs inside the sector pack"}

    def _dispatch_field_task(self, p, ws): return self._deferred_external(p, ws, "agriculture_dispatch_field_task")
    def _request_input_delivery(self, p, ws): return self._deferred_external(p, ws, "agriculture_request_input_delivery")
