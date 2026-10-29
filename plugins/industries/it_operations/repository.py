"""IT Operations repository with durable Supabase and test-friendly memory."""
import uuid
from datetime import datetime, timezone
from typing import Optional
from core.tenant_repository import require_workspace_id
from .models import Service, Incident, Deployment, IncidentUpdate, DeploymentOutcome, DeploymentReconciliationEvidence, ServiceOutcome, ServiceProviderBinding, DeploymentVersionReference, ServiceRemediation, ServiceRemediationReconciliationEvidence

class ITOperationsRepository:
    TABLES = {
        ServiceRemediation: "it_service_remediations",
        ServiceRemediationReconciliationEvidence: "it_service_remediation_reconciliation_evidence",
    }
    def __init__(self, supabase_client=None):
        self.supabase = supabase_client
        self._services: dict[str, Service] = {}
        self._incidents: dict[str, Incident] = {}
        self._deployments: dict[str, Deployment] = {}
        self._incident_updates: dict[str, IncidentUpdate] = {}
        self._deployment_outcomes: dict[str, DeploymentOutcome] = {}
        self._service_outcomes: dict[str, ServiceOutcome] = {}
        self._provider_bindings: dict[str, ServiceProviderBinding] = {}
        self._version_refs: dict[str, DeploymentVersionReference] = {}
        self._reconciliation_evidence: dict[str, DeploymentReconciliationEvidence] = {}
        self._remediations: dict[str, ServiceRemediation] = {}
        self._remediation_evidence: dict[str, ServiceRemediationReconciliationEvidence] = {}

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

    def bind_service_provider(self, workspace_id, service_id, data):
        workspace_id = require_workspace_id(workspace_id, op="bind_service_provider")
        self._require_durable_write("bind_service_provider")
        service = self.get_service(workspace_id, service_id)
        if service is None: raise ValueError("service not found")
        provider = str(data.get("provider", "")).strip().lower()
        provider_service_id = str(data.get("provider_service_id", "")).strip()
        environment = str(data.get("environment", service.environment)).strip().lower()
        if not provider or not provider_service_id: raise ValueError("provider and provider_service_id are required")
        if environment != service.environment: raise ValueError("provider binding environment must match service environment")
        binding = ServiceProviderBinding(id=f"spb-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, service_id=service_id, provider=provider, provider_service_id=provider_service_id, environment=environment, metadata=data.get("metadata") or {})
        if self.supabase: self.supabase.table("it_service_provider_bindings").insert(binding.model_dump(mode="json")).execute()
        self._provider_bindings[binding.id] = binding
        return binding

    def get_service_provider_binding(self, workspace_id, service_id, provider="render") -> Optional[ServiceProviderBinding]:
        workspace_id = require_workspace_id(workspace_id, op="get_service_provider_binding")
        provider = str(provider).strip().lower()
        if self.supabase:
            result = self.supabase.table("it_service_provider_bindings").select("*").eq("workspace_id", workspace_id).eq("service_id", service_id).eq("provider", provider).eq("status", "active").limit(1).execute()
            if result.data: return ServiceProviderBinding(**result.data[0])
        candidates=[x for x in self._provider_bindings.values() if x.workspace_id==workspace_id and x.service_id==service_id and x.provider==provider and x.status=="active"]
        return candidates[0] if candidates else None

    def bind_deployment_version(self, workspace_id, service_id, data):
        workspace_id = require_workspace_id(workspace_id, op="bind_deployment_version")
        self._require_durable_write("bind_deployment_version")
        if self.get_service(workspace_id, service_id) is None: raise ValueError("service not found")
        version = str(data.get("version", "")).strip()
        provider = str(data.get("provider", "render")).strip().lower()
        provider_version_ref = str(data.get("provider_version_ref", "")).strip()
        if not version or not provider_version_ref: raise ValueError("version and provider_version_ref are required")
        ref = DeploymentVersionReference(id=f"dvr-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, service_id=service_id, version=version, provider=provider, provider_version_ref=provider_version_ref)
        if self.supabase: self.supabase.table("it_deployment_version_references").insert(ref.model_dump(mode="json")).execute()
        self._version_refs[ref.id] = ref
        return ref

    def get_deployment_version_reference(self, workspace_id, service_id, version, provider="render") -> Optional[DeploymentVersionReference]:
        workspace_id = require_workspace_id(workspace_id, op="get_deployment_version_reference")
        provider = str(provider).strip().lower(); version = str(version).strip()
        if self.supabase:
            result = self.supabase.table("it_deployment_version_references").select("*").eq("workspace_id", workspace_id).eq("service_id", service_id).eq("version", version).eq("provider", provider).limit(1).execute()
            if result.data: return DeploymentVersionReference(**result.data[0])
        candidates=[x for x in self._version_refs.values() if x.workspace_id==workspace_id and x.service_id==service_id and x.version==version and x.provider==provider]
        return candidates[0] if candidates else None

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


    def create_service_remediation(self, workspace_id, service_id, data):
        workspace_id = require_workspace_id(workspace_id, op="create_service_remediation")
        self._require_durable_write("create_service_remediation")
        service = self.get_service(workspace_id, service_id)
        if service is None:
            raise ValueError("service not found")
        owner = str(data.get("owner", "")).strip()
        repo = str(data.get("repo", "")).strip()
        workflow_id = str(data.get("workflow_id", "")).strip()
        ref = str(data.get("ref", "")).strip()
        reason = str(data.get("reason", "")).strip()
        if not owner or not repo or not workflow_id or not ref or not reason:
            raise ValueError("owner, repo, workflow_id, ref and reason are required")
        obj = ServiceRemediation(id=f"rem-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, service_id=service_id, owner=owner, repo=repo, workflow_id=workflow_id, ref=ref, reason=reason)
        if self.supabase:
            self.supabase.table(self.TABLES[ServiceRemediation]).insert(obj.model_dump(mode="json")).execute()
        self._remediations[obj.id] = obj
        return obj

    def get_service_remediation(self, workspace_id, remediation_id):
        workspace_id = require_workspace_id(workspace_id, op="get_service_remediation")
        if self.supabase:
            result = self.supabase.table(self.TABLES[ServiceRemediation]).select("*").eq("workspace_id", workspace_id).eq("id", remediation_id).limit(1).execute()
            if result.data: return ServiceRemediation(**result.data[0])
        obj = getattr(self, "_remediations", {}).get(remediation_id)
        return obj if obj and obj.workspace_id == workspace_id else None

    def record_service_remediation_receipt(self, workspace_id, remediation_id, provider_request_id, evidence):
        workspace_id = require_workspace_id(workspace_id, op="record_service_remediation_receipt")
        obj = self.get_service_remediation(workspace_id, remediation_id)
        if obj is None: raise ValueError("remediation not found in workspace")
        updated = obj.model_copy(update={"provider_request_id": provider_request_id, "status": "uncertain"})
        self._require_durable_write("record_service_remediation_receipt")
        if self.supabase:
            self.supabase.table(self.TABLES[ServiceRemediation]).update(updated.model_dump(mode="json")).eq("workspace_id", workspace_id).eq("id", remediation_id).execute()
        self._remediations[updated.id] = updated
        return updated

    def record_service_remediation_outcome(self, workspace_id, remediation_id, status, evidence):
        workspace_id = require_workspace_id(workspace_id, op="record_service_remediation_outcome")
        obj = self.get_service_remediation(workspace_id, remediation_id)
        if obj is None: raise ValueError("remediation not found in workspace")
        if status not in {"succeeded", "failed", "operator_review"}: raise ValueError("invalid remediation terminal status")
        updated = obj.model_copy(update={"status": status})
        self._require_durable_write("record_service_remediation_outcome")
        if self.supabase:
            self.supabase.table(self.TABLES[ServiceRemediation]).update(updated.model_dump(mode="json")).eq("workspace_id", workspace_id).eq("id", remediation_id).execute()
        self._remediations[updated.id] = updated
        return updated

    def record_service_remediation_reconciliation_evidence(self, workspace_id, remediation_id, service_id, provider, owner, repo, workflow_id, ref, attempt_number, status, terminal, observed, verification_state, evidence):
        workspace_id = require_workspace_id(workspace_id, op="record_service_remediation_reconciliation_evidence")
        self._require_durable_write("record_service_remediation_reconciliation_evidence")
        row = ServiceRemediationReconciliationEvidence(id=f"rem-evidence-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, remediation_id=remediation_id, service_id=service_id, provider=provider, owner=owner, repo=repo, workflow_id=workflow_id, ref=ref, attempt_number=int(attempt_number), status=status, terminal=bool(terminal), observed=observed or {}, verification_state=verification_state, evidence=evidence or {})
        if self.supabase:
            self.supabase.table(self.TABLES[ServiceRemediationReconciliationEvidence]).insert(row.model_dump(mode="json")).execute()
        self._remediation_evidence[row.id] = row
        return row

    def list_service_remediation_reconciliation_evidence(self, workspace_id, remediation_id, limit=100):
        workspace_id = require_workspace_id(workspace_id, op="list_service_remediation_reconciliation_evidence")
        limit = max(1, min(int(limit), 500))
        if self.supabase:
            result = self.supabase.table(self.TABLES[ServiceRemediationReconciliationEvidence]).select("*").eq("workspace_id", workspace_id).eq("remediation_id", remediation_id).order("attempt_number", desc=True).limit(limit).execute()
            return [ServiceRemediationReconciliationEvidence(**x) for x in (result.data or [])]
        rows = [x for x in self._remediation_evidence.values() if x.workspace_id == workspace_id and x.remediation_id == remediation_id]
        return sorted(rows, key=lambda x: x.attempt_number, reverse=True)[:limit]

    def list_reconciliation_required_service_remediations(self, workspace_id=None, limit=100):
        if workspace_id is not None: workspace_id = require_workspace_id(workspace_id, op="list_reconciliation_required_service_remediations")
        limit = max(1, min(int(limit), 500))
        rows = []
        candidates = []
        if self.supabase:
            q = self.supabase.table(self.TABLES[ServiceRemediation]).select("*").eq("status", "uncertain").order("created_at", desc=False).limit(limit)
            if workspace_id is not None: q = q.eq("workspace_id", workspace_id)
            candidates = [ServiceRemediation(**x) for x in (q.execute().data or [])]
        else:
            candidates = [x for x in self._remediations.values() if x.status == "uncertain" and (workspace_id is None or x.workspace_id == workspace_id)]
        for obj in candidates:
            evidence = self.list_service_remediation_reconciliation_evidence(obj.workspace_id, obj.id, limit=500)
            if evidence and evidence[0].verification_state == "operator_review": continue
            rows.append(obj)
            if len(rows) >= limit: break
        return rows

    def list_reconciliation_required_deployments(self, workspace_id=None, limit=100):
        """Return deployments whose latest durable outcome is still uncertain.

        Discovery is read-only and workspace-scoped. The repository owns the
        durable lookup; reconciliation orchestration decides what to do with
        the returned identities. No provider call or retry occurs here.
        """
        limit = max(1, min(int(limit), 500))
        if workspace_id is not None:
            workspace_id = require_workspace_id(workspace_id, op="list_reconciliation_required_deployments")

        if self.supabase:
            query = self.supabase.table("it_deployment_outcomes").select(
                "deployment_id,workspace_id,status,provider_reference,evidence,observed_at"
            ).eq("status", "uncertain").order("observed_at", desc=True).limit(limit)
            if workspace_id is not None:
                query = query.eq("workspace_id", workspace_id)
            result = query.execute()
            rows = result.data or []
            latest = {}
            for row in rows:
                key = (str(row.get("workspace_id")), str(row.get("deployment_id")))
                latest.setdefault(key, row)
            pending = []
            for row in latest.values():
                if (row.get("evidence") or {}).get("reconciliation_state") == "operator_review":
                    continue
                deployment = self.get_deployment(row["workspace_id"], row["deployment_id"])
                if deployment is None:
                    continue
                pending.append({
                    "workspace_id": row["workspace_id"],
                    "deployment_id": row["deployment_id"],
                    "service_id": deployment.service_id,
                    "provider_reference": row.get("provider_reference"),
                    "status": row.get("status"),
                    "evidence": row.get("evidence") or {},
                    "reconciliation_attempts": len(self.list_reconciliation_evidence(row["workspace_id"], row["deployment_id"], limit=500)),
                })
                if len(pending) >= limit:
                    break
            return pending

        pending = []
        latest = {}
        for outcome in self._deployment_outcomes.values():
            if workspace_id is not None and outcome.workspace_id != workspace_id:
                continue
            key = (outcome.workspace_id, outcome.deployment_id)
            previous = latest.get(key)
            if previous is None or outcome.observed_at > previous.observed_at:
                latest[key] = outcome
        for outcome in latest.values():
            if outcome.status != "uncertain":
                continue
            deployment = self.get_deployment(outcome.workspace_id, outcome.deployment_id)
            if deployment is None:
                continue
            pending.append({
                "workspace_id": outcome.workspace_id,
                "deployment_id": outcome.deployment_id,
                "service_id": deployment.service_id,
                "provider_reference": outcome.provider_reference,
                "status": outcome.status,
                "evidence": outcome.evidence or {},
                "reconciliation_attempts": len(self.list_reconciliation_evidence(outcome.workspace_id, outcome.deployment_id, limit=500)),
            })
            if len(pending) >= limit:
                break
        return pending

    def record_reconciliation_evidence(self, workspace_id, deployment_id, service_id, provider, provider_service_id, provider_reference, attempt_number, status, terminal, observed, verification_state, evidence=None):
        workspace_id = require_workspace_id(workspace_id, op="record_reconciliation_evidence")
        self._require_durable_write("record_reconciliation_evidence")
        row = DeploymentReconciliationEvidence(
            id=f"dre-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, deployment_id=str(deployment_id),
            service_id=str(service_id), provider=str(provider), provider_service_id=str(provider_service_id),
            provider_reference=str(provider_reference), attempt_number=int(attempt_number), status=str(status),
            terminal=bool(terminal), observed=observed or {}, verification_state=str(verification_state),
            evidence=evidence or {},
        )
        if self.supabase:
            self.supabase.table("it_deployment_reconciliation_evidence").insert(row.model_dump(mode="json")).execute()
        if not hasattr(self, "_reconciliation_evidence"):
            self._reconciliation_evidence = {}
        self._reconciliation_evidence[row.id] = row
        return row

    def list_reconciliation_evidence(self, workspace_id, deployment_id, limit=100):
        workspace_id = require_workspace_id(workspace_id, op="list_reconciliation_evidence")
        limit = max(1, min(int(limit), 500))
        if self.supabase:
            result = (self.supabase.table("it_deployment_reconciliation_evidence").select("*")
                      .eq("workspace_id", workspace_id).eq("deployment_id", str(deployment_id))
                      .order("observed_at", desc=True).limit(limit).execute())
            return result.data or []
        rows = [x.model_dump(mode="json") for x in getattr(self, "_reconciliation_evidence", {}).values()
                if x.workspace_id == workspace_id and x.deployment_id == str(deployment_id)]
        rows.sort(key=lambda x: x.get("observed_at", ""), reverse=True)
        return rows[:limit]

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
