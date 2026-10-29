"""Authoritative GitHub workflow reconciliation for IT service remediation."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any

_TERMINAL = {"succeeded", "failed"}

class ITServiceRemediationReconciliationService:
    def __init__(self, repository: Any, github_adapter: Any):
        self.repository = repository
        self.github_adapter = github_adapter

    def reconcile(self, *, workspace_id: str, remediation_id: str) -> dict[str, Any]:
        obj = self.repository.get_service_remediation(workspace_id, remediation_id)
        if obj is None:
            raise ValueError("remediation not found in workspace")
        evidence = self.repository.list_service_remediation_reconciliation_evidence(workspace_id, remediation_id, limit=500)
        attempt = len(evidence) + 1
        observed = self.github_adapter.reconcile_workflow(
            obj.owner, obj.repo, workflow_id=obj.workflow_id, ref=obj.ref,
            started_after=obj.created_at, per_page=100,
        )
        if not isinstance(observed, dict):
            raise ValueError("invalid GitHub reconciliation response")
        matches = observed.get("matches") or []
        if len(matches) != 1:
            self.repository.record_service_remediation_reconciliation_evidence(
                workspace_id, obj.id, obj.service_id, "github", obj.owner, obj.repo,
                obj.workflow_id, obj.ref, attempt, "inconclusive", False, observed,
                "provider_observed_nonterminal", {"reconciliation_state": "in_progress"},
            )
            return {"remediation_id": obj.id, "status": "in_progress", "terminal": False, "reconciliation_required": True, "observed": observed}
        run = matches[0]
        status = str(run.get("status") or "").lower()
        conclusion = str(run.get("conclusion") or "").lower()
        if status != "completed":
            canonical = "in_progress"
        elif conclusion in {"success"}:
            canonical = "succeeded"
        elif conclusion in {"failure", "cancelled", "timed_out", "action_required", "startup_failure"}:
            canonical = "failed"
        else:
            canonical = "in_progress"
        terminal = canonical in _TERMINAL
        self.repository.record_service_remediation_reconciliation_evidence(
            workspace_id, obj.id, obj.service_id, "github", obj.owner, obj.repo,
            obj.workflow_id, obj.ref, attempt, canonical, terminal, {"workflow_run": run, "reconciliation": observed},
            "verified_provider_terminal" if terminal else "provider_observed_nonterminal",
            {"reconciliation_state": "terminal" if terminal else "in_progress"},
        )
        if terminal:
            outcome = self.repository.record_service_remediation_outcome(
                workspace_id, obj.id, canonical,
                {"provider": "github", "workflow_run_id": run.get("id"), "reconciled_at": datetime.now(timezone.utc).isoformat()},
            )
            return {"remediation_id": obj.id, "status": canonical, "terminal": True, "reconciliation_required": False, "run": run, "outcome_id": getattr(outcome, "id", None)}
        return {"remediation_id": obj.id, "status": "in_progress", "terminal": False, "reconciliation_required": True, "run": run}
