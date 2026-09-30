"""Governed Supply Chain / Logistics commands."""


class SupplyChainCommands:
    def __init__(self, repository, redis_client=None):
        self.repo = repository
        self.redis = redis_client

    def list_all(self):
        return [
            {"intent": "create_shipment", "execution_mode": "action_kernel", "governed_action_type": "supply_chain_create_shipment"},
            {"intent": "get_shipment", "execution_mode": "read_only"},
            {"intent": "update_shipment_status", "execution_mode": "action_kernel", "governed_action_type": "supply_chain_update_shipment"},
            {"intent": "upsert_inventory", "execution_mode": "action_kernel", "governed_action_type": "supply_chain_update_inventory"},
            {"intent": "get_inventory", "execution_mode": "read_only"},
            {"intent": "create_purchase_order", "execution_mode": "action_kernel", "governed_action_type": "supply_chain_create_purchase_order"},
            {"intent": "get_purchase_order", "execution_mode": "read_only"},
            {"intent": "dispatch_shipment", "execution_mode": "action_kernel", "governed_action_type": "supply_chain_dispatch_shipment"},
            {"intent": "request_carrier_booking", "execution_mode": "action_kernel", "governed_action_type": "supply_chain_request_carrier_booking"},
            {"intent": "record_exception", "execution_mode": "action_kernel", "governed_action_type": "supply_chain_record_exception"},
            {"intent": "get_exception", "execution_mode": "read_only"},
            {"intent": "record_outcome", "execution_mode": "action_kernel", "governed_action_type": "supply_chain_record_outcome"},
            {"intent": "get_outcome", "execution_mode": "read_only"},
        ]

    def handle(self, command, params, workspace_id):
        handler = getattr(self, f"_{command}", None)
        if not handler:
            return {"ok": False, "workspace_id": workspace_id, "error": f"Unknown supply-chain command: {command}"}
        return handler(params, workspace_id)

    def _create_shipment(self, p, ws):
        try:
            obj = self.repo.create_shipment(ws, p)
            return {"ok": True, "workspace_id": ws, "data": obj.model_dump(mode="json")}
        except ValueError as exc:
            return {"ok": False, "workspace_id": ws, "error": str(exc)}

    def _get_shipment(self, p, ws):
        obj = self.repo.get_shipment(ws, str(p.get("shipment_id", "")))
        return {"ok": obj is not None, "workspace_id": ws, "data": obj.model_dump(mode="json") if obj else None, "error": None if obj else "Shipment not found"}

    def _update_shipment_status(self, p, ws):
        try:
            obj = self.repo.update_shipment_status(ws, str(p.get("shipment_id", "")), str(p.get("status", "")).lower())
            return {"ok": obj is not None, "workspace_id": ws, "data": {"shipment_id": obj.id, "status": obj.status} if obj else None, "error": None if obj else "Shipment not found"}
        except ValueError as exc:
            return {"ok": False, "workspace_id": ws, "error": str(exc)}

    def _upsert_inventory(self, p, ws):
        try:
            obj = self.repo.upsert_inventory(ws, p)
            return {"ok": True, "workspace_id": ws, "data": obj.model_dump(mode="json")}
        except ValueError as exc:
            return {"ok": False, "workspace_id": ws, "error": str(exc)}

    def _get_inventory(self, p, ws):
        obj = self.repo.get_inventory(ws, str(p.get("inventory_id", "")))
        return {"ok": obj is not None, "workspace_id": ws, "data": obj.model_dump(mode="json") if obj else None, "error": None if obj else "Inventory item not found"}

    def _create_purchase_order(self, p, ws):
        try:
            obj = self.repo.create_purchase_order(ws, p)
            return {"ok": True, "workspace_id": ws, "data": obj.model_dump(mode="json")}
        except ValueError as exc:
            return {"ok": False, "workspace_id": ws, "error": str(exc)}

    def _get_purchase_order(self, p, ws):
        obj = self.repo.get_purchase_order(ws, str(p.get("purchase_order_id", "")))
        return {"ok": obj is not None, "workspace_id": ws, "data": obj.model_dump(mode="json") if obj else None, "error": None if obj else "Purchase order not found"}

    def _dispatch_shipment(self, p, ws):
        return self._deferred_external(p, ws, "supply_chain_dispatch_shipment")

    def _request_carrier_booking(self, p, ws):
        return self._deferred_external(p, ws, "supply_chain_request_carrier_booking")

    def _record_exception(self, p, ws):
        try:
            obj = self.repo.record_exception(ws, p)
            return {"ok": True, "workspace_id": ws, "data": obj.model_dump(mode="json")}
        except ValueError as exc:
            return {"ok": False, "workspace_id": ws, "error": str(exc)}

    def _get_exception(self, p, ws):
        obj = self.repo.get_exception(ws, str(p.get("exception_id", "")))
        return {"ok": obj is not None, "workspace_id": ws, "data": obj.model_dump(mode="json") if obj else None, "error": None if obj else "Shipment exception not found"}

    def _record_outcome(self, p, ws):
        try:
            obj = self.repo.record_outcome(ws, p)
            return {"ok": True, "workspace_id": ws, "status": "observed", "execution_mode": "action_kernel", "data": obj.model_dump(mode="json")}
        except ValueError as exc:
            return {"ok": False, "workspace_id": ws, "error": str(exc)}

    def _get_outcome(self, p, ws):
        obj = self.repo.get_outcome(ws, str(p.get("outcome_id", "")))
        return {"ok": obj is not None, "workspace_id": ws, "data": obj.model_dump(mode="json") if obj else None, "error": None if obj else "Logistics outcome not found"}

    @staticmethod
    def _deferred_external(p, ws, action_type):
        return {"ok": True, "workspace_id": ws, "status": "action_required", "execution_mode": "action_kernel", "action_type": action_type, "parameters": {k: v for k, v in p.items() if k != "workspace_id"}, "message": "External logistics side effect is connector-deferred; no carrier operation occurs inside the sector pack"}
