"""
Fallback Strategy — When Council is unavailable.

Fail OPEN for low-risk actions.
Fail CLOSED for high-risk actions.
"""

from __future__ import annotations

import logging
from typing import Optional

from council.models import (
    DeliberationResult, DecisionState, Proposal, RiskLevel,
)

logger = logging.getLogger("kanyoza.council.fallback")


class FallbackStrategy:
    """Degradable council fallback behavior."""
    
    def __init__(self, config=None):
        self.config = config
    
    def evaluate(self, proposal: Proposal, tier: int) -> DeliberationResult:
        """
        Evaluate proposal when Council is unavailable.
        
        Rules:
        - TRIVIAL → APPROVED (no council needed)
        - LOW → APPROVED_WITH_WARNINGS (fail open)
        - MEDIUM → NEEDS_REVISION (conservative)
        - HIGH → BLOCKED (fail closed)
        - CRITICAL → BLOCKED (fail closed)
        - IRREVERSIBLE → BLOCKED (fail closed)
        """
        risk = proposal.risk_level
        
        if risk == RiskLevel.TRIVIAL:
            decision = DecisionState.APPROVED
            confidence = 1.0
            warning = None
        elif risk == RiskLevel.LOW:
            decision = DecisionState.APPROVED_WITH_WARNINGS
            confidence = 0.6
            warning = "Council unavailable — auto-approved with warnings (fail open)"
        elif risk == RiskLevel.MEDIUM:
            decision = DecisionState.NEEDS_REVISION
            confidence = 0.5
            warning = "Council unavailable — medium risk requires revision"
        else:
            decision = DecisionState.BLOCKED
            confidence = 1.0
            warning = f"Council unavailable — {risk.value} risk action blocked (fail closed)"
        
        logger.warning("Fallback strategy for %s: %s", proposal.action_name, decision.value)
        
        return DeliberationResult(
            proposal_id=proposal.id,
            decision=decision,
            approve_ratio=0.6 if decision == DecisionState.APPROVED_WITH_WARNINGS else 0.0,
            reject_ratio=1.0 if decision == DecisionState.BLOCKED else 0.0,
            quorum_ratio=0.0,
            confidence=confidence,
            votes=[],
            dissent=[],
            warnings=[warning] if warning else [],
            risk_level=risk,
            tier_used=tier,
            fallback_used=True,
        )