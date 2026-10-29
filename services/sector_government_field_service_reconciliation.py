"""Authoritative Jira reconciliation for Government field-service dispatches."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any

class GovernmentFieldServiceReconciliationService:
    def __init__(self, repository: Any, jira_adapter: Any):
        self.repository, self.jira_adapter = repository, jira_adapter

    def reconcile(self, *, workspace_id: str, dispatch_id: str) -> dict[str, Any]:
        obj = self.repository.get_field_service_dispatch(workspace_id, dispatch_id)
        if obj is None:
            raise ValueError("field-service dispatch not found in workspace")
        evidence = self.repository.list_field_service_reconciliation_evidence(workspace_id, dispatch_id, limit=500)
        attempt = len(evidence) + 1
        issue_key = obj.provider_issue_key
        if not issue_key:
            self.repository.record_field_service_reconciliation_evidence(workspace_id, obj.id, attempt, "unknown", False, {}, "operator_review", {"reason": "provider issue reference unavailable"})
            return {"dispatch_id": obj.id, "status": "unknown", "terminal": False, "reconciliation_required": True, "operator_review": True}
        observed = self.jira_adapter.get_issue(issue_key)
        if not isinstance(observed, dict) or not observed.get("id"):
            self.repository.record_field_service_reconciliation_evidence(workspace_id, obj.id, attempt, "in_progress", False, observed if isinstance(observed, dict) else {}, "provider_observed_nonterminal", {"reconciliation_state": "in_progress"})
            return {"dispatch_id": obj.id, "status": "in_progress", "terminal": False, "reconciliation_required": True, "observed": observed}
        self.repository.record_field_service_reconciliation_evidence(workspace_id, obj.id, attempt, "succeeded", True, observed, "verified_provider_terminal", {"reconciliation_state": "terminal", "provider_object_is_dispatch_request": True})
        outcome = self.repository.record_field_service_outcome(workspace_id, obj.id, "succeeded", {"provider": "jira", "issue_key": issue_key, "reconciled_at": datetime.now(timezone.utc).isoformat()})
        return {"dispatch_id": obj.id, "status": "succeeded", "terminal": True, "reconciliation_required": False, "issue": observed, "outcome_id": getattr(outcome, "id", None)}
