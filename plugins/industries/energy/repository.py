"""Durable Energy repository with tenant-scoped memory fallback for tests."""
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from core.tenant_repository import require_workspace_id
from .models import UtilityAsset, MeterReading, UtilityIncident, UtilityWorkOrder, UtilityException, UtilityOutcome

logger = logging.getLogger("kanyoza.plugins.energy")


class EnergyRepository:
    TABLES = {
        UtilityAsset: "energy_assets",
        MeterReading: "energy_meter_readings",
        UtilityIncident: "energy_incidents",
        UtilityWorkOrder: "energy_work_orders",
        UtilityException: "energy_exceptions",
        UtilityOutcome: "energy_outcomes",
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
            raise RuntimeError(f"durable energy database is required for {operation}")

    def _put(self, obj):
        self._require_durable_write(f"write_{type(obj).__name__.lower()}")
        if self.supabase:
            try:
                self.supabase.table(self.TABLES[type(obj)]).insert(obj.model_dump(mode="json")).execute()
            except Exception as exc:
                logger.error("Energy persistence failed: %s", exc)
                raise RuntimeError("energy persistence failed") from exc
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
                logger.warning("Energy lookup failed: %s", exc)
        return None

    def create_asset(self, workspace_id: str, data: dict) -> UtilityAsset:
        workspace_id = require_workspace_id(workspace_id, op="create_energy_asset")
        name, asset_type = str(data.get("name", "")).strip(), str(data.get("asset_type", "")).strip().lower()
        if not name or not asset_type:
            raise ValueError("asset name and asset_type are required")
        status = str(data.get("status", "operational")).strip().lower()
        if status not in {"operational", "degraded", "offline", "maintenance"}:
            raise ValueError(f"unsupported asset status: {status}")
        return self._put(UtilityAsset(id=f"energy-asset-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, name=name, asset_type=asset_type, location=str(data.get("location", "")).strip(), status=status))

    def get_asset(self, workspace_id: str, object_id: str) -> Optional[UtilityAsset]:
        return self._get(UtilityAsset, workspace_id, object_id)

    def record_meter(self, workspace_id: str, data: dict) -> MeterReading:
        workspace_id = require_workspace_id(workspace_id, op="record_meter_reading")
        asset_id, metric, unit = str(data.get("asset_id", "")).strip(), str(data.get("metric", "")).strip(), str(data.get("unit", "")).strip()
        if not asset_id or not self.get_asset(workspace_id, asset_id):
            raise ValueError("energy asset not found")
        if not metric or not unit:
            raise ValueError("metric and unit are required")
        try:
            value = float(data.get("value"))
        except (TypeError, ValueError):
            raise ValueError("meter value must be numeric")
        return self._put(MeterReading(id=f"energy-meter-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, asset_id=asset_id, metric=metric, value=value, unit=unit))

    def get_meter(self, workspace_id: str, object_id: str) -> Optional[MeterReading]:
        return self._get(MeterReading, workspace_id, object_id)

    def create_incident(self, workspace_id: str, data: dict) -> UtilityIncident:
        workspace_id = require_workspace_id(workspace_id, op="create_energy_incident")
        asset_id, title = str(data.get("asset_id", "")).strip(), str(data.get("title", "")).strip()
        if not asset_id or not self.get_asset(workspace_id, asset_id):
            raise ValueError("energy asset not found")
        if not title:
            raise ValueError("incident title is required")
        severity = str(data.get("severity", "medium")).strip().lower()
        if severity not in {"low", "medium", "high", "critical"}:
            raise ValueError(f"unsupported incident severity: {severity}")
        return self._put(UtilityIncident(id=f"energy-inc-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, asset_id=asset_id, title=title, severity=severity))

    def get_incident(self, workspace_id: str, object_id: str) -> Optional[UtilityIncident]:
        return self._get(UtilityIncident, workspace_id, object_id)

    def update_incident(self, workspace_id: str, object_id: str, status: str) -> Optional[UtilityIncident]:
        workspace_id = require_workspace_id(workspace_id, op="update_energy_incident")
        if status not in {"open", "acknowledged", "resolved", "closed"}:
            raise ValueError(f"unsupported incident status: {status}")
        obj = self.get_incident(workspace_id, object_id)
        if obj is None:
            return None
        obj = obj.model_copy(update={"status": status, "updated_at": datetime.now(timezone.utc)})
        self._require_durable_write("update_energy_incident")
        if self.supabase:
            try:
                result = self.supabase.table(self.TABLES[UtilityIncident]).update({"status": status, "updated_at": obj.updated_at.isoformat()}).eq("workspace_id", workspace_id).eq("id", object_id).select("*").limit(1).execute()
                if result.data:
                    obj = UtilityIncident(**result.data[0])
            except Exception as exc:
                logger.error("Energy incident update failed: %s", exc)
                raise RuntimeError("energy persistence failed") from exc
        self._memory[obj.id] = obj
        return obj

    def create_work_order(self, workspace_id: str, data: dict) -> UtilityWorkOrder:
        workspace_id = require_workspace_id(workspace_id, op="create_energy_work_order")
        asset_id, description = str(data.get("asset_id", "")).strip(), str(data.get("description", "")).strip()
        if not asset_id or not self.get_asset(workspace_id, asset_id):
            raise ValueError("energy asset not found")
        if not description:
            raise ValueError("work order description is required")
        priority = str(data.get("priority", "medium")).strip().lower()
        if priority not in {"low", "medium", "high", "critical"}:
            raise ValueError(f"unsupported work order priority: {priority}")
        return self._put(UtilityWorkOrder(id=f"energy-wo-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, asset_id=asset_id, description=description, priority=priority))

    def get_work_order(self, workspace_id: str, object_id: str) -> Optional[UtilityWorkOrder]:
        return self._get(UtilityWorkOrder, workspace_id, object_id)


    def record_exception(self, workspace_id: str, data: dict) -> UtilityException:
        workspace_id = require_workspace_id(workspace_id, op="record_energy_exception")
        asset_id = str(data.get("asset_id", "")).strip()
        exception_type = str(data.get("exception_type", "")).strip()
        if not asset_id or not self.get_asset(workspace_id, asset_id):
            raise ValueError("energy asset not found")
        if not exception_type:
            raise ValueError("exception_type is required")
        severity = str(data.get("severity", "medium")).strip().lower()
        if severity not in {"low", "medium", "high", "critical"}:
            raise ValueError(f"unsupported exception severity: {severity}")
        return self._put(UtilityException(
            id=f"energy-ex-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id,
            asset_id=asset_id, exception_type=exception_type, severity=severity,
            status=str(data.get("status", "open")).strip().lower(),
            resolution=str(data.get("resolution", "")).strip(),
            evidence_ref=str(data.get("evidence_ref", "")).strip(),
        ))

    def get_exception(self, workspace_id: str, object_id: str) -> Optional[UtilityException]:
        return self._get(UtilityException, workspace_id, object_id)

    def record_outcome(self, workspace_id: str, data: dict) -> UtilityOutcome:
        workspace_id = require_workspace_id(workspace_id, op="record_energy_outcome")
        reference_type = str(data.get("reference_type", "")).strip()
        reference_id = str(data.get("reference_id", "")).strip()
        outcome_type = str(data.get("outcome_type", "")).strip()
        if not reference_type or not reference_id or not outcome_type:
            raise ValueError("reference_type, reference_id and outcome_type are required")
        if reference_type == "asset" and not self.get_asset(workspace_id, reference_id):
            raise ValueError("energy asset not found")
        if reference_type == "incident" and not self.get_incident(workspace_id, reference_id):
            raise ValueError("energy incident not found")
        if reference_type == "work_order" and not self.get_work_order(workspace_id, reference_id):
            raise ValueError("energy work order not found")
        raw_value = data.get("value")
        value = None if raw_value is None else float(raw_value)
        if value is not None and value < 0:
            raise ValueError("outcome value cannot be negative")
        return self._put(UtilityOutcome(
            id=f"energy-out-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id,
            reference_type=reference_type, reference_id=reference_id,
            outcome_type=outcome_type, status=str(data.get("status", "recorded")).strip().lower(),
            metric=str(data.get("metric", "")).strip(), value=value,
            evidence_ref=str(data.get("evidence_ref", "")).strip(),
        ))

    def get_outcome(self, workspace_id: str, object_id: str) -> Optional[UtilityOutcome]:
        return self._get(UtilityOutcome, workspace_id, object_id)
