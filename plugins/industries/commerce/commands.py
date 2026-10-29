"""Commerce sector completion command surface."""
from .repository import CommerceRepository


class CommerceCommands:
    def __init__(self, repository: CommerceRepository, redis_client=None):
        self.repo = repository
        self.redis = redis_client

    def list_all(self) -> list[dict]:
        return [
            {"intent": "add_product", "description": "Create a product/SKU", "params": ["name", "sku", "price", "inventory_quantity"], "category": "catalog", "execution_mode": "action_kernel", "governed_action_type": "commerce_add_product"},
            {"intent": "add_customer", "description": "Create a commerce customer", "params": ["name", "email", "phone"], "category": "customers", "execution_mode": "action_kernel", "governed_action_type": "commerce_add_customer"},
            {"intent": "create_order", "description": "Create an order for an existing customer", "params": ["customer_id", "items"], "category": "orders", "execution_mode": "action_kernel", "governed_action_type": "commerce_create_order"},
            {"intent": "get_order", "description": "Read an order", "params": ["order_id"], "category": "orders", "execution_mode": "read_only"},
            {"intent": "update_order_status", "description": "Advance or cancel an order", "params": ["order_id", "status"], "category": "orders", "execution_mode": "action_kernel", "governed_action_type": "commerce_update_order_status"},
            {"intent": "reserve_inventory", "description": "Reserve inventory for an order", "params": ["order_id"], "category": "inventory", "execution_mode": "action_kernel", "governed_action_type": "commerce_reserve_inventory"},
            {"intent": "capture_payment", "description": "Request payment capture through a governed connector", "params": ["order_id", "amount", "currency"], "category": "payments", "execution_mode": "action_kernel", "governed_action_type": "commerce_capture_payment"},
            {"intent": "record_payment", "description": "Record an authoritative payment provider outcome", "params": ["order_id", "amount", "currency", "status", "provider_reference"], "category": "payments", "execution_mode": "action_kernel", "governed_action_type": "commerce_record_payment"},
            {"intent": "fulfill_order", "description": "Request order fulfillment through a governed connector", "params": ["order_id"], "category": "fulfillment", "execution_mode": "action_kernel", "governed_action_type": "commerce_fulfill_order"},
            {"intent": "record_fulfillment", "description": "Record fulfillment provider outcome", "params": ["order_id", "status", "carrier", "tracking_reference"], "category": "fulfillment", "execution_mode": "action_kernel", "governed_action_type": "commerce_record_fulfillment"},
            {"intent": "record_outcome", "description": "Record an observed commerce outcome", "params": ["order_id", "metric", "value", "source", "evidence"], "category": "analytics", "execution_mode": "action_kernel", "governed_action_type": "commerce_record_outcome"},
        ]

    def handle(self, command: str, params: dict, workspace_id: str) -> dict:
        handlers = {
            "add_product": self._add_product, "add_customer": self._add_customer, "create_order": self._create_order,
            "get_order": self._get_order, "update_order_status": self._update_order_status, "reserve_inventory": self._reserve_inventory,
            "capture_payment": self._deferred_external, "record_payment": self._record_payment,
            "fulfill_order": self._deferred_external, "record_fulfillment": self._record_fulfillment, "record_outcome": self._record_outcome,
        }
        handler = handlers.get(command)
        if not handler:
            return {"ok": False, "error": f"Unknown commerce command: {command}"}
        if command in {"capture_payment", "fulfill_order"}:
            return handler(params, workspace_id, command)
        return handler(params, workspace_id)

    def _add_product(self, p, w):
        x=self.repo.add_product(w,p); return {"ok":True,"message":f"Product '{x.name}' created","data":x.model_dump(mode="json")}
    def _add_customer(self, p, w):
        x=self.repo.add_customer(w,p); return {"ok":True,"message":f"Customer '{x.name}' created","data":x.model_dump(mode="json")}
    def _create_order(self, p, w):
        x=self.repo.create_order(w,str(p.get("customer_id","")),p.get("items") or []); return {"ok":True,"message":f"Order '{x.id}' created","data":x.model_dump(mode="json")}
    def _get_order(self,p,w):
        x=self.repo.get_order(w,str(p.get("order_id",""))); return {"ok":bool(x),"data":x.model_dump(mode="json") if x else None,"error":None if x else "Order not found"}
    def _update_order_status(self,p,w):
        x=self.repo.update_order_status(w,str(p.get("order_id","")),str(p.get("status","")).lower()); return {"ok":bool(x),"data":x.model_dump(mode="json") if x else None,"error":None if x else "Order not found"}
    def _reserve_inventory(self,p,w):
        xs=self.repo.reserve_inventory(w,str(p.get("order_id",""))); return {"ok":True,"status":"reserved","execution_mode":"action_kernel","data":[x.model_dump(mode="json") for x in xs]}
    def _record_payment(self,p,w):
        x=self.repo.record_payment(w,p); return {"ok":True,"status":"recorded","execution_mode":"action_kernel","data":x.model_dump(mode="json")}
    def _record_fulfillment(self,p,w):
        x=self.repo.record_fulfillment(w,p); return {"ok":True,"status":"recorded","execution_mode":"action_kernel","data":x.model_dump(mode="json")}
    def _record_outcome(self,p,w):
        x=self.repo.record_outcome(w,p); return {"ok":True,"status":"observed","execution_mode":"action_kernel","data":x.model_dump(mode="json")}
    def _deferred_external(self,p,w,command):
        return {"ok":True,"status":"action_required","execution_mode":"action_kernel","workspace_id":w,"action_type":command,"parameters":{k:v for k,v in p.items() if k!="workspace_id"},"message":"External commerce side effect will be implemented in the connector layer."}
