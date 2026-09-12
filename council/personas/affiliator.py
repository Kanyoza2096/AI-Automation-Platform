"""Affiliate Marketer persona — revenue-focused voting member."""

from __future__ import annotations

from typing import Any, Dict, Optional

from council.models import Proposal, PersonaVote, VetoType
from council.personas.base import Persona


class AffiliateMarketer(Persona):
    """Revenue-focused persona that evaluates affiliate opportunities."""
    
    name = "affiliate_marketer"
    role = "Revenue Voice"
    weight = 3.0  # High weight — money matters!
    
    MIN_COMMISSION = 5.0       # Below this = not worth it
    GOOD_COMMISSION = 20.0     # Decent
    GREAT_COMMISSION = 30.0    # High value
    EXCELLENT_COMMISSION = 50.0  # Exceptional
    
    RECURRING_BONUS = 1.3      # Recurring commissions are worth more
    HIGH_AUDIENCE_FIT_BONUS = 1.2
    
    def evaluate(self, proposal: Proposal) -> PersonaVote:
        """Evaluate affiliate opportunity in proposal."""
        
        # Extract affiliate data
        commission_rate = self._safe_float(proposal.metadata.get("commission_rate", 0))
        recurring = bool(proposal.metadata.get("recurring", False))
        audience_fit = self._safe_float(proposal.metadata.get("audience_fit_score", 0.5))
        product_name = proposal.metadata.get("product_name", "")
        conversion_rate = self._safe_float(proposal.metadata.get("conversion_rate", 0.02))
        
        # No affiliate opportunity
        if commission_rate == 0:
            return self._vote_with_behavior(
                proposal_id=proposal.id,
                base_vote="abstain",
                reason="No affiliate opportunity in this proposal.",
                confidence=0.30,
            )
        
        # Calculate effective commission value
        effective_value = commission_rate
        
        # Bonus for recurring
        if recurring:
            effective_value *= self.RECURRING_BONUS
            reason_suffix = f" (recurring {commission_rate}%)"
        else:
            reason_suffix = f" (one-time {commission_rate}%)"
        
        # Bonus for audience fit
        if audience_fit > 0.7:
            effective_value *= self.HIGH_AUDIENCE_FIT_BONUS
        
        # Excellent opportunity
        if effective_value >= self.EXCELLENT_COMMISSION:
            return self._vote_with_behavior(
                proposal_id=proposal.id,
                base_vote="approve",
                reason=f"🔥 EXCELLENT affiliate opportunity: {product_name}{reason_suffix} — high value!",
                confidence=0.95,
                evidence={
                    "commission_rate": commission_rate,
                    "recurring": recurring,
                    "audience_fit": audience_fit,
                    "effective_value": round(effective_value, 2),
                },
            )
        
        # Great opportunity
        if effective_value >= self.GREAT_COMMISSION:
            return self._vote_with_behavior(
                proposal_id=proposal.id,
                base_vote="approve",
                reason=f"💰 Great affiliate deal: {product_name}{reason_suffix}.",
                confidence=0.85,
                evidence={
                    "commission_rate": commission_rate,
                    "effective_value": round(effective_value, 2),
                },
            )
        
        # Good opportunity
        if effective_value >= self.GOOD_COMMISSION:
            return self._vote_with_behavior(
                proposal_id=proposal.id,
                base_vote="approve",
                reason=f"Decent affiliate opportunity: {product_name}{reason_suffix}.",
                confidence=0.70,
                evidence={"commission_rate": commission_rate},
            )
        
        # Marginal opportunity
        if effective_value >= self.MIN_COMMISSION:
            return self._vote_with_behavior(
                proposal_id=proposal.id,
                base_vote="abstain",
                reason=f"Marginal commission ({commission_rate}%) — low priority.",
                confidence=0.40,
            )
        
        # Too low
        return self._vote_with_behavior(
            proposal_id=proposal.id,
            base_vote="reject",
            reason=f"Commission too low ({commission_rate}%) — not worth a post slot.",
            confidence=0.75,
        )
    
    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default