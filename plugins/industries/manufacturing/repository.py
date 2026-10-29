"""Durable Manufacturing repository with tenant-scoped memory fallback for tests."""
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from core.tenant_repository import require_workspace_id
from .models import WorkCenter, ProductionOrder, QualityInspection, MaintenanceWorkOrder, MaterialConsumption, ManufacturingOutcome, MaintenanceProviderBinding, MaintenanceRequest, MaintenanceReconciliationEvidence

logger = logging.getLogger("kanyoza.plugins.manufacturing")


class ManufacturingRepository:
    TABLES = {
        WorkCenter: "manufacturing_work_centers",
        ProductionOrder: "manufacturing_production_orders",
        QualityInspection: "manufacturing_quality_inspections",
        MaintenanceWorkOrder: "manufacturing_maintenance_orders",
        MaterialConsumption: "manufacturing_material_consumption",
        ManufacturingOutcome: "manufacturing_outcomes",
        MaintenanceProviderBinding: "manufacturing_maintenance_provider_bindings",
        MaintenanceRequest: "manufacturing_maintenance_requests",
        MaintenanceReconciliationEvidence: "manufacturing_maintenance_reconciliation_evidence",
    }

    def __init__(self, supabase_client=None):
        self.supabase = supabase_client
        self._memory: dict[str, object] = {}

    @staticmethod
    def _production() -> bool:
        try:
            from config.settings import settings
            mode = os.getenv("SERVICE_MODE", "").strip().lower()
            return bool(getattr(settings, "is_production", False)) and mode in {
                "full", "api", "core", "director", "meta", "condition", "workers", "monitor", "github", "chaos",
            }
        except Exception:
            return False

    def _require_durable_write(self, operation: str) -> None:
        if self.supabase is None and self._production():
            raise RuntimeError(f"durable manufacturing database is required for {operation}")

    def _put(self, obj):
        self._require_durable_write(f"write_{type(obj).__name__.lower()}")
        if self.supabase:
            try:
                self.supabase.table(self.TABLES[type(obj)]).insert(obj.model_dump(mode="json")).execute()
            except Exception as exc:
                logger.error("Manufacturing persistence failed: %s", exc)
                raise RuntimeError("manufacturing persistence failed") from exc
        self._memory[obj.id] = obj
        return obj

    def _get(self, cls, workspace_id: str, object_id: str):
        workspace_id = require_workspace_id(workspace_id, op=f"get_{cls.__name__.lower()}")
        obj = self._memory.get(object_id)
        if isinstance(obj, cls) and obj.workspace_id == workspace_id:
            return obj
        if self.supabase:
            try:
                result = self.supabase.table(self.TABLES[cls]).select("*").eq("workspace_id", workspace_id).eq("id", object_id).limit(1).execute()
                if result.data:
                    return cls(**result.data[0])
            except Exception as exc:
                logger.warning("Manufacturing lookup failed: %s", exc)
        return None

    def create_work_center(self, workspace_id: str, data: dict) -> WorkCenter:
        workspace_id = require_workspace_id(workspace_id, op="create_work_center")
        name, location = str(data.get("name", "")).strip(), str(data.get("location", "")).strip()
        if not name:
            raise ValueError("work center name is required")
        return self._put(WorkCenter(
            id=f"mfg-wc-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id,
            name=name, location=location, capacity_units=int(data.get("capacity_units", 0)),
        ))

    def get_work_center(self, workspace_id: str, object_id: str) -> Optional[WorkCenter]:
        return self._get(WorkCenter, workspace_id, object_id)

    def create_production_order(self, workspace_id: str, data: dict) -> ProductionOrder:
        workspace_id = require_workspace_id(workspace_id, op="create_production_order")
        reference, sku = str(data.get("reference", "")).strip(), str(data.get("product_sku", "")).strip()
        quantity = int(data.get("quantity", 0))
        if not reference or not sku or quantity <= 0:
            raise ValueError("reference, product_sku and positive quantity are required")
        work_center_id = str(data.get("work_center_id", "")).strip() or None
        if work_center_id and not self.get_work_center(workspace_id, work_center_id):
            raise ValueError("work center not found")
        return self._put(ProductionOrder(
            id=f"mfg-po-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id,
            reference=reference, product_sku=sku, quantity=quantity, work_center_id=work_center_id,
        ))

    def get_production_order(self, workspace_id: str, object_id: str) -> Optional[ProductionOrder]:
        return self._get(ProductionOrder, workspace_id, object_id)

    def update_production_status(self, workspace_id: str, object_id: str, status: str) -> Optional[ProductionOrder]:
        workspace_id = require_workspace_id(workspace_id, op="update_production_status")
        allowed = {"planned", "released", "in_progress", "completed", "cancelled", "blocked"}
        if status not in allowed:
            raise ValueError(f"unsupported production status: {status}")
        obj = self.get_production_order(workspace_id, object_id)
        if obj is None:
            return None
        obj = obj.model_copy(update={"status": status, "updated_at": datetime.now(timezone.utc)})
        self._require_durable_write("update_production_status")
        if self.supabase:
            try:
                result = self.supabase.table(self.TABLES[ProductionOrder]).update({"status": status, "updated_at": obj.updated_at.isoformat()}).eq("workspace_id", workspace_id).eq("id", object_id).select("*").limit(1).execute()
                if result.data:
                    obj = ProductionOrder(**result.data[0])
            except Exception as exc:
                logger.error("Production status update failed: %s", exc)
                raise RuntimeError("production persistence failed") from exc
        self._memory[obj.id] = obj
        return obj

    def record_quality(self, workspace_id: str, data: dict) -> QualityInspection:
        workspace_id = require_workspace_id(workspace_id, op="record_quality")
        order_id = str(data.get("production_order_id", "")).strip()
        result = str(data.get("result", "pending")).strip().lower()
        defects = int(data.get("defect_count", 0))
        if not order_id or not self.get_production_order(workspace_id, order_id):
            raise ValueError("production order not found")
        if result not in {"pending", "passed", "failed", "rework"}:
            raise ValueError(f"unsupported quality result: {result}")
        if defects < 0:
            raise ValueError("defect_count cannot be negative")
        return self._put(QualityInspection(
            id=f"mfg-qi-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id,
            production_order_id=order_id, result=result, defect_count=defects,
            notes=str(data.get("notes", "")).strip() or None,
        ))

    def get_quality(self, workspace_id: str, object_id: str) -> Optional[QualityInspection]:
        return self._get(QualityInspection, workspace_id, object_id)


    def record_material_consumption(self, workspace_id: str, data: dict) -> MaterialConsumption:
        workspace_id = require_workspace_id(workspace_id, op="record_material_consumption")
        self._require_durable_write("record_material_consumption")
        order_id = str(data.get("production_order_id", "")).strip()
        material_sku = str(data.get("material_sku", "")).strip()
        quantity = float(data.get("quantity", 0))
        if not order_id or not self.get_production_order(workspace_id, order_id):
            raise ValueError("production order not found")
        if not material_sku or quantity <= 0:
            raise ValueError("material_sku and positive quantity are required")
        obj = MaterialConsumption(
            id=f"mfg-mc-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id,
            production_order_id=order_id, material_sku=material_sku, quantity=quantity,
            unit=str(data.get("unit", "unit")).strip() or "unit",
            evidence_ref=str(data.get("evidence_ref", "")).strip(),
        )
        return self._put(obj)

    def get_material_consumption(self, workspace_id: str, object_id: str) -> Optional[MaterialConsumption]:
        return self._get(MaterialConsumption, workspace_id, object_id)

    def record_outcome(self, workspace_id: str, data: dict) -> ManufacturingOutcome:
        workspace_id = require_workspace_id(workspace_id, op="record_manufacturing_outcome")
        self._require_durable_write("record_manufacturing_outcome")
        order_id = str(data.get("production_order_id", "")).strip()
        metric = str(data.get("metric", "")).strip()
        if not order_id or not self.get_production_order(workspace_id, order_id):
            raise ValueError("production order not found")
        if not metric:
            raise ValueError("metric is required")
        value = float(data.get("value", 0))
        planned = data.get("planned_quantity")
        produced = data.get("produced_quantity")
        scrap = data.get("scrap_quantity")
        for name, raw in (("value", value), ("planned_quantity", planned), ("produced_quantity", produced), ("scrap_quantity", scrap)):
            if raw is not None and float(raw) < 0:
                raise ValueError(f"{name} cannot be negative")
        if planned is not None and produced is not None and float(produced) > float(planned):
            raise ValueError("produced quantity cannot exceed planned quantity")
        obj = ManufacturingOutcome(
            id=f"mfg-out-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id,
            production_order_id=order_id, metric=metric, value=value,
            planned_quantity=float(planned) if planned is not None else None,
            produced_quantity=float(produced) if produced is not None else None,
            scrap_quantity=float(scrap) if scrap is not None else None,
            evidence_ref=str(data.get("evidence_ref", "")).strip(),
        )
        return self._put(obj)

    def get_outcome(self, workspace_id: str, object_id: str) -> Optional[ManufacturingOutcome]:
        return self._get(ManufacturingOutcome, workspace_id, object_id)

    def create_maintenance(self, workspace_id: str, data: dict) -> MaintenanceWorkOrder:
        workspace_id = require_workspace_id(workspace_id, op="create_maintenance")
        work_center_id = str(data.get("work_center_id", "")).strip()
        description = str(data.get("description", "")).strip()
        priority = str(data.get("priority", "medium")).strip().lower()
        if not work_center_id or not self.get_work_center(workspace_id, work_center_id):
            raise ValueError("work center not found")
        if not description:
            raise ValueError("maintenance description is required")
        if priority not in {"low", "medium", "high", "critical"}:
            raise ValueError(f"unsupported maintenance priority: {priority}")
        return self._put(MaintenanceWorkOrder(
            id=f"mfg-mwo-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id,
            work_center_id=work_center_id, description=description, priority=priority,
        ))

    def get_maintenance(self, workspace_id: str, object_id: str) -> Optional[MaintenanceWorkOrder]:
        return self._get(MaintenanceWorkOrder, workspace_id, object_id)


    def bind_maintenance_provider(self, workspace_id: str, provider: str, metadata: dict) -> MaintenanceProviderBinding:
        workspace_id = require_workspace_id(workspace_id, op="bind_maintenance_provider")
        if provider != "jira": raise ValueError("only jira is supported for maintenance requests")
        binding = MaintenanceProviderBinding(id=f"mfg-bind-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, provider=provider, metadata=dict(metadata or {}))
        return self._put(binding)

    def get_maintenance_provider_binding(self, workspace_id: str, provider: str = "jira") -> Optional[MaintenanceProviderBinding]:
        workspace_id = require_workspace_id(workspace_id, op="get_maintenance_provider_binding")
        for obj in self._memory.values():
            if isinstance(obj, MaintenanceProviderBinding) and obj.workspace_id == workspace_id and obj.provider == provider and obj.status == "active": return obj
        if self.supabase:
            result = self.supabase.table(self.TABLES[MaintenanceProviderBinding]).select("*").eq("workspace_id", workspace_id).eq("provider", provider).eq("status", "active").limit(1).execute()
            if result.data: return MaintenanceProviderBinding(**result.data[0])
        return None

    def create_maintenance_request(self, workspace_id: str, maintenance_id: str, data: dict) -> MaintenanceRequest:
        workspace_id = require_workspace_id(workspace_id, op="create_maintenance_request")
        if not self.get_maintenance(workspace_id, maintenance_id): raise ValueError("maintenance work order not found")
        return self._put(MaintenanceRequest(id=f"mfg-mr-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, maintenance_id=maintenance_id, project_key=str(data.get("project_key","")), issue_type=str(data.get("issue_type","Task")), reason=str(data.get("reason",""))))

    def get_maintenance_request(self, workspace_id: str, request_id: str) -> Optional[MaintenanceRequest]:
        return self._get(MaintenanceRequest, workspace_id, request_id)

    def record_maintenance_request_receipt(self, workspace_id: str, request_id: str, provider_request_id: Optional[str], receipt: dict, started_at=None) -> MaintenanceRequest:
        obj = self.get_maintenance_request(workspace_id, request_id)
        if obj is None: raise ValueError("maintenance request not found")
        meta = dict(receipt.get("metadata") or {})
        obj = obj.model_copy(update={"provider_request_id": provider_request_id, "provider_issue_key": meta.get("issue_key") or obj.provider_issue_key, "status":"awaiting_reconciliation"})
        self._require_durable_write("record_maintenance_request_receipt")
        if self.supabase: self.supabase.table(self.TABLES[MaintenanceRequest]).update(obj.model_dump(mode="json")).eq("workspace_id", workspace_id).eq("id", request_id).execute()
        self._memory[obj.id]=obj; return obj

    def record_maintenance_request_outcome(self, workspace_id: str, request_id: str, status: str, evidence: dict):
        obj=self.get_maintenance_request(workspace_id, request_id)
        if obj is None: raise ValueError("maintenance request not found")
        obj=obj.model_copy(update={"status":status})
        self._require_durable_write("record_maintenance_request_outcome")
        if self.supabase: self.supabase.table(self.TABLES[MaintenanceRequest]).update(obj.model_dump(mode="json")).eq("workspace_id",workspace_id).eq("id",request_id).execute()
        self._memory[obj.id]=obj; return obj

    def record_maintenance_reconciliation_evidence(self, workspace_id: str, request_id: str, attempt_number: int, status: str, terminal: bool, observed: dict, verification_state: str, evidence: dict):
        obj=MaintenanceReconciliationEvidence(id=f"mfg-mre-{uuid.uuid4().hex[:12]}",workspace_id=require_workspace_id(workspace_id,op="record_maintenance_reconciliation_evidence"),request_id=request_id,attempt_number=attempt_number,status=status,terminal=terminal,observed=observed or {},verification_state=verification_state,evidence=evidence or {})
        return self._put(obj)

    def list_maintenance_reconciliation_evidence(self, workspace_id: str, request_id: str, limit: int = 100):
        ws=require_workspace_id(workspace_id,op="list_maintenance_reconciliation_evidence")
        items=[x for x in self._memory.values() if isinstance(x,MaintenanceReconciliationEvidence) and x.workspace_id==ws and x.request_id==request_id]
        items.sort(key=lambda x:x.attempt_number,reverse=True); return [x.model_dump(mode="json") for x in items[:limit]]

    def list_reconciliation_required_maintenance_requests(self, workspace_id=None, limit=100):
        items=[x for x in self._memory.values() if isinstance(x,MaintenanceRequest) and x.status=="awaiting_reconciliation"]
        if workspace_id is not None: items=[x for x in items if x.workspace_id==require_workspace_id(workspace_id,op="list_reconciliation_required_maintenance_requests")]
        return items[:limit]
