"""Cybersecurity repository with durable Supabase and test-friendly in-memory storage."""
import uuid
from datetime import datetime, timezone
from typing import Optional
from core.tenant_repository import require_workspace_id
from .models import Asset, SecurityFinding, SecurityAlert, SecurityIncident, SecurityResponseOutcome, SecurityOutcome


class CybersecurityRepository:
    def __init__(self, supabase_client=None):
        self.supabase = supabase_client
        self._assets: dict[str, Asset] = {}
        self._findings: dict[str, SecurityFinding] = {}
        self._alerts: dict[str, SecurityAlert] = {}
        self._incidents: dict[str, SecurityIncident] = {}
        self._responses: dict[str, SecurityResponseOutcome] = {}
        self._outcomes: dict[str, SecurityOutcome] = {}

    @staticmethod
    def _production() -> bool:
        try:
            from config.settings import settings
            import os
            mode = os.getenv("SERVICE_MODE", "").strip().lower()
            return bool(getattr(settings, "is_production", False)) and mode in {
                "full", "api", "core", "director", "meta", "condition", "workers", "monitor",
                "github", "chaos",
            }
        except Exception:
            return False

    def _require_durable_write(self, operation: str):
        if self.supabase is None and self._production():
            raise RuntimeError(f"durable cybersecurity database is required for {operation}")

    def add_asset(self, workspace_id, data):
        workspace_id = require_workspace_id(workspace_id, op="add_asset")
        self._require_durable_write("add_asset")
        asset = Asset(
            id=f"ast-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id,
            name=str(data.get("name", "")).strip(), asset_type=str(data.get("asset_type", "service")).lower(),
            environment=str(data.get("environment", "production")).lower(), criticality=str(data.get("criticality", "medium")).lower(),
            status=str(data.get("status", "active")).lower(),
        )
        if not asset.name:
            raise ValueError("asset name is required")
        if asset.criticality not in {"low", "medium", "high", "critical"}:
            raise ValueError("unsupported asset criticality")
        if self.supabase:
            self.supabase.table("security_assets").insert(asset.model_dump(mode="json")).execute()
        self._assets[asset.id] = asset
        return asset

    def get_asset(self, workspace_id, asset_id) -> Optional[Asset]:
        workspace_id = require_workspace_id(workspace_id, op="get_asset")
        if self.supabase:
            result = self.supabase.table("security_assets").select("*").eq("workspace_id", workspace_id).eq("id", asset_id).limit(1).execute()
            if result.data:
                return Asset(**result.data[0])
        obj = self._assets.get(asset_id)
        return obj if obj and obj.workspace_id == workspace_id else None

    def create_finding(self, workspace_id, asset_id, data):
        workspace_id = require_workspace_id(workspace_id, op="create_finding")
        self._require_durable_write("create_finding")
        if self.get_asset(workspace_id, asset_id) is None:
            raise ValueError("asset not found")
        finding = SecurityFinding(
            id=f"find-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, asset_id=asset_id,
            title=str(data.get("title", "")).strip(), severity=str(data.get("severity", "medium")).lower(),
            source=str(data.get("source", "manual")).lower(),
        )
        if not finding.title:
            raise ValueError("finding title is required")
        if finding.severity not in {"low", "medium", "high", "critical"}:
            raise ValueError("unsupported finding severity")
        if self.supabase:
            self.supabase.table("security_findings").insert(finding.model_dump(mode="json")).execute()
        self._findings[finding.id] = finding
        return finding

    def get_finding(self, workspace_id, finding_id) -> Optional[SecurityFinding]:
        workspace_id = require_workspace_id(workspace_id, op="get_finding")
        if self.supabase:
            result = self.supabase.table("security_findings").select("*").eq("workspace_id", workspace_id).eq("id", finding_id).limit(1).execute()
            if result.data:
                return SecurityFinding(**result.data[0])
        obj = self._findings.get(finding_id)
        return obj if obj and obj.workspace_id == workspace_id else None

    def update_finding(self, workspace_id, finding_id, status):
        workspace_id = require_workspace_id(workspace_id, op="update_finding")
        if status not in {"open", "acknowledged", "remediated", "accepted", "closed", "verified"}:
            raise ValueError("unsupported finding status")
        now = datetime.now(timezone.utc)
        if self.supabase:
            result = self.supabase.table("security_findings").update({"status": status, "updated_at": now.isoformat()}).eq("workspace_id", workspace_id).eq("id", finding_id).select("*").limit(1).execute()
            if result.data:
                return SecurityFinding(**result.data[0])
        obj = self._findings.get(finding_id)
        if obj and obj.workspace_id == workspace_id:
            updated = obj.model_copy(update={"status": status, "updated_at": now})
            self._findings[finding_id] = updated
            return updated
        return None

    def create_alert(self, workspace_id, data):
        workspace_id = require_workspace_id(workspace_id, op="create_alert")
        self._require_durable_write("create_alert")
        asset_id = data.get("asset_id")
        if asset_id and self.get_asset(workspace_id, str(asset_id)) is None:
            raise ValueError("asset not found")
        alert = SecurityAlert(
            id=f"alt-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, asset_id=str(asset_id) if asset_id else None,
            title=str(data.get("title", "")).strip(), severity=str(data.get("severity", "medium")).lower(), source=str(data.get("source", "monitoring")).lower(),
        )
        if not alert.title:
            raise ValueError("alert title is required")
        if alert.severity not in {"low", "medium", "high", "critical"}:
            raise ValueError("unsupported alert severity")
        if self.supabase:
            self.supabase.table("security_alerts").insert(alert.model_dump(mode="json")).execute()
        self._alerts[alert.id] = alert
        return alert

    def create_incident(self, workspace_id, data):
        workspace_id = require_workspace_id(workspace_id, op="create_security_incident")
        self._require_durable_write("create_security_incident")
        incident = SecurityIncident(
            id=f"secinc-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id,
            title=str(data.get("title", "")).strip(), severity=str(data.get("severity", "medium")).lower(), source=str(data.get("source", "security")).lower(),
        )
        if not incident.title:
            raise ValueError("incident title is required")
        if incident.severity not in {"low", "medium", "high", "critical"}:
            raise ValueError("unsupported incident severity")
        if self.supabase:
            self.supabase.table("security_incidents").insert(incident.model_dump(mode="json")).execute()
        self._incidents[incident.id] = incident
        return incident

    def update_incident(self, workspace_id, incident_id, status):
        workspace_id = require_workspace_id(workspace_id, op="update_security_incident")
        if status not in {"open", "triaged", "contained", "eradicated", "recovered", "closed"}:
            raise ValueError("unsupported incident status")
        now = datetime.now(timezone.utc)
        if self.supabase:
            result = self.supabase.table("security_incidents").update({"status": status, "updated_at": now.isoformat()}).eq("workspace_id", workspace_id).eq("id", incident_id).select("*").limit(1).execute()
            if result.data:
                return SecurityIncident(**result.data[0])
        obj = self._incidents.get(incident_id)
        if obj and obj.workspace_id == workspace_id:
            updated = obj.model_copy(update={"status": status, "updated_at": now})
            self._incidents[incident_id] = updated
            return updated
        return None

    def record_response_outcome(self, workspace_id, data):
        workspace_id = require_workspace_id(workspace_id, op="record_security_response_outcome")
        self._require_durable_write("record_security_response_outcome")
        status = str(data.get("status", "")).lower()
        if status not in {"succeeded", "failed", "uncertain", "verified"}:
            raise ValueError("unsupported response outcome status")
        outcome = SecurityResponseOutcome(
            id=f"sro-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id,
            action_type=str(data.get("action_type", "")).strip(), target_id=str(data.get("target_id", "")).strip(),
            status=status, provider_reference=data.get("provider_reference"), evidence=data.get("evidence") or {},
        )
        if not outcome.action_type or not outcome.target_id:
            raise ValueError("action_type and target_id are required")
        if self.supabase:
            self.supabase.table("security_response_outcomes").insert(outcome.model_dump(mode="json")).execute()
        self._responses[outcome.id] = outcome
        return outcome

    def record_outcome(self, workspace_id, data):
        workspace_id = require_workspace_id(workspace_id, op="record_security_outcome")
        self._require_durable_write("record_security_outcome")
        outcome = SecurityOutcome(
            id=f"sout-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id,
            resource_type=str(data.get("resource_type", "")).strip(), resource_id=str(data.get("resource_id")) if data.get("resource_id") else None,
            metric=str(data.get("metric", "")).strip(), value=float(data.get("value", 0)), source=str(data.get("source", "security")).strip(), evidence=data.get("evidence") or {},
        )
        if not outcome.resource_type or not outcome.metric:
            raise ValueError("resource_type and metric are required")
        if self.supabase:
            self.supabase.table("security_outcomes").insert(outcome.model_dump(mode="json")).execute()
        self._outcomes[outcome.id] = outcome
        return outcome
