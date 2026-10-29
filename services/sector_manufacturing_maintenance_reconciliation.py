"""Authoritative Jira verification for manufacturing maintenance requests."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any

class ManufacturingMaintenanceReconciliationService:
    def __init__(self, repository: Any, jira_adapter: Any):
        self.repository, self.jira_adapter = repository, jira_adapter

    def reconcile(self, *, workspace_id: str, request_id: str) -> dict[str, Any]:
        obj = self.repository.get_maintenance_request(workspace_id, request_id)
        if obj is None:
            raise ValueError("maintenance request not found in workspace")
        evidence = self.repository.list_maintenance_reconciliation_evidence(workspace_id, request_id, limit=500)
        attempt = len(evidence) + 1
        issue_key = obj.provider_issue_key
        if not issue_key:
            self.repository.record_maintenance_reconciliation_evidence(workspace_id, obj.id, attempt, "inconclusive", False, {}, "operator_review", {"reason":"provider issue reference unavailable"})
            return {"request_id": obj.id, "status":"unknown", "terminal":False, "reconciliation_required":True, "operator_review":True}
        observed = self.jira_adapter.get_issue(issue_key)
        if not isinstance(observed, dict) or not observed.get("id"):
            self.repository.record_maintenance_reconciliation_evidence(workspace_id, obj.id, attempt, "inconclusive", False, observed if isinstance(observed, dict) else {}, "provider_observed_nonterminal", {"reconciliation_state":"in_progress"})
            return {"request_id":obj.id,"status":"in_progress","terminal":False,"reconciliation_required":True,"observed":observed}
        # Creation of the Jira issue is the business request represented by this action;
        # the provider's existence of the referenced issue is the terminal verification.
        self.repository.record_maintenance_reconciliation_evidence(workspace_id, obj.id, attempt, "succeeded", True, observed, "verified_provider_terminal", {"reconciliation_state":"terminal"})
        outcome = self.repository.record_maintenance_request_outcome(workspace_id, obj.id, "succeeded", {"provider":"jira","issue_key":issue_key,"reconciled_at":datetime.now(timezone.utc).isoformat()})
        return {"request_id":obj.id,"status":"succeeded","terminal":True,"reconciliation_required":False,"issue":observed,"outcome_id":getattr(outcome,"id",None)}
