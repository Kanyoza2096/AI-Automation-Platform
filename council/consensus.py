"""Consensus engine with weighted scoring and early termination.

v2 — QUORUM DENOMINATOR FIXED:
  - Quorum is now measured against the *invited set* (the personas the
    router selected for this tier), not against the voters. Previously a
    small invited panel that mostly showed up could self-certify quorum
    even though most of the council was never asked. This closes the
    governance hole the module docstring has always claimed to prevent.
  - `deliberate()` accepts an optional `invited_weight`. When supplied,
    it is the quorum denominator. When omitted, the caller is assumed to
    have invited exactly the personas that voted, and today's behavior is
    preserved.
"""

from __future__ import annotations

import logging
import time
from typing import List, Optional

from council.config import DemocracyConfig
from council.models import (
    DeliberationResult,
    DecisionState,
    PersonaVote,
    Proposal,
    RiskLevel,
    VetoType,
)
from council.policies.veto_policy import VetoPolicy

logger = logging.getLogger("kanyoza.council.consensus")


class ConsensusEngine:
    """Consensus calculation with adaptive scoring."""

    def __init__(self, config: Optional[DemocracyConfig] = None):
        self.config = config or DemocracyConfig()
        self.veto_policy = VetoPolicy()

    def deliberate(
        self,
        proposal: Proposal,
        votes: List[PersonaVote],
        tier: int,
        invited_weight: Optional[float] = None,
    ) -> DeliberationResult:
        """Calculate a risk-sensitive, evidence-aware consensus.

        A Council decision is not a popularity contest. High/critical actions
        require quorum, adequate evidence, complete safety coverage and a
        materially stronger approval mandate. Invalid vetoes are ignored rather
        than becoming accidental single-member authority.

        `invited_weight` is the total weight of personas the router invited
        for this tier. When supplied, it is the quorum denominator (see
        `_compute_quorum`). When omitted, the denominator is derived from the
        votes as before, preserving backward compatibility for direct callers.
        """
        start = time.monotonic()

        valid_vetoes = self.veto_policy.validate_vetoes(votes)
        invalid_hard_vetoes = [
            v for v in votes
            if v.veto_type == VetoType.HARD and v not in valid_vetoes
        ]

        # Only policy-valid hard vetoes can block a decision.
        for vote in valid_vetoes:
            if vote.veto_type == VetoType.HARD and vote.vote == "reject":
                return self._build_result(
                    proposal, votes, DecisionState.BLOCKED,
                    approve_ratio=0.0, reject_ratio=1.0,
                    quorum_ratio=1.0, confidence=1.0,
                    tier=tier, early_terminated=True,
                    elapsed_ms=(time.monotonic() - start) * 1000,
                    extra_warnings=("Valid hard veto exercised by " + vote.member,),
                )

        quorum_ratio = self._compute_quorum(votes, invited_weight)

        approve_score = 0.0
        reject_score = 0.0
        for vote in votes:
            influence = self._calculate_influence(vote)
            if vote.vote == "approve":
                approve_score += influence
            elif vote.vote == "reject":
                reject_score += influence

        decision_weight = approve_score + reject_score
        approve_ratio = approve_score / decision_weight if decision_weight else 0.0
        reject_ratio = reject_score / decision_weight if decision_weight else 0.0
        confidence = max(approve_ratio, reject_ratio) if decision_weight else 0.0

        warnings = list(self._quality_warnings(
            proposal, votes, quorum_ratio, invalid_hard_vetoes
        ))

        # A quorum failure is never an approval. This closes the old path where
        # abstention/no-response could still produce a majority among a tiny
        # participating subset.
        if quorum_ratio < self.config.min_quorum_ratio:
            decision = DecisionState.NEEDS_REVISION
            warnings.append(
                f"Insufficient quorum: {quorum_ratio:.2f} < "
                f"{self.config.min_quorum_ratio:.2f}."
            )
        elif decision_weight <= 0:
            decision = DecisionState.NEEDS_REVISION
            warnings.append("No decisive approve/reject evidence was available.")
        elif proposal.risk_level == RiskLevel.CRITICAL:
            decision = self._risk_sensitive_decision(
                proposal, votes, approve_ratio, reject_ratio,
                self.config.critical_approval_threshold,
                self.config.critical_rejection_threshold,
                self.config.max_critical_abstention_ratio,
                self.config.minimum_evidence_quality_critical,
            )
        elif proposal.risk_level == RiskLevel.HIGH:
            decision = self._risk_sensitive_decision(
                proposal, votes, approve_ratio, reject_ratio,
                self.config.high_risk_approval_threshold,
                self.config.high_risk_rejection_threshold,
                self.config.max_high_risk_abstention_ratio,
                self.config.minimum_evidence_quality_high_risk,
            )
        else:
            if approve_ratio >= self.config.approval_threshold:
                decision = (
                    DecisionState.APPROVED_WITH_WARNINGS
                    if self._has_warnings(votes) else DecisionState.APPROVED
                )
            elif reject_ratio >= self.config.rejection_threshold:
                decision = (
                    DecisionState.ESCALATED
                    if self._has_soft_veto(votes) else DecisionState.REJECTED
                )
            else:
                decision = DecisionState.NEEDS_REVISION

        # A soft veto always prevents autonomous approval. It creates a
        # deliberate escalation/review state instead of being swallowed by a
        # numerical majority.
        if decision in {
            DecisionState.APPROVED,
            DecisionState.APPROVED_WITH_WARNINGS,
        } and self._has_soft_veto(votes):
            decision = DecisionState.ESCALATED
            warnings.append("Soft veto requires review before execution.")

        return self._build_result(
            proposal, votes, decision,
            approve_ratio=round(approve_ratio, 4),
            reject_ratio=round(reject_ratio, 4),
            quorum_ratio=round(quorum_ratio, 4),
            confidence=round(confidence, 4),
            tier=tier,
            early_terminated=False,
            elapsed_ms=(time.monotonic() - start) * 1000,
            extra_warnings=tuple(warnings),
        )

    def _compute_quorum(
        self,
        votes: List[PersonaVote],
        invited_weight: Optional[float],
    ) -> float:
        """Compute quorum as (participating weight) / (invited weight).

        The denominator is the invited set's total weight when supplied. If
        the caller does not know the invited set, fall back to the weight of
        the voters (previous behavior, kept for backward compatibility).

        `participating` counts votes with non-zero confidence. A vote with
        zero confidence is not real participation regardless of the persona.
        """
        participating_weight = sum(
            max(0.0, v.weight) for v in votes if v.confidence > 0
        )

        if invited_weight and invited_weight > 0:
            denominator = float(invited_weight)
        else:
            # Fallback: the caller is assumed to have invited exactly the
            # personas that voted. This preserves pre-v2 behavior for callers
            # that do not (yet) pass invited_weight.
            denominator = sum(max(0.0, v.weight) for v in votes)

        return participating_weight / denominator if denominator else 0.0

    def _risk_sensitive_decision(
        self,
        proposal: Proposal,
        votes: List[PersonaVote],
        approve_ratio: float,
        reject_ratio: float,
        approval_threshold: float,
        rejection_threshold: float,
        max_abstention_ratio: float,
        minimum_evidence_quality: float,
    ) -> DecisionState:
        abstain_weight = sum(max(0.0, v.weight) for v in votes if v.vote == "abstain")
        total_weight = sum(max(0.0, v.weight) for v in votes)
        abstention_ratio = abstain_weight / total_weight if total_weight else 1.0

        decisive_votes = [v for v in votes if v.vote in {"approve", "reject"}]
        evidence_quality = (
            sum(max(0.0, v.evidence_quality) for v in decisive_votes)
            / len(decisive_votes)
            if decisive_votes else 0.0
        )

        if abstention_ratio > max_abstention_ratio:
            return DecisionState.NEEDS_REVISION
        if evidence_quality < minimum_evidence_quality:
            return DecisionState.NEEDS_REVISION
        if (
            proposal.risk_level == RiskLevel.CRITICAL
            and not any(v.member == "risk_manager" for v in votes)
        ):
            return DecisionState.BLOCKED

        if approve_ratio >= approval_threshold:
            return (
                DecisionState.APPROVED_WITH_WARNINGS
                if self._has_warnings(votes) else DecisionState.APPROVED
            )
        if reject_ratio >= rejection_threshold:
            return (
                DecisionState.ESCALATED
                if self._has_soft_veto(votes) else DecisionState.REJECTED
            )
        return DecisionState.NEEDS_REVISION

    def _quality_warnings(
        self,
        proposal: Proposal,
        votes: List[PersonaVote],
        quorum_ratio: float,
        invalid_hard_vetoes: List[PersonaVote],
    ) -> List[str]:
        warnings: List[str] = []
        if invalid_hard_vetoes:
            warnings.append("One or more invalid hard veto claims were ignored by policy.")
        if proposal.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL) and not any(
            v.member == "risk_manager" for v in votes
        ):
            warnings.append("Risk Manager coverage is missing for a high-risk decision.")
        if proposal.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL) and not any(
            v.evidence for v in votes if v.vote in {"approve", "reject"}
        ):
            warnings.append(
                "No decisive vote supplied evidence; decision quality is insufficient."
            )
        return warnings

    def _calculate_influence(self, vote: PersonaVote) -> float:
        """Calculate effective influence using the multi-factor model."""
        return (
            vote.weight
            * vote.reliability
            * vote.evidence_quality
            * vote.confidence
            * vote.relevance
        )

    def _has_warnings(self, votes: List[PersonaVote]) -> bool:
        return any(
            v.reason and "risk" in v.reason.lower()
            for v in votes if v.vote in ("reject", "abstain")
        )

    def _has_soft_veto(self, votes: List[PersonaVote]) -> bool:
        return any(
            v.veto_type == VetoType.SOFT and v.vote == "reject" for v in votes
        )

    def _build_result(
        self, proposal, votes, decision, approve_ratio, reject_ratio,
        quorum_ratio, confidence, tier, early_terminated, elapsed_ms,
        extra_warnings=(),
    ) -> DeliberationResult:
        dissent = [
            {"member": v.member, "reason": v.reason}
            for v in votes if v.vote == "reject" and v.confidence > 0.5
        ]
        warnings = [
            v.reason for v in votes
            if v.vote == "reject" and v.veto_type == VetoType.SOFT
        ]
        warnings.extend(extra_warnings)

        return DeliberationResult(
            proposal_id=proposal.id,
            decision=decision,
            approve_ratio=approve_ratio,
            reject_ratio=reject_ratio,
            quorum_ratio=quorum_ratio,
            confidence=confidence,
            votes=votes,
            dissent=dissent,
            warnings=warnings,
            risk_level=proposal.risk_level,
            tier_used=tier,
            early_terminated=early_terminated,
            deliberation_time_ms=round(elapsed_ms, 3),
        )
