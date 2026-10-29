"""Phase 7.23 — Worker Selection Policy Audit & History.

Read-only audit projection over the governed policy lifecycle. It does not own
policy state, activate policy, authorize work, execute actions, or call providers.
"""
from __future__ import annotations
from typing import Any, Optional
from services.v71_32_worker_selection_policy_governance import WorkerSelectionPolicyGovernance, PolicyGovernanceBlock

class PolicyAuditBlock(str):
    TENANT_MISMATCH = "tenant_mismatch"
    TAMPERED = "tampered"
    EMPTY = "empty"
    AUTHORITY_VIOLATION = "authority_violation"

class WorkerSelectionPolicyAudit:
    VERSION = "phase7.23"
    def __init__(self, workspace_id: str, governance: WorkerSelectionPolicyGovernance):
        ws = str(workspace_id or "").strip()
        if not ws or governance.workspace_id != ws:
            raise ValueError(PolicyAuditBlock.TENANT_MISMATCH)
        self.workspace_id = ws
        self.governance = governance

    def history(self, *, policy_id: Optional[str] = None, change_id: Optional[str] = None) -> list[dict[str, Any]]:
        events = self.governance.audit_history(policy_id=policy_id, change_id=change_id)
        for event in events:
            try:
                self.governance.assert_audit_integrity(event)
            except ValueError:
                raise ValueError(PolicyAuditBlock.TAMPERED)
        return events

    def lifecycle(self, change_id: str) -> dict[str, Any]:
        events = self.history(change_id=change_id)
        if not events:
            raise ValueError(PolicyAuditBlock.EMPTY)
        return {
            "workspace_id": self.workspace_id,
            "change_id": change_id,
            "policy_id": events[0]["policy_id"],
            "version": events[0]["version"],
            "events": events,
            "states": [e["to_state"] for e in events],
            "terminal_state": events[-1]["to_state"],
            "read_only": True,
            "authority_granted": False,
            "can_authorize": False,
            "can_execute": False,
            "can_call_provider": False,
            "can_invoke_action_kernel": False,
        }

    def verify_chain(self, *, policy_id: Optional[str] = None) -> dict[str, Any]:
        events = self.history(policy_id=policy_id)
        return {"workspace_id": self.workspace_id, "event_count": len(events), "integrity": "PASS", "read_only": True}

__all__ = ["PolicyAuditBlock", "WorkerSelectionPolicyAudit"]
