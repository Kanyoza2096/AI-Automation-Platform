"""IT Operations repository with durable Supabase and test-friendly memory."""
import uuid
from datetime import datetime, timezone
from typing import Optional
from core.tenant_repository import require_workspace_id
from .models import Service, Incident, Deployment, IncidentUpdate, DeploymentOutcome, ServiceOutcome

class ITOperationsRepository:
    def __init__(self, supabase_client=None):
        self.supabase = supabase_client
        self._services: dict[str, Service] = {}
        self._incidents: dict[str, Incident] = {}
        self._deployments: dict[str, Deployment] = {}
        self._incident_updates: dict[str, IncidentUpdate] = {}
        self._deployment_outcomes: dict[str, DeploymentOutcome] = {}
        self._service_outcomes: dict[str, ServiceOutcome] = {}

    @staticmethod
    def _production() -> bool:
        try:
            from config.settings import settings
            import os
            mode = os.getenv("SERVICE_MODE", "").strip().lower()
            return bool(getattr(settings, "is_production", False)) and mode in {
                "full", "api", "core", "director", "meta", "condition", "workers", "monitor", "github", "chaos",
            }
        except Exception:
            return False

    def _require_durable_write(self, operation: str):
        if self.supabase is None and self._production():
            raise RuntimeError(f"durable IT operations database is required for {operation}")

    def add_service(self, workspace_id, data):
        workspace_id = require_workspace_id(workspace_id, op="add_service")
        self._require_durable_write("add_service")
        service = Service(id=f"svc-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id,
                          name=str(data.get("name", "")).strip(),
                          environment=str(data.get("environment", "production")).lower(),
                          status=str(data.get("status", "healthy")).lower())
        if not service.name: raise ValueError("service name is required")
        if self.supabase:
            self.supabase.table("it_services").insert(service.model_dump(mode="json")).execute()
        self._services[service.id] = service
        return service

    def get_service(self, workspace_id, service_id) -> Optional[Service]:
        workspace_id = require_workspace_id(workspace_id, op="get_service")
        if self.supabase:
            result = self.supabase.table("it_services").select("*").eq("workspace_id", workspace_id).eq("id", service_id).limit(1).execute()
            if result.data: return Service(**result.data[0])
        obj = self._services.get(service_id)
        return obj if obj and obj.workspace_id == workspace_id else None

    def create_incident(self, workspace_id, service_id, data):
        workspace_id = require_workspace_id(workspace_id, op="create_incident")
        self._require_durable_write("create_incident")
        if self.get_service(workspace_id, service_id) is None: raise ValueError("service not found")
        incident = Incident(id=f"inc-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, service_id=service_id,
                            title=str(data.get("title", "")).strip(), severity=str(data.get("severity", "medium")).lower())
        if not incident.title: raise ValueError("incident title is required")
        if incident.severity not in {"low", "medium", "high", "critical"}: raise ValueError("unsupported incident severity")
        if self.supabase: self.supabase.table("it_incidents").insert(incident.model_dump(mode="json")).execute()
        self._incidents[incident.id] = incident
        return incident

    def get_incident(self, workspace_id, incident_id) -> Optional[Incident]:
        workspace_id = require_workspace_id(workspace_id, op="get_incident")
        if self.supabase:
            result = self.supabase.table("it_incidents").select("*").eq("workspace_id", workspace_id).eq("id", incident_id).limit(1).execute()
            if result.data: return Incident(**result.data[0])
        obj = self._incidents.get(incident_id)
        return obj if obj and obj.workspace_id == workspace_id else None

    def update_incident(self, workspace_id, incident_id, status):
        workspace_id = require_workspace_id(workspace_id, op="update_incident")
        if status not in {"open", "acknowledged", "resolved", "closed"}: raise ValueError("unsupported incident status")
        now = datetime.now(timezone.utc)
        if self.supabase:
            result = self.supabase.table("it_incidents").update({"status": status, "updated_at": now.isoformat()}).eq("workspace_id", workspace_id).eq("id", incident_id).select("*").limit(1).execute()
            if result.data: return Incident(**result.data[0])
        obj = self._incidents.get(incident_id)
        if obj and obj.workspace_id == workspace_id:
            updated = obj.model_copy(update={"status": status, "updated_at": now})
            self._incidents[incident_id] = updated
            return updated
        return None

    def add_incident_update(self, workspace_id, incident_id, status, note=""):
        workspace_id = require_workspace_id(workspace_id, op="add_incident_update")
        self._require_durable_write("add_incident_update")
        if self.get_incident(workspace_id, incident_id) is None: raise ValueError("incident not found")
        if status not in {"open", "acknowledged", "resolved", "closed"}: raise ValueError("unsupported incident status")
        update = IncidentUpdate(id=f"iu-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, incident_id=incident_id, status=status, note=str(note or "").strip())
        if self.supabase: self.supabase.table("it_incident_updates").insert(update.model_dump(mode="json")).execute()
        self._incident_updates[update.id] = update
        self.update_incident(workspace_id, incident_id, status)
        return update

    def request_deployment(self, workspace_id, service_id, data):
        workspace_id = require_workspace_id(workspace_id, op="request_deployment")
        self._require_durable_write("request_deployment")
        if self.get_service(workspace_id, service_id) is None: raise ValueError("service not found")
        version = str(data.get("version", "")).strip()
        if not version: raise ValueError("deployment version is required")
        dep = Deployment(id=f"dep-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, service_id=service_id,
                         version=version, environment=str(data.get("environment", "production")).lower())
        if self.supabase: self.supabase.table("it_deployments").insert(dep.model_dump(mode="json")).execute()
        self._deployments[dep.id] = dep
        return dep

    def get_deployment(self, workspace_id, deployment_id) -> Optional[Deployment]:
        workspace_id = require_workspace_id(workspace_id, op="get_deployment")
        if self.supabase:
            result = self.supabase.table("it_deployments").select("*").eq("workspace_id", workspace_id).eq("id", deployment_id).limit(1).execute()
            if result.data: return Deployment(**result.data[0])
        obj = self._deployments.get(deployment_id)
        return obj if obj and obj.workspace_id == workspace_id else None

    def record_deployment_outcome(self, workspace_id, deployment_id, status, provider_reference=None, evidence=None):
        workspace_id = require_workspace_id(workspace_id, op="record_deployment_outcome")
        self._require_durable_write("record_deployment_outcome")
        if self.get_deployment(workspace_id, deployment_id) is None: raise ValueError("deployment not found")
        if status not in {"succeeded", "failed", "cancelled", "uncertain"}: raise ValueError("unsupported deployment outcome")
        outcome = DeploymentOutcome(id=f"do-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, deployment_id=deployment_id, status=status, provider_reference=provider_reference, evidence=evidence or {})
        if self.supabase: self.supabase.table("it_deployment_outcomes").insert(outcome.model_dump(mode="json")).execute()
        self._deployment_outcomes[outcome.id] = outcome
        return outcome

    def record_service_outcome(self, workspace_id, service_id, metric, value, source, evidence=None):
        workspace_id = require_workspace_id(workspace_id, op="record_service_outcome")
        self._require_durable_write("record_service_outcome")
        if self.get_service(workspace_id, service_id) is None: raise ValueError("service not found")
        if not str(metric).strip(): raise ValueError("metric is required")
        outcome = ServiceOutcome(id=f"so-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, service_id=service_id, metric=str(metric).strip(), value=float(value), source=str(source).strip(), evidence=evidence or {})
        if self.supabase: self.supabase.table("it_service_outcomes").insert(outcome.model_dump(mode="json")).execute()
        self._service_outcomes[outcome.id] = outcome
        return outcome
