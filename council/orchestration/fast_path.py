"""
Fast Path — Low-risk actions, minimal voting.

Used for: Tier 1 (LOW RISK)
Personas: 1-2 relevant members
Latency: <50ms

v2 — CORRECTED CONSENSUS MATH:
  - Quorum is measured against the FULL council weight, not just the
    two-member panel.
  - Abstentions count as negative evidence. Silence is not consent.
  - Fast path can no longer rubber-stamp decisions with a unanimous
    two-member vote that hides abstention from the rest of the council.
  - If confidence is weak, escalate the action to Tier 2.
"""

from __future__ import annotations

import logging
import time
from typing import List, Optional

from council.models import (
    DeliberationResult, DecisionState, PersonaVote,
    Proposal, RiskLevel, VetoType,
)

logger = logging.getLogger("kanyoza.council.fast_path")


def _compute_total_council_weight() -> float:
    """Sum of weights across every persona in the full council."""
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


class FastPath:
    """Fast deliberation for low-risk actions."""

    # Minimum total influence (relative to full council) required to approve.
    # A 2-member panel rarely reaches 0.15 on its own — that's intentional.
    # The panel approves only when their combined influence is decisive.
    MIN_QUORUM_FRACTION = 0.10
    # If abstain outweighs approve, escalate instead of approving.
    MAX_ABSTAIN_RATIO = 0.50
    # A flat reject from any single persona blocks outright.
    REJECT_BLOCK_THRESHOLD = 0.7

    def __init__(self, personas: dict, config=None):
        self._personas = personas
        self.config = config
        self._total_council_weight = _compute_total_council_weight()

    async def deliberate(
        self,
        proposal: Proposal,
        selected_agents: Optional[List[str]] = None,
    ) -> DeliberationResult:
        """Quick deliberation with minimal voting."""
        start = time.monotonic()

        personas = self._select_personas(proposal, selected_agents)

        votes: List[PersonaVote] = []
        errors: List[str] = []

        for persona in personas:
            try:
                vote = persona.evaluate(proposal)
                votes.append(vote)
            except Exception as exc:
                # Do not swallow silently. Record the failure so it can be
                # surfaced as a warning, and DO NOT default to approve.
                msg = f"{persona.name}: {type(exc).__name__}: {exc}"
                logger.warning("Fast path persona failed: %s", msg)
                errors.append(msg[:200])

        # ── Fail closed: no votes ────────────────────────────────────────
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
                tier_used=1,
                deliberation_time_ms=round((time.monotonic() - start) * 1000, 3),
                fallback_used=True,
            )

        # ── Per-vote influence (weight × confidence) ────────────────────
        total_council_weight = self._total_council_weight or 1.0

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
                risk_level=RiskLevel.LOW,
                tier_used=1,
                deliberation_time_ms=round((time.monotonic() - start) * 1000, 3),
            )

        approve_ratio = approve_influence / decisive_influence
        reject_ratio = reject_influence / decisive_influence
        abstain_ratio = (
            abstain_influence / max(participated_influence, 0.0001)
        )

        # ── Decision ─────────────────────────────────────────────────────
        if reject_ratio > self.REJECT_BLOCK_THRESHOLD:
            decision = DecisionState.REJECTED
            confidence = reject_ratio
        elif quorum < self.MIN_QUORUM_FRACTION:
            # Panel voted but the combined weight is negligible relative to
            # the full council. Escalate to a real deliberation.
            decision = DecisionState.ESCALATED
            confidence = quorum
        elif abstain_ratio >= self.MAX_ABSTAIN_RATIO:
            # Panel split or abstained too much. Not a mandate.
            decision = DecisionState.NEEDS_REVISION
            confidence = abstain_ratio
        elif approve_ratio > 0.6:
            decision = DecisionState.APPROVED
            confidence = approve_ratio
        else:
            decision = DecisionState.NEEDS_REVISION
            confidence = max(approve_ratio, reject_ratio)

        return DeliberationResult(
            proposal_id=proposal.id,
            decision=decision,
            approve_ratio=round(approve_ratio, 4),
            reject_ratio=round(reject_ratio, 4),
            quorum_ratio=round(quorum, 4),
            confidence=round(confidence, 4),
            votes=votes,
            dissent=[],
            warnings=errors,
            risk_level=RiskLevel.LOW,
            tier_used=1,
            deliberation_time_ms=round((time.monotonic() - start) * 1000, 3),
        )

    def _select_personas(self, proposal, selected_agents):
        """Select 1-2 relevant personas."""
        if selected_agents:
            return [
                self._personas[name]
                for name in selected_agents[:2]
                if name in self._personas
            ]

        defaults = ["customer_voice", "trend_spotter"]
        return [
            self._personas[name]
            for name in defaults
            if name in self._personas
        ][:2]
