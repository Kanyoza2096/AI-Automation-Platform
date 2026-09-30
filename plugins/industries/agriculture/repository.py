"""Durable Agriculture repository with tenant-scoped memory fallback for tests."""
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from core.tenant_repository import require_workspace_id
from .models import Farm, Field, CropCycle, FarmActivity, Harvest, FieldException, AgriculturalOutcome

logger = logging.getLogger("kanyoza.plugins.agriculture")


class AgricultureRepository:
    TABLES = {
        Farm: "agriculture_farms",
        Field: "agriculture_fields",
        CropCycle: "agriculture_crop_cycles",
        FarmActivity: "agriculture_activities",
        Harvest: "agriculture_harvests",
        FieldException: "agriculture_field_exceptions",
        AgriculturalOutcome: "agriculture_outcomes",
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
            raise RuntimeError(f"durable agriculture database is required for {operation}")

    def _put(self, obj):
        self._require_durable_write(f"write_{type(obj).__name__.lower()}")
        if self.supabase:
            try:
                self.supabase.table(self.TABLES[type(obj)]).insert(obj.model_dump(mode="json")).execute()
            except Exception as exc:
                logger.error("Agriculture persistence failed: %s", exc)
                raise RuntimeError("agriculture persistence failed") from exc
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
                logger.warning("Agriculture lookup failed: %s", exc)
        return None

    def create_farm(self, workspace_id: str, data: dict) -> Farm:
        workspace_id = require_workspace_id(workspace_id, op="create_farm")
        name, farm_type = str(data.get("name", "")).strip(), str(data.get("farm_type", "")).strip().lower()
        if not name or not farm_type:
            raise ValueError("farm name and farm_type are required")
        status = str(data.get("status", "active")).strip().lower()
        if status not in {"active", "paused", "retired"}:
            raise ValueError(f"unsupported farm status: {status}")
        return self._put(Farm(id=f"ag-farm-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, name=name, farm_type=farm_type, status=status))

    def get_farm(self, workspace_id: str, object_id: str) -> Optional[Farm]:
        return self._get(Farm, workspace_id, object_id)

    def create_field(self, workspace_id: str, data: dict) -> Field:
        workspace_id = require_workspace_id(workspace_id, op="create_field")
        farm_id, name = str(data.get("farm_id", "")).strip(), str(data.get("name", "")).strip()
        if not farm_id or not self.get_farm(workspace_id, farm_id):
            raise ValueError("farm not found")
        if not name:
            raise ValueError("field name is required")
        try:
            area = float(data.get("area"))
        except (TypeError, ValueError):
            raise ValueError("field area must be numeric")
        if area <= 0:
            raise ValueError("field area must be greater than zero")
        return self._put(Field(id=f"ag-field-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, farm_id=farm_id, name=name, area=area, area_unit=str(data.get("area_unit", "hectare")).strip().lower()))

    def get_field(self, workspace_id: str, object_id: str) -> Optional[Field]:
        return self._get(Field, workspace_id, object_id)

    def create_crop_cycle(self, workspace_id: str, data: dict) -> CropCycle:
        workspace_id = require_workspace_id(workspace_id, op="create_crop_cycle")
        field_id, crop, season = str(data.get("field_id", "")).strip(), str(data.get("crop", "")).strip(), str(data.get("season", "")).strip()
        if not field_id or not self.get_field(workspace_id, field_id):
            raise ValueError("field not found")
        if not crop or not season:
            raise ValueError("crop and season are required")
        status = str(data.get("status", "planned")).strip().lower()
        if status not in {"planned", "planted", "growing", "harvested", "cancelled"}:
            raise ValueError(f"unsupported crop cycle status: {status}")
        return self._put(CropCycle(id=f"ag-cycle-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, field_id=field_id, crop=crop, season=season, status=status))

    def get_crop_cycle(self, workspace_id: str, object_id: str) -> Optional[CropCycle]:
        return self._get(CropCycle, workspace_id, object_id)

    def update_crop_cycle(self, workspace_id: str, object_id: str, status: str) -> Optional[CropCycle]:
        workspace_id = require_workspace_id(workspace_id, op="update_crop_cycle")
        if status not in {"planned", "planted", "growing", "harvested", "cancelled"}:
            raise ValueError(f"unsupported crop cycle status: {status}")
        obj = self.get_crop_cycle(workspace_id, object_id)
        if obj is None:
            return None
        updated = obj.model_copy(update={"status": status, "updated_at": datetime.now(timezone.utc)})
        self._require_durable_write("update_crop_cycle")
        if self.supabase:
            try:
                self.supabase.table(self.TABLES[CropCycle]).update(updated.model_dump(mode="json")).eq("workspace_id", workspace_id).eq("id", object_id).execute()
            except Exception as exc:
                logger.error("Agriculture crop cycle update failed: %s", exc)
                raise RuntimeError("agriculture persistence failed") from exc
        self._memory[updated.id] = updated
        return updated

    def record_activity(self, workspace_id: str, data: dict) -> FarmActivity:
        workspace_id = require_workspace_id(workspace_id, op="record_farm_activity")
        field_id, activity_type = str(data.get("field_id", "")).strip(), str(data.get("activity_type", "")).strip().lower()
        if not field_id or not self.get_field(workspace_id, field_id):
            raise ValueError("field not found")
        if not activity_type:
            raise ValueError("activity_type is required")
        return self._put(FarmActivity(id=f"ag-activity-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, field_id=field_id, activity_type=activity_type, notes=str(data.get("notes", "")).strip()))

    def get_activity(self, workspace_id: str, object_id: str) -> Optional[FarmActivity]:
        return self._get(FarmActivity, workspace_id, object_id)

    def record_harvest(self, workspace_id: str, data: dict) -> Harvest:
        workspace_id = require_workspace_id(workspace_id, op="record_harvest")
        cycle_id = str(data.get("crop_cycle_id", "")).strip()
        if not cycle_id or not self.get_crop_cycle(workspace_id, cycle_id):
            raise ValueError("crop cycle not found")
        try:
            quantity = float(data.get("quantity"))
        except (TypeError, ValueError):
            raise ValueError("harvest quantity must be numeric")
        if quantity <= 0:
            raise ValueError("harvest quantity must be greater than zero")
        unit = str(data.get("unit", "")).strip().lower()
        if not unit:
            raise ValueError("harvest unit is required")
        grade = str(data.get("quality_grade", "standard")).strip().lower()
        if grade not in {"premium", "standard", "low", "rejected"}:
            raise ValueError(f"unsupported quality grade: {grade}")
        return self._put(Harvest(id=f"ag-harvest-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, crop_cycle_id=cycle_id, quantity=quantity, unit=unit, quality_grade=grade))

    def get_harvest(self, workspace_id: str, object_id: str) -> Optional[Harvest]:
        return self._get(Harvest, workspace_id, object_id)


    def record_exception(self, workspace_id: str, data: dict) -> FieldException:
        workspace_id = require_workspace_id(workspace_id, op="record_field_exception")
        field_id = str(data.get("field_id", "")).strip()
        if self.get_field(workspace_id, field_id) is None:
            raise ValueError("field not found")
        exception_type = str(data.get("exception_type", "")).strip()
        if not exception_type:
            raise ValueError("exception_type is required")
        severity = str(data.get("severity", "medium")).strip().lower()
        if severity not in {"low", "medium", "high", "critical"}:
            raise ValueError("unsupported exception severity")
        status = str(data.get("status", "open")).strip().lower()
        if status not in {"open", "resolved", "cancelled"}:
            raise ValueError("unsupported exception status")
        return self._put(FieldException(
            id=f"ag-exc-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id,
            field_id=field_id, exception_type=exception_type, severity=severity, status=status,
            resolution=str(data.get("resolution", "")).strip(),
            evidence_ref=str(data.get("evidence_ref", "")).strip(),
        ))

    def get_exception(self, workspace_id: str, exception_id: str) -> Optional[FieldException]:
        return self._get(FieldException, workspace_id, exception_id)

    def record_outcome(self, workspace_id: str, data: dict) -> AgriculturalOutcome:
        workspace_id = require_workspace_id(workspace_id, op="record_agricultural_outcome")
        reference_id = str(data.get("reference_id", "")).strip()
        outcome_type = str(data.get("outcome_type", "")).strip()
        if not reference_id or not outcome_type:
            raise ValueError("reference_id and outcome_type are required")
        reference_type = str(data.get("reference_type", "crop_cycle")).strip().lower()
        if reference_type not in {"farm", "field", "crop_cycle", "harvest", "activity"}:
            raise ValueError("unsupported outcome reference type")
        value = data.get("value")
        if value is not None:
            try:
                value = float(value)
            except (TypeError, ValueError):
                raise ValueError("outcome value must be numeric")
        return self._put(AgriculturalOutcome(
            id=f"ag-out-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id,
            reference_type=reference_type, reference_id=reference_id, outcome_type=outcome_type,
            status=str(data.get("status", "recorded")).strip().lower(),
            metric=str(data.get("metric", "")).strip(), value=value,
            unit=str(data.get("unit", "")).strip().lower(),
            evidence_ref=str(data.get("evidence_ref", "")).strip(),
        ))

    def get_outcome(self, workspace_id: str, outcome_id: str) -> Optional[AgriculturalOutcome]:
        return self._get(AgriculturalOutcome, workspace_id, outcome_id)
