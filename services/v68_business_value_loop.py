"""V68.5 — governed business value loop.

Closes the gap between one successful workflow execution and a repeatable
business operating loop. This module is intentionally a read/planning layer:
it evaluates measured outcomes against an autonomy envelope, produces an
advisory next-step disposition, and never changes policy, authority, approval,
or execution state.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from services.business_autonomy_envelope import BusinessAutonomyEnvelope


@dataclass(frozen=True)
class BusinessValueLoopResult:
    workspace_id: str
    envelope_id: str
    objective: str
    outcome_state: str
    metric_results: tuple[dict[str, Any], ...]
    disposition: str
    reasons: tuple[str, ...]
    next_action: Optional[str] = None
    learning_evaluation_state: Optional[str] = None
    authority: str = "none"
    execution_effect: str = "none"
    approval_effect: str = "none"
    policy_effect: str = "none"

    def __post_init__(self) -> None:
        if any(v != "none" for v in (self.authority, self.execution_effect, self.approval_effect, self.policy_effect)):
            raise ValueError("business value loop cannot grant authority or effects")

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "envelope_id": self.envelope_id,
            "objective": self.objective,
            "outcome_state": self.outcome_state,
            "metric_results": [dict(x) for x in self.metric_results],
            "disposition": self.disposition,
            "reasons": list(self.reasons),
            "next_action": self.next_action,
            "learning_evaluation_state": self.learning_evaluation_state,
            "authority": "none",
            "execution_effect": "none",
            "approval_effect": "none",
            "policy_effect": "none",
        }


def _compare(value: float, operator: str, target: float) -> bool:
    return {
        ">=": value >= target,
        "<=": value <= target,
        "==": value == target,
        ">": value > target,
        "<": value < target,
    }[operator]


def evaluate_business_value_loop(
    envelope: BusinessAutonomyEnvelope,
    *,
    workspace_id: str,
    outcome_state: str,
    metrics: Mapping[str, float],
    next_action: Optional[str] = None,
    learning_evaluation_state: Optional[str] = None,
    evidence_confidence: float = 1.0,
    review_required: bool = False,
) -> BusinessValueLoopResult:
    """Evaluate whether the business loop has evidence to continue.

    A successful execution is not sufficient: the declared business metrics
    must be measured, confidence must satisfy the metric requirement, and
    review/uncertainty must remain visible. A next action is only advisory.
    """
    reasons: list[str] = []
    results: list[dict[str, Any]] = []

    if workspace_id != envelope.workspace_id:
        return BusinessValueLoopResult(workspace_id, envelope.envelope_id, envelope.objective,
                                       outcome_state, (), "blocked", ("workspace_mismatch",))
    if not 0.0 <= float(evidence_confidence) <= 1.0:
        raise ValueError("evidence_confidence must be between 0 and 1")
    if not envelope.is_active():
        return BusinessValueLoopResult(workspace_id, envelope.envelope_id, envelope.objective,
                                       outcome_state, (), "blocked", ("envelope_not_active",))

    for metric in envelope.success_metrics:
        present = metric.name in metrics
        value = float(metrics[metric.name]) if present else None
        passed = bool(present and _compare(value, metric.operator, float(metric.target)))
        confidence_ok = evidence_confidence >= float(metric.minimum_confidence)
        result = {
            "name": metric.name,
            "target": float(metric.target),
            "operator": metric.operator,
            "value": value,
            "passed": passed,
            "confidence": float(evidence_confidence),
            "confidence_required": float(metric.minimum_confidence),
            "confidence_ok": confidence_ok,
        }
        results.append(result)
        if not present:
            reasons.append(f"metric_missing:{metric.name}")
        elif not passed:
            reasons.append(f"metric_failed:{metric.name}")
        elif not confidence_ok:
            reasons.append(f"metric_confidence_insufficient:{metric.name}")

    if outcome_state in {"uncertain", "unknown"}:
        reasons.append("business_outcome_uncertain")
    elif outcome_state == "failed":
        reasons.append("business_outcome_failed")
    elif outcome_state not in {"succeeded", "observed"}:
        reasons.append("unsupported_outcome_state")

    if review_required:
        reasons.append("review_required")
    if learning_evaluation_state in {"mixed", "review_required", "insufficient_evidence", "ineffective"}:
        reasons.append(f"learning_{learning_evaluation_state}")

    all_metrics_pass = bool(results) and all(r["passed"] and r["confidence_ok"] for r in results)

    if outcome_state in {"uncertain", "unknown"} or "review_required" in reasons:
        disposition = "review_required"
    elif outcome_state == "failed" or not all_metrics_pass:
        disposition = "improve_or_escalate"
    elif learning_evaluation_state == "effective" and all_metrics_pass:
        disposition = "continue"
    else:
        disposition = "continue_with_observation"

    # The next action is informational only. The caller must still pass it
    # through workflow admission, decision policy, approvals, and Action Kernel.
    return BusinessValueLoopResult(
        workspace_id=workspace_id,
        envelope_id=envelope.envelope_id,
        objective=envelope.objective,
        outcome_state=outcome_state,
        metric_results=tuple(results),
        disposition=disposition,
        reasons=tuple(dict.fromkeys(reasons)),
        next_action=next_action if disposition in {"continue", "continue_with_observation"} else None,
        learning_evaluation_state=learning_evaluation_state,
    )


def assert_value_loop_is_advisory(result: Mapping[str, Any]) -> None:
    """Defense-in-depth assertion against accidental authority escalation."""
    for key in ("authority", "execution_effect", "approval_effect", "policy_effect"):
        if result.get(key, "none") != "none":
            raise ValueError(f"business value loop cannot grant {key}")
    if result.get("execute_provider") or result.get("provider_execute") or result.get("bypass_action_kernel"):
        raise ValueError("parallel execution metadata rejected")
