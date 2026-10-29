"""Governed Manufacturing / Industrial Operations commands."""


class ManufacturingCommands:
    def __init__(self, repository, redis_client=None):
        self.repo = repository
        self.redis = redis_client

    def list_all(self):
        return [
            {"intent": "create_work_center", "execution_mode": "action_kernel", "governed_action_type": "manufacturing_create_work_center"},
            {"intent": "get_work_center", "execution_mode": "read_only"},
            {"intent": "create_production_order", "execution_mode": "action_kernel", "governed_action_type": "manufacturing_create_production_order"},
            {"intent": "get_production_order", "execution_mode": "read_only"},
            {"intent": "update_production_status", "execution_mode": "action_kernel", "governed_action_type": "manufacturing_update_production"},
            {"intent": "record_quality", "execution_mode": "action_kernel", "governed_action_type": "manufacturing_record_quality"},
            {"intent": "get_quality", "execution_mode": "read_only"},
            {"intent": "create_maintenance", "execution_mode": "action_kernel", "governed_action_type": "manufacturing_create_maintenance"},
            {"intent": "get_maintenance", "execution_mode": "read_only"},
            {"intent": "record_material_consumption", "execution_mode": "action_kernel", "governed_action_type": "manufacturing_record_material_consumption"},
            {"intent": "get_material_consumption", "execution_mode": "read_only"},
            {"intent": "record_production_outcome", "execution_mode": "action_kernel", "governed_action_type": "manufacturing_record_outcome"},
            {"intent": "get_production_outcome", "execution_mode": "read_only"},
            {"intent": "release_production", "execution_mode": "action_kernel", "governed_action_type": "manufacturing_release_production"},
            {"intent": "request_maintenance", "execution_mode": "action_kernel", "governed_action_type": "manufacturing_request_maintenance"},
        ]

    def handle(self, command, params, workspace_id):
        handler = getattr(self, f"_{command}", None)
        if not handler:
            return {"ok": False, "workspace_id": workspace_id, "error": f"Unknown manufacturing command: {command}"}
        return handler(params, workspace_id)

    def _create_work_center(self, p, ws):
        try:
            obj = self.repo.create_work_center(ws, p)
            return {"ok": True, "workspace_id": ws, "data": obj.model_dump(mode="json")}
        except (ValueError, TypeError) as exc:
            return {"ok": False, "workspace_id": ws, "error": str(exc)}

    def _get_work_center(self, p, ws):
        obj = self.repo.get_work_center(ws, str(p.get("work_center_id", "")))
        return {"ok": obj is not None, "workspace_id": ws, "data": obj.model_dump(mode="json") if obj else None, "error": None if obj else "Work center not found"}

    def _create_production_order(self, p, ws):
        try:
            obj = self.repo.create_production_order(ws, p)
            return {"ok": True, "workspace_id": ws, "data": obj.model_dump(mode="json")}
        except (ValueError, TypeError) as exc:
            return {"ok": False, "workspace_id": ws, "error": str(exc)}

    def _get_production_order(self, p, ws):
        obj = self.repo.get_production_order(ws, str(p.get("production_order_id", "")))
        return {"ok": obj is not None, "workspace_id": ws, "data": obj.model_dump(mode="json") if obj else None, "error": None if obj else "Production order not found"}

    def _update_production_status(self, p, ws):
        try:
            obj = self.repo.update_production_status(ws, str(p.get("production_order_id", "")), str(p.get("status", "")).lower())
            return {"ok": obj is not None, "workspace_id": ws, "data": {"production_order_id": obj.id, "status": obj.status} if obj else None, "error": None if obj else "Production order not found"}
        except ValueError as exc:
            return {"ok": False, "workspace_id": ws, "error": str(exc)}

    def _record_quality(self, p, ws):
        try:
            obj = self.repo.record_quality(ws, p)
            return {"ok": True, "workspace_id": ws, "data": obj.model_dump(mode="json")}
        except (ValueError, TypeError) as exc:
            return {"ok": False, "workspace_id": ws, "error": str(exc)}

    def _get_quality(self, p, ws):
        obj = self.repo.get_quality(ws, str(p.get("quality_inspection_id", "")))
        return {"ok": obj is not None, "workspace_id": ws, "data": obj.model_dump(mode="json") if obj else None, "error": None if obj else "Quality inspection not found"}

    def _create_maintenance(self, p, ws):
        try:
            obj = self.repo.create_maintenance(ws, p)
            return {"ok": True, "workspace_id": ws, "data": obj.model_dump(mode="json")}
        except (ValueError, TypeError) as exc:
            return {"ok": False, "workspace_id": ws, "error": str(exc)}

    def _get_maintenance(self, p, ws):
        obj = self.repo.get_maintenance(ws, str(p.get("maintenance_id", "")))
        return {"ok": obj is not None, "workspace_id": ws, "data": obj.model_dump(mode="json") if obj else None, "error": None if obj else "Maintenance work order not found"}

    def _record_material_consumption(self, p, ws):
        try:
            obj = self.repo.record_material_consumption(ws, p)
            return {"ok": True, "workspace_id": ws, "data": obj.model_dump(mode="json")}
        except ValueError as exc:
            return {"ok": False, "workspace_id": ws, "error": str(exc)}

    def _get_material_consumption(self, p, ws):
        obj = self.repo.get_material_consumption(ws, str(p.get("material_consumption_id", "")))
        return {"ok": obj is not None, "workspace_id": ws, "data": obj.model_dump(mode="json") if obj else None, "error": None if obj else "Material consumption not found"}

    def _record_production_outcome(self, p, ws):
        try:
            obj = self.repo.record_outcome(ws, p)
            return {"ok": True, "workspace_id": ws, "data": obj.model_dump(mode="json")}
        except ValueError as exc:
            return {"ok": False, "workspace_id": ws, "error": str(exc)}

    def _get_production_outcome(self, p, ws):
        obj = self.repo.get_outcome(ws, str(p.get("outcome_id", "")))
        return {"ok": obj is not None, "workspace_id": ws, "data": obj.model_dump(mode="json") if obj else None, "error": None if obj else "Manufacturing outcome not found"}

    @staticmethod
    def _deferred_external(p, ws, action_type):
        return {"ok": True, "workspace_id": ws, "status": "action_required", "execution_mode": "action_kernel", "action_type": action_type, "parameters": {k: v for k, v in p.items() if k != "workspace_id"}, "message": "External industrial side effect is connector-deferred; no provider operation occurs inside the sector pack"}

    def _release_production(self, p, ws):
        return self._deferred_external(p, ws, "manufacturing_release_production")

    def _request_maintenance(self, p, ws):
        return self._deferred_external(p, ws, "manufacturing_request_maintenance")
