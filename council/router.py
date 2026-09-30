"""Decision router — routes proposals by risk level."""

from __future__ import annotations

import logging
from typing import List, Optional

from council.config import DemocracyConfig
from council.models import Proposal, RiskLevel
from council.risk import RiskAssessor

logger = logging.getLogger("kanyoza.council.router")


class DecisionRouter:
    """Routes proposals to appropriate voting tier."""
    
    def __init__(self, config: Optional[DemocracyConfig] = None):
        self.config = config or DemocracyConfig()
        self.risk_assessor = RiskAssessor()
    
    def route(self, proposal: Proposal) -> int:
        """
        Determine voting tier for proposal.
        
        Returns:
            0 = Auto-approve (no council)
            1 = Mini council (2-4 agents)
            2 = Standard council (5 agents)
            3 = Full council (10 agents)
        """
        risk = self.risk_assessor.assess(proposal)
        proposal.risk_level = risk
        
        if risk == RiskLevel.TRIVIAL:
            return 0
        elif risk == RiskLevel.LOW:
            return 1
        elif risk == RiskLevel.MEDIUM:
            return 2
        elif risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            return 3
        
        return 2  # Default
    
    def select_agents(self, proposal: Proposal, tier: int) -> Optional[List[str]]:
        """Select relevant agents for the proposal type."""
        if tier == 0:
            return []
        
        agent_map = self.config.agent_selection_map
        proposal_type = proposal.proposal_type or "general"
        
        selected = agent_map.get(proposal_type, None)
        
        if selected is None:
            # Use all agents
            return None
        
        # Limit by tier
        if tier == 1:
            return selected[:2]
        elif tier == 2:
            return selected[:5]
        else:
            return selected