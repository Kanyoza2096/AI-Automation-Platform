"""Durable Government repository with tenant-scoped memory fallback for tests."""
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from core.tenant_repository import require_workspace_id
from .models import PublicService, ServiceRequest, PermitCase, PublicCase, ServiceResolution, PublicServiceOutcome, FieldServiceProviderBinding, FieldServiceDispatch, FieldServiceReconciliationEvidence, FieldServiceOutcome

logger = logging.getLogger("kanyoza.plugins.government")


class GovernmentRepository:
    TABLES = {
        PublicService: "government_services",
        ServiceRequest: "government_service_requests",
        PermitCase: "government_permit_cases",
        PublicCase: "government_cases",
        ServiceResolution: "government_service_resolutions",
        PublicServiceOutcome: "government_service_outcomes",
        FieldServiceProviderBinding: "government_field_service_provider_bindings",
        FieldServiceDispatch: "government_field_service_dispatches",
        FieldServiceReconciliationEvidence: "government_field_service_reconciliation_evidence",
        FieldServiceOutcome: "government_field_service_outcomes",
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
            raise RuntimeError(f"durable government database is required for {operation}")

    def _put(self, obj):
        self._require_durable_write(f"write_{type(obj).__name__.lower()}")
        if self.supabase:
            try:
                self.supabase.table(self.TABLES[type(obj)]).insert(obj.model_dump(mode="json")).execute()
            except Exception as exc:
                logger.error("Government persistence failed: %s", exc)
                raise RuntimeError("government persistence failed") from exc
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
                logger.warning("Government lookup failed: %s", exc)
        return None

    def create_service(self, workspace_id: str, data: dict) -> PublicService:
        workspace_id = require_workspace_id(workspace_id, op="create_public_service")
        name, category = str(data.get("name", "")).strip(), str(data.get("category", "")).strip().lower()
        if not name or not category:
            raise ValueError("service name and category are required")
        status = str(data.get("status", "active")).strip().lower()
        if status not in {"active", "paused", "retired"}:
            raise ValueError(f"unsupported service status: {status}")
        return self._put(PublicService(id=f"gov-service-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, name=name, category=category, status=status))

    def get_service(self, workspace_id: str, object_id: str) -> Optional[PublicService]:
        return self._get(PublicService, workspace_id, object_id)

    def create_request(self, workspace_id: str, data: dict) -> ServiceRequest:
        workspace_id = require_workspace_id(workspace_id, op="create_service_request")
        service_id, title = str(data.get("service_id", "")).strip(), str(data.get("title", "")).strip()
        if not service_id or not self.get_service(workspace_id, service_id):
            raise ValueError("public service not found")
        if not title:
            raise ValueError("service request title is required")
        priority = str(data.get("priority", "medium")).strip().lower()
        if priority not in {"low", "medium", "high", "urgent"}:
            raise ValueError(f"unsupported request priority: {priority}")
        return self._put(ServiceRequest(id=f"gov-request-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, service_id=service_id, title=title, priority=priority))

    def get_request(self, workspace_id: str, object_id: str) -> Optional[ServiceRequest]:
        return self._get(ServiceRequest, workspace_id, object_id)

    def update_request(self, workspace_id: str, object_id: str, status: str) -> Optional[ServiceRequest]:
        workspace_id = require_workspace_id(workspace_id, op="update_service_request")
        if status not in {"submitted", "in_review", "in_progress", "resolved", "closed"}:
            raise ValueError(f"unsupported request status: {status}")
        obj = self._get(ServiceRequest, workspace_id, object_id)
        if obj is None:
            return None
        updated = obj.model_copy(update={"status": status, "updated_at": datetime.now(timezone.utc)})
        self._require_durable_write("update_service_request")
        if self.supabase:
            self.supabase.table(self.TABLES[ServiceRequest]).update(updated.model_dump(mode="json")).eq("workspace_id", workspace_id).eq("id", object_id).execute()
        self._memory[updated.id] = updated
        return updated

    def create_permit(self, workspace_id: str, data: dict) -> PermitCase:
        workspace_id = require_workspace_id(workspace_id, op="create_permit_case")
        service_id = str(data.get("service_id", "")).strip()
        if not service_id or not self.get_service(workspace_id, service_id):
            raise ValueError("public service not found")
        applicant_ref = str(data.get("applicant_ref", "")).strip()
        permit_type = str(data.get("permit_type", "")).strip()
        if not applicant_ref or not permit_type:
            raise ValueError("applicant_ref and permit_type are required")
        return self._put(PermitCase(id=f"gov-permit-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, service_id=service_id, applicant_ref=applicant_ref, permit_type=permit_type))

    def get_permit(self, workspace_id: str, object_id: str) -> Optional[PermitCase]:
        return self._get(PermitCase, workspace_id, object_id)

    def create_case(self, workspace_id: str, data: dict) -> PublicCase:
        workspace_id = require_workspace_id(workspace_id, op="create_public_case")
        service_id, title = str(data.get("service_id", "")).strip(), str(data.get("title", "")).strip()
        if not service_id or not self.get_service(workspace_id, service_id):
            raise ValueError("public service not found")
        if not title:
            raise ValueError("case title is required")
        return self._put(PublicCase(id=f"gov-case-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, service_id=service_id, title=title))

    def get_case(self, workspace_id: str, object_id: str) -> Optional[PublicCase]:
        return self._get(PublicCase, workspace_id, object_id)


    def record_resolution(self, workspace_id: str, data: dict) -> ServiceResolution:
        workspace_id = require_workspace_id(workspace_id, op="record_service_resolution")
        request_id = str(data.get("request_id", "")).strip()
        if not request_id or not self.get_request(workspace_id, request_id):
            raise ValueError("service request not found")
        resolution = str(data.get("resolution", "")).strip()
        if not resolution:
            raise ValueError("resolution is required")
        status = str(data.get("status", "recorded")).strip().lower()
        if status not in {"recorded", "verified", "reopened"}:
            raise ValueError(f"unsupported resolution status: {status}")
        return self._put(ServiceResolution(id=f"gov-resolution-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, request_id=request_id, status=status, resolution=resolution, evidence_ref=str(data.get("evidence_ref", "")).strip()))

    def get_resolution(self, workspace_id: str, object_id: str) -> Optional[ServiceResolution]:
        return self._get(ServiceResolution, workspace_id, object_id)

    def record_outcome(self, workspace_id: str, data: dict) -> PublicServiceOutcome:
        workspace_id = require_workspace_id(workspace_id, op="record_public_service_outcome")
        reference_id = str(data.get("reference_id", "")).strip()
        outcome_type = str(data.get("outcome_type", "")).strip()
        if not reference_id or not outcome_type:
            raise ValueError("reference_id and outcome_type are required")
        value = data.get("value")
        if value is not None:
            value = float(value)
        return self._put(PublicServiceOutcome(id=f"gov-outcome-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, reference_type=str(data.get("reference_type", "service_request")).strip(), reference_id=reference_id, outcome_type=outcome_type, status=str(data.get("status", "recorded")).strip().lower(), metric=str(data.get("metric", "")).strip(), value=value, evidence_ref=str(data.get("evidence_ref", "")).strip()))

    def get_outcome(self, workspace_id: str, object_id: str) -> Optional[PublicServiceOutcome]:
        return self._get(PublicServiceOutcome, workspace_id, object_id)

    def bind_field_service_provider(self, workspace_id: str, provider: str, metadata: dict) -> FieldServiceProviderBinding:
        workspace_id = require_workspace_id(workspace_id, op="bind_field_service_provider")
        if provider != "jira":
            raise ValueError("only jira is supported for field-service dispatch")
        return self._put(FieldServiceProviderBinding(id=f"gov-fsb-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, provider=provider, metadata=dict(metadata or {})))

    def get_field_service_provider_binding(self, workspace_id: str, provider: str = "jira") -> Optional[FieldServiceProviderBinding]:
        workspace_id = require_workspace_id(workspace_id, op="get_field_service_provider_binding")
        for obj in self._memory.values():
            if isinstance(obj, FieldServiceProviderBinding) and obj.workspace_id == workspace_id and obj.provider == provider and obj.status == "active":
                return obj
        if self.supabase:
            result = self.supabase.table(self.TABLES[FieldServiceProviderBinding]).select("*").eq("workspace_id", workspace_id).eq("provider", provider).eq("status", "active").limit(1).execute()
            if result.data:
                return FieldServiceProviderBinding(**result.data[0])
        return None

    def create_field_service_dispatch(self, workspace_id: str, request_id: str, team_id: str, title: str) -> FieldServiceDispatch:
        workspace_id = require_workspace_id(workspace_id, op="create_field_service_dispatch")
        if not self.get_request(workspace_id, request_id):
            raise ValueError("government service request not found")
        binding = self.get_field_service_provider_binding(workspace_id, "jira")
        if binding is None:
            raise ValueError("active Jira field-service provider binding is required")
        meta = dict(binding.metadata or {})
        project_key = str(meta.get("project_key", "")).strip()
        issue_type = str(meta.get("issue_type", "Task")).strip() or "Task"
        if not project_key:
            raise ValueError("Jira field-service binding requires project_key")
        return self._put(FieldServiceDispatch(id=f"gov-fsd-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, request_id=request_id, team_id=team_id, project_key=project_key, issue_type=issue_type))

    def get_field_service_dispatch(self, workspace_id: str, dispatch_id: str) -> Optional[FieldServiceDispatch]:
        return self._get(FieldServiceDispatch, workspace_id, dispatch_id)

    def record_field_service_receipt(self, workspace_id: str, dispatch_id: str, provider_request_id: Optional[str], receipt: dict, started_at=None) -> FieldServiceDispatch:
        obj = self.get_field_service_dispatch(workspace_id, dispatch_id)
        if obj is None:
            raise ValueError("field-service dispatch not found")
        meta = dict(receipt.get("metadata") or {})
        obj = obj.model_copy(update={"provider_request_id": provider_request_id, "provider_issue_key": meta.get("issue_key") or obj.provider_issue_key, "status": "awaiting_reconciliation"})
        self._require_durable_write("record_field_service_receipt")
        if self.supabase:
            self.supabase.table(self.TABLES[FieldServiceDispatch]).update(obj.model_dump(mode="json")).eq("workspace_id", workspace_id).eq("id", dispatch_id).execute()
        self._memory[obj.id] = obj
        return obj

    def record_field_service_reconciliation_evidence(self, workspace_id: str, dispatch_id: str, attempt_number: int, status: str, terminal: bool, observed: dict, verification_state: str, evidence: dict):
        obj = FieldServiceReconciliationEvidence(id=f"gov-fse-{uuid.uuid4().hex[:12]}", workspace_id=require_workspace_id(workspace_id, op="record_field_service_reconciliation_evidence"), dispatch_id=dispatch_id, attempt_number=attempt_number, status=status, terminal=terminal, observed=observed or {}, verification_state=verification_state, evidence=evidence or {})
        return self._put(obj)

    def list_field_service_reconciliation_evidence(self, workspace_id: str, dispatch_id: str, limit: int = 100):
        ws = require_workspace_id(workspace_id, op="list_field_service_reconciliation_evidence")
        items = [x for x in self._memory.values() if isinstance(x, FieldServiceReconciliationEvidence) and x.workspace_id == ws and x.dispatch_id == dispatch_id]
        items.sort(key=lambda x: x.attempt_number, reverse=True)
        return [x.model_dump(mode="json") for x in items[:limit]]

    def record_field_service_outcome(self, workspace_id: str, dispatch_id: str, status: str, evidence: dict):
        obj = self.get_field_service_dispatch(workspace_id, dispatch_id)
        if obj is None:
            raise ValueError("field-service dispatch not found")
        obj = obj.model_copy(update={"status": status})
        self._require_durable_write("record_field_service_outcome")
        if self.supabase:
            self.supabase.table(self.TABLES[FieldServiceDispatch]).update(obj.model_dump(mode="json")).eq("workspace_id", workspace_id).eq("id", dispatch_id).execute()
        self._memory[obj.id] = obj
        outcome = FieldServiceOutcome(id=f"gov-fso-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, dispatch_id=dispatch_id, status=status, evidence_ref=str(evidence.get("issue_key", "")))
        return self._put(outcome)

    def list_reconciliation_required_field_service_dispatches(self, workspace_id=None, limit=100):
        items = [x for x in self._memory.values() if isinstance(x, FieldServiceDispatch) and x.status == "awaiting_reconciliation"]
        if workspace_id is not None:
            items = [x for x in items if x.workspace_id == require_workspace_id(workspace_id, op="list_reconciliation_required_field_service_dispatches")]
        return items[:limit]
