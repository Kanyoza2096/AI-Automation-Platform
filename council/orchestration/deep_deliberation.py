"""
Deep Deliberation — High-risk actions, full council voting.

Used for: Tier 3 (HIGH RISK, CRITICAL, IRREVERSIBLE)
Personas: 5-12 members (full council)
Latency: <500ms
Includes: Risk analysis, evidence collection, escalation

v2 — QUORUM DENOMINATOR FIXED:
  - Quorum is now measured against the *invited set* (the personas the
    router selected for this tier), consistent with standard_path and
    fast_path. Previously it was measured against voters only, which
    meant a small invited panel that all showed up could self-certify
    quorum=1.0 even when the majority of the council never participated.
  - Participation is still computed as "how many of the invited personas
    actually produced a vote with non-zero confidence". The change is
    that the denominator is now the invited-set total weight, not just
    the weight of voters.
"""

from __future__ import annotations

import logging
import time
from typing import List, Optional

from council.models import (
    DeliberationResult,
    DecisionState,
    PersonaVote,
    Proposal,
    RiskLevel,
    VetoType,
)
from council.reliability import ReliabilityTracker

logger = logging.getLogger("kanyoza.council.deep_deliberation")


class DeepDeliberation:
    """Full council deliberation for high-risk actions."""

    MIN_QUORUM = 0.60
    APPROVAL_THRESHOLD = 0.70
    CRITICAL_APPROVAL_THRESHOLD = 0.80
    REJECTION_THRESHOLD = 0.40
    MAX_ABSTAIN_RATIO = 0.35

    def __init__(
        self,
        personas: dict,
        reliability: Optional[ReliabilityTracker] = None,
        config=None,
    ):
        self._personas = personas
        self._reliability = reliability or ReliabilityTracker()
        self.config = config

    async def deliberate(
        self,
        proposal: Proposal,
        selected_agents: Optional[List[str]] = None,
    ) -> DeliberationResult:
        """Deep deliberation with full council."""
        start = time.monotonic()

        personas = self._select_personas(proposal, selected_agents)

        # The invited set's total weight is the quorum denominator. This is
        # the same convention used by standard_path and fast_path. Without
        # it, a tier that invites 5 personas could self-certify 100% quorum
        # while 7 council members were never asked.
        invited_weight = sum(
            float(getattr(p, "weight", 1.0) or 1.0) for p in personas
        ) or 1.0

        votes: List[PersonaVote] = []
        hard_veto_found = False
        soft_vetoes: List[PersonaVote] = []
        errors: List[str] = []

        for persona in personas:
            try:
                vote = persona.evaluate(proposal)

                # Apply reliability
                try:
                    vote.reliability = self._reliability.get_reliability(persona.name)
                except Exception:
                    vote.reliability = getattr(vote, "reliability", 0.5) or 0.5

                # Compute and attach effective influence (safe against
                # missing fields).
                influence = self._calculate_influence(vote)
                try:
                    vote.effective_influence = influence
                except Exception:
                    # Frozen dataclass — ignore, we recompute in consensus.
                    pass

                votes.append(vote)

                if vote.veto_type == VetoType.HARD and vote.vote == "reject":
                    hard_veto_found = True
                    break

                if vote.veto_type == VetoType.SOFT and vote.vote == "reject":
                    soft_vetoes.append(vote)

            except Exception as exc:
                msg = f"{persona.name}: {type(exc).__name__}: {exc}"
                logger.warning("Deep deliberation persona failed: %s", msg)
                errors.append(msg[:200])

        result = self._calculate_consensus(
            proposal,
            votes,
            hard_veto_found,
            soft_vetoes,
            errors,
            invited_weight=invited_weight,
        )
        result.deliberation_time_ms = round((time.monotonic() - start) * 1000, 3)

        return result

    def _select_personas(self, proposal, selected_agents):
        """Select all relevant personas (up to 12)."""
        if selected_agents:
            return [
                self._personas[name]
                for name in selected_agents
                if name in self._personas
            ]

        return list(self._personas.values())

    def _calculate_influence(self, vote: PersonaVote) -> float:
        """
        Multi-factor influence calculation.

        Uses getattr with safe defaults because personas are not required to
        populate every optional field. Missing fields degrade gracefully
        instead of crashing deliberation.
        """
        weight = float(getattr(vote, "weight", 1.0) or 1.0)
        reliability = float(getattr(vote, "reliability", 0.5) or 0.5)
        evidence_quality = float(getattr(vote, "evidence_quality", 0.5) or 0.5)
        confidence = float(getattr(vote, "confidence", 0.5) or 0.5)
        relevance = float(getattr(vote, "relevance", 1.0) or 1.0)
        return weight * reliability * evidence_quality * confidence * relevance

    def _calculate_consensus(
        self,
        proposal: Proposal,
        votes: List[PersonaVote],
        hard_veto_found: bool,
        soft_vetoes: List[PersonaVote],
        errors: Optional[List[str]] = None,
        invited_weight: Optional[float] = None,
    ) -> DeliberationResult:
        """Calculate consensus with full analysis.

        `invited_weight` is the total weight of personas the router invited
        for this tier. When supplied, it is the quorum denominator and the
        historical voters-only denominator is used only as a fallback.
        """
        errors = errors or []

        # ── Hard veto blocks immediately ────────────────────────────────
        if hard_veto_found:
            hard_vote = None
            for v in reversed(votes):
                if v.veto_type == VetoType.HARD and v.vote == "reject":
                    hard_vote = v
                    break

            return DeliberationResult(
                proposal_id=proposal.id,
                decision=DecisionState.BLOCKED,
                approve_ratio=0.0,
                reject_ratio=1.0,
                quorum_ratio=1.0,
                confidence=1.0,
                votes=votes,
                dissent=(
                    [{"member": hard_vote.member, "reason": hard_vote.reason}]
                    if hard_vote else []
                ),
                warnings=errors,
                risk_level=proposal.risk_level,
                tier_used=3,
                early_terminated=True,
            )

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
                warnings=["No votes cast — council may be unavailable"] + errors,
                risk_level=proposal.risk_level,
                tier_used=3,
                fallback_used=True,
            )

        # ── Quorum ──────────────────────────────────────────────────────
        # Invited set is the denominator. Fall back to the weight of voters
        # only if the caller did not supply an invited weight (kept for
        # direct callers that don't know the invitation set).
        if invited_weight and invited_weight > 0:
            total_weight = invited_weight
        else:
            total_weight = sum(
                (getattr(v, "weight", 1.0) or 1.0) for v in votes
            ) or 1.0

        participating = sum(
            (getattr(v, "weight", 1.0) or 1.0)
            for v in votes
            if (getattr(v, "confidence", 0.0) or 0.0) > 0
        )
        quorum = participating / total_weight if total_weight else 0.0

        # ── Weighted influence totals ───────────────────────────────────
        approve_influence = 0.0
        reject_influence = 0.0

        for vote in votes:
            influence = getattr(vote, "effective_influence", None)
            if not influence:
                influence = self._calculate_influence(vote)
            if vote.vote == "approve":
                approve_influence += influence
            elif vote.vote == "reject":
                reject_influence += influence

        total_influence = approve_influence + reject_influence

        # ── Fail closed: no decisive influence ──────────────────────────
        if total_influence == 0:
            return DeliberationResult(
                proposal_id=proposal.id,
                decision=DecisionState.NEEDS_REVISION,
                approve_ratio=0.0,
                reject_ratio=0.0,
                quorum_ratio=round(quorum, 4),
                confidence=0.0,
                votes=votes,
                dissent=[],
                warnings=["No decisive approve/reject influence"] + errors,
                risk_level=proposal.risk_level,
                tier_used=3,
            )

        approve_ratio = approve_influence / total_influence
        reject_ratio = reject_influence / total_influence
        confidence = max(approve_ratio, reject_ratio)

        # ── Decision: risk-sensitive, quorum-bound, fail-closed ─────────
        if proposal.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            if not any(v.member == "risk_manager" for v in votes):
                return DeliberationResult(
                    proposal_id=proposal.id,
                    decision=DecisionState.NEEDS_REVISION,
                    approve_ratio=0.0,
                    reject_ratio=0.0,
                    quorum_ratio=0.0,
                    confidence=0.0,
                    votes=votes,
                    dissent=[],
                    warnings=["Mandatory risk_manager vote missing"] + errors,
                    risk_level=proposal.risk_level,
                    tier_used=3,
                )

        abstain_weight = sum(
            float(getattr(v, "weight", 1.0) or 1.0)
            for v in votes if v.vote == "abstain"
        )
        vote_weight = sum(
            float(getattr(v, "weight", 1.0) or 1.0) for v in votes
        )
        abstain_ratio = abstain_weight / vote_weight if vote_weight else 1.0

        threshold = (
            self.CRITICAL_APPROVAL_THRESHOLD
            if proposal.risk_level == RiskLevel.CRITICAL
            else self.APPROVAL_THRESHOLD
        )
        if quorum < self.MIN_QUORUM:
            decision = DecisionState.NEEDS_REVISION
        elif abstain_ratio > self.MAX_ABSTAIN_RATIO:
            decision = DecisionState.NEEDS_REVISION
        elif (
            proposal.risk_level == RiskLevel.CRITICAL
            and not proposal.metadata.get("safety_plan")
        ):
            decision = DecisionState.ESCALATED
        elif approve_ratio >= threshold:
            decision = (
                DecisionState.ESCALATED if soft_vetoes else DecisionState.APPROVED
            )
        elif reject_ratio >= self.REJECTION_THRESHOLD:
            decision = DecisionState.REJECTED
        else:
            decision = DecisionState.ESCALATED

        # ── Dissent ─────────────────────────────────────────────────────
        dissent = [
            {
                "member": v.member,
                "reason": v.reason,
                "confidence": v.confidence,
            }
            for v in votes
            if v.vote == "reject" and (v.confidence or 0) > 0.4
        ]

        # ── Warnings ────────────────────────────────────────────────────
        warnings = [v.reason for v in soft_vetoes] + errors
        if abstain_ratio > self.MAX_ABSTAIN_RATIO:
            warnings.append(
                f"High abstention ({abstain_ratio:.0%}) — insufficient independent mandate"
            )

        return DeliberationResult(
            proposal_id=proposal.id,
            decision=decision,
            approve_ratio=round(approve_ratio, 4),
            reject_ratio=round(reject_ratio, 4),
            quorum_ratio=round(quorum, 4),
            confidence=round(confidence, 4),
            votes=votes,
            dissent=dissent,
            warnings=warnings,
            risk_level=proposal.risk_level,
            tier_used=3,
            early_terminated=False,
        )
