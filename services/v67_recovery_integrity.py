"""V67.16 — recovery control-plane integrity certification.

Recovery state is useful only when it agrees with authoritative execution state.
This module detects impossible/orphaned combinations without changing execution
state. Repair remains a separate, governed control-plane operation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class RecoveryIntegrityIssue:
    code: str
    severity: str
    plan_id: str | None
    intent_id: str | None
    reason: str


@dataclass(frozen=True)
class RecoveryIntegrityReport:
    status: str
    issues: tuple[RecoveryIntegrityIssue, ...]
    authority: str = "none"

    @property
    def ok(self) -> bool:
        return self.status == "healthy"


def _v(row: Any, key: str, default=None):
    return row.get(key, default) if isinstance(row, dict) else getattr(row, key, default)


def audit_recovery_integrity(
    plans: Iterable[Any],
    exceptions: Iterable[Any],
    outbox: Iterable[Any],
) -> RecoveryIntegrityReport:
    """Fail-closed consistency audit over recovery/control-plane projections.

    The function deliberately performs no repair and grants no execution
    authority. It compares the recovery plan/exception state with the durable
    outbox state supplied by an authoritative reader.
    """
    plan_rows = list(plans)
    exception_rows = list(exceptions)
    outbox_rows = list(outbox)
    issues: list[RecoveryIntegrityIssue] = []

    plans_by_id = {str(_v(p, "plan_id", "")): p for p in plan_rows if _v(p, "plan_id")}
    exceptions_by_id = {str(_v(e, "exception_id", "")): e for e in exception_rows if _v(e, "exception_id")}
    outbox_by_intent = {str(_v(o, "intent_id", "")): o for o in outbox_rows if _v(o, "intent_id")}

    for p in plan_rows:
        pid = str(_v(p, "plan_id", ""))
        eid = str(_v(p, "exception_id", ""))
        ws = str(_v(p, "workspace_id", ""))
        action = str(_v(p, "action_type", ""))
        state = str(_v(p, "state", "planned"))
        disposition = str(_v(p, "disposition", ""))
        authority = str(_v(p, "authority", "none"))

        if not pid or not eid or not ws or not action:
            issues.append(RecoveryIntegrityIssue("MALFORMED_PLAN", "critical", pid or None, None, "recovery plan identity is incomplete"))
            continue
        if authority != "none":
            issues.append(RecoveryIntegrityIssue("AUTHORITY_ESCALATION", "critical", pid, None, "recovery plan authority is not none"))
        if state not in {"planned", "released", "recovered", "superseded"}:
            issues.append(RecoveryIntegrityIssue("INVALID_PLAN_STATE", "critical", pid, None, "unknown recovery plan state"))
        if disposition == "auto_recover" and _v(p, "max_attempts", 0) <= 0:
            issues.append(RecoveryIntegrityIssue("INVALID_AUTO_RECOVER_BUDGET", "critical", pid, None, "AUTO_RECOVER plan has no positive retry budget"))

        exc = exceptions_by_id.get(eid)
        if exc is None:
            issues.append(RecoveryIntegrityIssue("ORPHAN_PLAN", "critical", pid, None, "recovery plan references a missing exception"))
            continue
        if str(_v(exc, "workspace_id", "")) != ws:
            issues.append(RecoveryIntegrityIssue("WORKSPACE_LINEAGE_MISMATCH", "critical", pid, None, "plan and exception workspace differ"))

        intent_id = _v(exc, "intent_id")
        if not intent_id:
            # Reconciliation/review plans can exist before an intent is known;
            # however an already released/recovered plan must have one.
            if state in {"released", "recovered"}:
                issues.append(RecoveryIntegrityIssue("MISSING_RELEASED_INTENT", "critical", pid, None, "released/recovered plan has no intent lineage"))
            continue
        intent_id = str(intent_id)
        out = outbox_by_intent.get(intent_id)

        if state in {"released", "recovered"} and out is None:
            issues.append(RecoveryIntegrityIssue("ORPHAN_RELEASE", "critical", pid, intent_id, "released/recovered plan has no matching outbox intent"))
            continue
        if out is not None:
            out_ws = str(_v(out, "workspace_id", ""))
            out_plan = _v(out, "recovery_plan_id")
            out_action = str(_v(out, "action_type", ""))
            if out_ws != ws:
                issues.append(RecoveryIntegrityIssue("OUTBOX_WORKSPACE_MISMATCH", "critical", pid, intent_id, "outbox and recovery workspace differ"))
            if out_plan is not None and str(out_plan) != pid:
                issues.append(RecoveryIntegrityIssue("RECOVERY_PLAN_LINEAGE_MISMATCH", "critical", pid, intent_id, "outbox references a different recovery plan"))
            if out_action and out_action != action:
                issues.append(RecoveryIntegrityIssue("ACTION_LINEAGE_MISMATCH", "critical", pid, intent_id, "recovery plan and outbox action differ"))

            out_status = str(_v(out, "status", "")).lower()
            if state == "recovered" and out_status != "succeeded":
                issues.append(RecoveryIntegrityIssue("FALSE_RECOVERY", "critical", pid, intent_id, "recovered plan is not backed by terminal outbox success"))
            if state == "released" and out_status in {"succeeded", "failed", "uncertain", "reconciliation_required", "cancelled"}:
                # A terminal outcome means the plan should have been closed or
                # explicitly superseded/reviewed rather than left merely released.
                issues.append(RecoveryIntegrityIssue("STALE_RELEASE_STATE", "high", pid, intent_id, "released plan disagrees with terminal outbox outcome"))

    for e in exception_rows:
        eid = str(_v(e, "exception_id", ""))
        state = str(_v(e, "state", ""))
        if not eid:
            issues.append(RecoveryIntegrityIssue("MALFORMED_EXCEPTION", "critical", None, None, "exception identity is missing"))
            continue
        linked = [p for p in plan_rows if str(_v(p, "exception_id", "")) == eid]
        if state in {"recovery_planned", "recovered"} and not linked:
            issues.append(RecoveryIntegrityIssue("ORPHAN_EXCEPTION_STATE", "critical", None, _v(e, "intent_id"), "exception state claims recovery without a recovery plan"))
        if state == "recovered" and linked and not any(str(_v(p, "state", "")) == "recovered" for p in linked):
            issues.append(RecoveryIntegrityIssue("EXCEPTION_PLAN_DISAGREEMENT", "critical", str(_v(linked[0], "plan_id")), _v(e, "intent_id"), "exception is recovered but no linked plan is recovered"))

    status = "healthy" if not issues else "attention_required"
    return RecoveryIntegrityReport(status=status, issues=tuple(issues))


def validate_recovery_integrity_report(report: RecoveryIntegrityReport) -> None:
    if report.authority != "none":
        raise ValueError("integrity audit cannot grant authority")
    if report.status not in {"healthy", "attention_required"}:
        raise ValueError("invalid integrity status")
    if any(i.severity == "critical" for i in report.issues) and report.status == "healthy":
        raise ValueError("critical recovery integrity issue cannot be healthy")
