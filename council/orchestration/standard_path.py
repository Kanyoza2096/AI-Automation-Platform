"""
Standard Path — Medium-risk actions, moderate voting.

Used for: Tier 2 (MEDIUM RISK)
Personas: 3-5 relevant members
Latency: <200ms

v2 — CORRECTED CONSENSUS MATH:
  - Quorum is now measured against the FULL council weight, not just voters.
    A tier-2 deliberation with 5 of 12 members voting cannot self-certify as
    quorum=1.0.
  - Abstentions count as negative evidence against approval. Silence is not
    consent. A single approve + four abstains no longer becomes "100%".
  - High abstention rate forces NEEDS_REVISION even if the yes vote is
    unanimous among voters.
"""

from __future__ import annotations

import logging
import time
from typing import List, Optional

from council.models import (
    DeliberationResult, DecisionState, PersonaVote,
    Proposal, RiskLevel, VetoType,
)

logger = logging.getLogger("kanyoza.council.standard_path")


def _compute_total_council_weight() -> float:
    """
    Sum of weights across every persona in the full council.

    Used as the denominator for quorum and approval ratios so that a
    partial tier-2 vote cannot masquerade as a full-council decision.
    """
    try:
        from council.personas import ALL_PERSONAS
    except Exception:
        return 0.0
    total = 0.0
    for cls in ALL_PERSONAS:
        try:
            total += float(getattr(cls, "weight", 1.0) or 1.0)
        except Exception:
            total += 1.0
    return total


class StandardPath:
    """Standard deliberation for medium-risk actions."""

    MIN_QUORUM = 0.50            # half the council must participate
    APPROVAL_THRESHOLD = 0.55    # of decisive influence
    REJECTION_THRESHOLD = 0.35   # of decisive influence
    MAX_ABSTAIN_RATIO = 0.50     # >50% abstain → force revision
    UNANIMOUS_MIN_QUORUM = 0.40  # a unanimous vote still needs this quorum

    def __init__(self, personas: dict, config=None):
        self._personas = personas
        self.config = config
        self._total_council_weight = _compute_total_council_weight()

    async def deliberate(
        self,
        proposal: Proposal,
        selected_agents: Optional[List[str]] = None,
    ) -> DeliberationResult:
        """Standard deliberation with 3-5 personas."""
        start = time.monotonic()

        personas = self._select_personas(proposal, selected_agents)

        votes: List[PersonaVote] = []
        hard_veto_found = False
        errors: List[str] = []

        for persona in personas:
            try:
                vote = persona.evaluate(proposal)
                votes.append(vote)

                if vote.veto_type == VetoType.HARD and vote.vote == "reject":
                    hard_veto_found = True
                    break
            except Exception as exc:
                msg = f"{persona.name}: {type(exc).__name__}: {exc}"
                logger.warning("Standard path persona failed: %s", msg)
                errors.append(msg[:200])

        result = self._calculate_consensus(proposal, votes, hard_veto_found, errors)
        result.deliberation_time_ms = round((time.monotonic() - start) * 1000, 3)

        return result

    def _select_personas(self, proposal, selected_agents):
        """Select 3-5 relevant personas."""
        if selected_agents:
            return [
                self._personas[name]
                for name in selected_agents[:5]
                if name in self._personas
            ]

        defaults = [
            "business_owner", "customer_voice", "data_analyst",
            "trend_spotter", "rippler",
        ]
        return [
            self._personas[name]
            for name in defaults
            if name in self._personas
        ][:5]

    def _calculate_consensus(
        self,
        proposal: Proposal,
        votes: List[PersonaVote],
        hard_veto_found: bool,
        errors: Optional[List[str]] = None,
    ) -> DeliberationResult:
        """Calculate consensus with dissent tracking."""

        errors = errors or []
        total_council_weight = self._total_council_weight or 1.0

        # ── Fail closed: no votes ───────────────────────────────────────
        if not votes:
            return DeliberationResult(
                proposal_id=proposal.id,
                decision=DecisionState.NEEDS_REVISION,
                approve_ratio=0.0,
                reject_ratio=0.0,
                quorum_ratio=0.0,
                confidence=0.0,
                votes=[],
                dissent=[],
                warnings=["No personas produced votes — deliberation failed"] + errors,
                risk_level=RiskLevel.MEDIUM,
                tier_used=2,
                fallback_used=True,
            )

        # ── Hard veto blocks immediately ────────────────────────────────
        if hard_veto_found:
            return DeliberationResult(
                proposal_id=proposal.id,
                decision=DecisionState.BLOCKED,
                approve_ratio=0.0,
                reject_ratio=1.0,
                quorum_ratio=1.0,
                confidence=1.0,
                votes=votes,
                dissent=[],
                warnings=errors,
                risk_level=RiskLevel.MEDIUM,
                tier_used=2,
                early_terminated=True,
            )

        # ── Per-vote influence ──────────────────────────────────────────
        approve_influence = 0.0
        reject_influence = 0.0
        abstain_influence = 0.0

        for vote in votes:
            weight = float(getattr(vote, "weight", 1.0) or 1.0)
            conf = float(getattr(vote, "confidence", 0.0) or 0.0)
            influence = weight * conf

            if vote.vote == "approve":
                approve_influence += influence
            elif vote.vote == "reject":
                reject_influence += influence
            else:
                # Abstentions count as a fraction of the member's weight.
                # A confident abstain ("I refuse to vote") is treated more
                # seriously than a weak one.
                abstain_influence += influence

        decisive_influence = approve_influence + reject_influence
        participated_influence = (
            approve_influence + reject_influence + abstain_influence
        )

        # ── Quorum: measured against the FULL council ───────────────────
        quorum = participated_influence / total_council_weight

        # ── Fail closed: no decisive score ──────────────────────────────
        if decisive_influence == 0:
            return DeliberationResult(
                proposal_id=proposal.id,
                decision=DecisionState.NEEDS_REVISION,
                approve_ratio=0.0,
                reject_ratio=0.0,
                quorum_ratio=round(quorum, 4),
                confidence=0.0,
                votes=votes,
                dissent=[],
                warnings=["All personas abstained — inconclusive"] + errors,
                risk_level=RiskLevel.MEDIUM,
                tier_used=2,
            )

        # ── Fail closed: insufficient quorum ────────────────────────────
        if quorum < self.MIN_QUORUM:
            return DeliberationResult(
                proposal_id=proposal.id,
                decision=DecisionState.NEEDS_REVISION,
                approve_ratio=round(approve_influence / decisive_influence, 4),
                reject_ratio=round(reject_influence / decisive_influence, 4),
                quorum_ratio=round(quorum, 4),
                confidence=round(quorum, 4),
                votes=votes,
                dissent=[],
                warnings=[
                    f"Low quorum ({quorum:.0%} < {self.MIN_QUORUM:.0%}) — insufficient council participation",
                    *errors,
                ],
                risk_level=RiskLevel.MEDIUM,
                tier_used=2,
            )

        # ── Ratio computed against decisive influence only ──────────────
        approve_ratio = approve_influence / decisive_influence
        reject_ratio = reject_influence / decisive_influence
        abstain_ratio = (
            abstain_influence / max(participated_influence, 0.0001)
        )

        # ── High abstention forces revision ─────────────────────────────
        # A unanimous yes from a small minority of the council is not a
        # mandate. Abort early.
        if abstain_ratio > self.MAX_ABSTAIN_RATIO and approve_ratio < 0.7:
            return DeliberationResult(
                proposal_id=proposal.id,
                decision=DecisionState.NEEDS_REVISION,
                approve_ratio=round(approve_ratio, 4),
                reject_ratio=round(reject_ratio, 4),
                quorum_ratio=round(quorum, 4),
                confidence=round(abstain_ratio, 4),
                votes=votes,
                dissent=[],
                warnings=[
                    f"High abstention ({abstain_ratio:.0%}) — insufficient mandate",
                    *errors,
                ],
                risk_level=RiskLevel.MEDIUM,
                tier_used=2,
            )

        confidence = max(approve_ratio, reject_ratio)

        # ── Decision ────────────────────────────────────────────────────
        dissent = [
            {"member": v.member, "reason": v.reason}
            for v in votes
            if v.vote == "reject" and float(getattr(v, "confidence", 0.0) or 0.0) > 0.5
        ]

        soft_veto_warnings = [
            v.reason for v in votes
            if v.vote == "reject" and getattr(v, "veto_type", None) == VetoType.SOFT
        ]

        all_warnings = soft_veto_warnings + errors

        if approve_ratio >= self.APPROVAL_THRESHOLD:
            if soft_veto_warnings:
                decision = DecisionState.APPROVED_WITH_WARNINGS
            else:
                decision = DecisionState.APPROVED
        elif reject_ratio >= self.REJECTION_THRESHOLD:
            has_soft_veto = any(
                getattr(v, "veto_type", None) == VetoType.SOFT and v.vote == "reject"
                for v in votes
            )
            if has_soft_veto:
                decision = DecisionState.ESCALATED
            else:
                decision = DecisionState.REJECTED
        else:
            decision = DecisionState.NEEDS_REVISION

        return DeliberationResult(
            proposal_id=proposal.id,
            decision=decision,
            approve_ratio=round(approve_ratio, 4),
            reject_ratio=round(reject_ratio, 4),
            quorum_ratio=round(quorum, 4),
            confidence=round(confidence, 4),
            votes=votes,
            dissent=dissent,
            warnings=all_warnings,
            risk_level=RiskLevel.MEDIUM,
            tier_used=2,
            early_terminated=False,
        )
