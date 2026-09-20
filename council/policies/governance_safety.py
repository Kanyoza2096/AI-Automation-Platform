"""Final governance safety boundary for Council decisions.

Council deliberation is a recommendation plane. It must never accidentally turn
high-impact or critical recommendations into autonomous approval. This module
is deliberately deterministic and side-effect free so every Council entry point
can enforce the same invariant after routing, fallback, or consensus.
"""
from __future__ import annotations

from council.models import DecisionState, DeliberationResult, Proposal, RiskLevel


_NON_AUTONOMOUS_RISK = {RiskLevel.HIGH, RiskLevel.CRITICAL}
_APPROVED = {DecisionState.APPROVED, DecisionState.APPROVED_WITH_WARNINGS}


def enforce_risk_decision_boundary(
    proposal: Proposal, result: DeliberationResult
) -> DeliberationResult:
    """Prevent high/critical Council results from becoming autonomous approval.

    This is a defense-in-depth invariant. Normal routing and consensus should
    already produce a safe result, but stale cache entries, alternate engines,
    fallback behavior, or future changes must not create a destructive approval
    path.
    """
    risk = proposal.risk_level
    if risk not in _NON_AUTONOMOUS_RISK or result.decision not in _APPROVED:
        return result

    warnings = list(result.warnings)
    warnings.append(
        f"Governance safety boundary: {risk.value} decision cannot be autonomously approved; human review required."
    )
    result.decision = DecisionState.ESCALATED
    result.fallback_used = result.fallback_used
    result.risk_level = risk
    result.approve_ratio = 0.0
    result.confidence = min(float(result.confidence), 0.99)
    result.warnings = warnings
    return result
