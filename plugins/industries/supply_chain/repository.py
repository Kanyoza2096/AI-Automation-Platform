"""Durable Supply Chain repository with test-friendly memory storage."""
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from core.tenant_repository import require_workspace_id
from .models import Shipment, WarehouseItem, PurchaseOrder, ShipmentException, LogisticsOutcome

logger = logging.getLogger("kanyoza.plugins.supply_chain")


class SupplyChainRepository:
    TABLES = {
        Shipment: "supply_chain_shipments",
        WarehouseItem: "supply_chain_inventory",
        PurchaseOrder: "supply_chain_purchase_orders",
        ShipmentException: "supply_chain_exceptions",
        LogisticsOutcome: "supply_chain_outcomes",
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
            raise RuntimeError(f"durable supply-chain database is required for {operation}")

    def _put(self, obj):
        self._require_durable_write(f"write_{type(obj).__name__.lower()}")
        if self.supabase:
            try:
                self.supabase.table(self.TABLES[type(obj)]).insert(obj.model_dump(mode="json")).execute()
            except Exception as exc:
                logger.error("Supply-chain persistence failed: %s", exc)
                raise RuntimeError("supply-chain persistence failed") from exc
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
                logger.warning("Supply-chain lookup failed: %s", exc)
        return None

    def create_shipment(self, workspace_id: str, data: dict) -> Shipment:
        workspace_id = require_workspace_id(workspace_id, op="create_shipment")
        reference = str(data.get("reference", "")).strip()
        origin = str(data.get("origin", "")).strip()
        destination = str(data.get("destination", "")).strip()
        if not reference or not origin or not destination:
            raise ValueError("shipment reference, origin and destination are required")
        return self._put(Shipment(
            id=f"sc-shp-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id,
            reference=reference, origin=origin, destination=destination,
            carrier=str(data.get("carrier", "")).strip() or None,
            tracking_reference=str(data.get("tracking_reference", "")).strip() or None,
        ))

    def get_shipment(self, workspace_id: str, shipment_id: str) -> Optional[Shipment]:
        return self._get(Shipment, workspace_id, shipment_id)

    def update_shipment_status(self, workspace_id: str, shipment_id: str, status: str) -> Optional[Shipment]:
        workspace_id = require_workspace_id(workspace_id, op="update_shipment_status")
        allowed = {"planned", "picked_up", "in_transit", "delivered", "cancelled"}
        if status not in allowed:
            raise ValueError(f"unsupported shipment status: {status}")
        obj = self.get_shipment(workspace_id, shipment_id)
        if obj is None:
            return None
        obj = obj.model_copy(update={"status": status, "updated_at": datetime.now(timezone.utc)})
        self._require_durable_write("update_shipment_status")
        if self.supabase:
            try:
                result = self.supabase.table(self.TABLES[Shipment]).update({"status": status, "updated_at": obj.updated_at.isoformat()}).eq("workspace_id", workspace_id).eq("id", shipment_id).select("*").limit(1).execute()
                if result.data:
                    obj = Shipment(**result.data[0])
            except Exception as exc:
                logger.error("Shipment update failed: %s", exc)
                raise RuntimeError("shipment persistence failed") from exc
        self._memory[obj.id] = obj
        return obj

    def upsert_inventory(self, workspace_id: str, data: dict) -> WarehouseItem:
        workspace_id = require_workspace_id(workspace_id, op="upsert_inventory")
        sku = str(data.get("sku", "")).strip()
        warehouse = str(data.get("warehouse", "")).strip()
        quantity = int(data.get("quantity", 0))
        reorder_point = int(data.get("reorder_point", 0))
        if not sku or not warehouse:
            raise ValueError("sku and warehouse are required")
        if quantity < 0 or reorder_point < 0:
            raise ValueError("quantity and reorder_point cannot be negative")
        existing = next((x for x in self._memory.values() if isinstance(x, WarehouseItem) and x.workspace_id == workspace_id and x.sku == sku and x.warehouse == warehouse), None)
        if existing:
            existing = existing.model_copy(update={"quantity": quantity, "reorder_point": reorder_point, "status": "reorder_required" if quantity <= reorder_point else "available", "updated_at": datetime.now(timezone.utc)})
            self._require_durable_write("upsert_inventory")
            if self.supabase:
                try:
                    result = self.supabase.table(self.TABLES[WarehouseItem]).update(existing.model_dump(mode="json")).eq("workspace_id", workspace_id).eq("id", existing.id).select("*").limit(1).execute()
                    if result.data:
                        existing = WarehouseItem(**result.data[0])
                except Exception as exc:
                    logger.error("Inventory update failed: %s", exc)
                    raise RuntimeError("inventory persistence failed") from exc
            self._memory[existing.id] = existing
            return existing
        return self._put(WarehouseItem(
            id=f"sc-inv-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id,
            sku=sku, warehouse=warehouse, quantity=quantity, reorder_point=reorder_point,
            status="reorder_required" if quantity <= reorder_point else "available",
        ))

    def get_inventory(self, workspace_id: str, inventory_id: str) -> Optional[WarehouseItem]:
        return self._get(WarehouseItem, workspace_id, inventory_id)

    def create_purchase_order(self, workspace_id: str, data: dict) -> PurchaseOrder:
        workspace_id = require_workspace_id(workspace_id, op="create_purchase_order")
        supplier_ref = str(data.get("supplier_ref", "")).strip()
        reference = str(data.get("reference", "")).strip()
        amount = float(data.get("total_amount", 0))
        if not supplier_ref or not reference:
            raise ValueError("supplier_ref and reference are required")
        if amount <= 0:
            raise ValueError("purchase order amount must be positive")
        return self._put(PurchaseOrder(
            id=f"sc-po-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id,
            supplier_ref=supplier_ref, reference=reference, total_amount=amount,
            currency=str(data.get("currency", "MWK")).upper(),
        ))

    def get_purchase_order(self, workspace_id: str, purchase_order_id: str) -> Optional[PurchaseOrder]:
        return self._get(PurchaseOrder, workspace_id, purchase_order_id)


    def record_exception(self, workspace_id: str, data: dict) -> ShipmentException:
        workspace_id = require_workspace_id(workspace_id, op="record_shipment_exception")
        shipment_id = str(data.get("shipment_id", "")).strip()
        if self.get_shipment(workspace_id, shipment_id) is None:
            raise ValueError("shipment not found")
        exception_type = str(data.get("exception_type", "")).strip()
        if not exception_type:
            raise ValueError("exception_type is required")
        severity = str(data.get("severity", "medium")).lower()
        if severity not in {"low", "medium", "high", "critical"}:
            raise ValueError("unsupported exception severity")
        return self._put(ShipmentException(
            id=f"sc-exc-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id,
            shipment_id=shipment_id, exception_type=exception_type, severity=severity,
            status=str(data.get("status", "open")).lower(),
            resolution=str(data.get("resolution", "")),
            evidence_ref=str(data.get("evidence_ref", "")),
        ))

    def get_exception(self, workspace_id: str, exception_id: str) -> Optional[ShipmentException]:
        return self._get(ShipmentException, workspace_id, exception_id)

    def record_outcome(self, workspace_id: str, data: dict) -> LogisticsOutcome:
        workspace_id = require_workspace_id(workspace_id, op="record_logistics_outcome")
        reference_id = str(data.get("reference_id", "")).strip()
        outcome_type = str(data.get("outcome_type", "")).strip()
        if not reference_id or not outcome_type:
            raise ValueError("reference_id and outcome_type are required")
        value = data.get("value")
        if value is not None:
            value = float(value)
        return self._put(LogisticsOutcome(
            id=f"sc-out-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id,
            reference_type=str(data.get("reference_type", "shipment")), reference_id=reference_id,
            outcome_type=outcome_type, status=str(data.get("status", "recorded")),
            metric=str(data.get("metric", "")), value=value, evidence_ref=str(data.get("evidence_ref", "")),
        ))

    def get_outcome(self, workspace_id: str, outcome_id: str) -> Optional[LogisticsOutcome]:
        return self._get(LogisticsOutcome, workspace_id, outcome_id)
