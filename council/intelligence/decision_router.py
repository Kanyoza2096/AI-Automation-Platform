"""Decision Router — routes proposals by risk level."""

from __future__ import annotations

import logging
from typing import List, Optional

from council.config import DemocracyConfig
from council.models import Proposal, RiskLevel
from council.policies.action_policy import ActionPolicy, ActionClass
from council.intelligence.risk_engine import RiskEngine

logger = logging.getLogger("kanyoza.council.router")


class DecisionRouter:
    """Routes proposals to appropriate voting tier."""
    
    def __init__(self, config: Optional[DemocracyConfig] = None):
        self.config = config or DemocracyConfig()
        self.action_policy = ActionPolicy()
        self._risk_engine = RiskEngine()
    
    def route(self, proposal: Proposal) -> int:
        """
        Determine voting tier:
        0 = Auto-approve (trivial/read-only)
        1 = Fast path (low risk, 1-2 personas)
        2 = Standard path (medium risk, 3-5 personas)
        3 = Deep deliberation (high/critical, 5-12 personas)
        """
        action_class = self.action_policy.classify(proposal.action_name)
        requirements = self.action_policy.get_requirements(action_class)

        # Risk is authoritative at the routing boundary. A caller-supplied
        # low/trivial label can never downgrade a dangerous action. The
        # independent RiskEngine can only raise the floor further.
        independent_risk = self._independent_risk(proposal)
        
        # Map action class to risk level
        risk_map = {
            ActionClass.READ_ONLY: RiskLevel.TRIVIAL,
            ActionClass.LOW_RISK: RiskLevel.LOW,
            ActionClass.MEDIUM_RISK: RiskLevel.MEDIUM,
            ActionClass.HIGH_RISK: RiskLevel.HIGH,
            ActionClass.CRITICAL: RiskLevel.CRITICAL,
            ActionClass.IRREVERSIBLE: RiskLevel.CRITICAL,
        }
        
        classified_risk = risk_map.get(action_class, RiskLevel.MEDIUM)
        risk_order = {
            RiskLevel.TRIVIAL: 0, RiskLevel.LOW: 1, RiskLevel.MEDIUM: 2,
            RiskLevel.HIGH: 3, RiskLevel.CRITICAL: 4,
        }
        proposal.risk_level = (
            independent_risk
            if risk_order[independent_risk] > risk_order[classified_risk]
            else classified_risk
        )

        if proposal.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            return 3
        return requirements["tier"]

    def _independent_risk(self, proposal: Proposal) -> RiskLevel:
        try:
            return self._risk_engine.assess_risk(proposal)
        except Exception:
            return RiskLevel.MEDIUM

    @staticmethod
    def _all_persona_names() -> List[str]:
        from council.personas import ALL_PERSONAS
        return [getattr(cls, "name", "") for cls in ALL_PERSONAS if getattr(cls, "name", "")]
    
    def select_agents(self, proposal: Proposal, tier: int) -> Optional[List[str]]:
        """Select relevant agents based on proposal type."""
        if tier == 0:
            return []
        
        agent_map = self.config.agent_selection_map
        proposal_type = proposal.proposal_type or "general"
        
        selected = agent_map.get(proposal_type)
        
        if selected is None:
            selected = [name for name in self._all_persona_names()]

        # High/critical decisions require independent safety coverage even
        # when a proposal type's curated panel omitted the risk persona.
        if tier == 3:
            # Deep deliberation is a full-council operation. A curated
            # proposal-type panel is appropriate for low/medium risk, but it
            # must not silently shrink a high/critical decision to a handful
            # of friendly voters. Include every registered persona and ensure
            # safety/evidence voices are present.
            all_names = self._all_persona_names()
            ordered = list(selected) + [name for name in all_names if name not in selected]
            return ordered[:12]
        
        if tier == 1:
            return selected[:2]
        elif tier == 2:
            return selected[:5]
        else:
            return selected