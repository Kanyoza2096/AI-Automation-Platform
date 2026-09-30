"""Veto policy — prevents single persona domination."""

from dataclasses import dataclass
from typing import List, Optional

from council.models import PersonaVote, VetoType


@dataclass
class VetoRequirement:
    """Requirements for a valid veto."""
    authorized_members: List[str]
    required_conditions: List[str]
    minimum_confidence: float
    policy_match_required: bool
    audit_event_required: bool = True


class VetoPolicy:
    """Controls when vetoes are valid."""
    
    HARD_VETO_REQUIREMENTS = {
        "community_elder": VetoRequirement(
            authorized_members=["community_elder"],
            required_conditions=["disrespect", "dangerous_health_claim"],
            minimum_confidence=0.90,
            policy_match_required=True,
        ),
        "risk_manager": VetoRequirement(
            authorized_members=["risk_manager"],
            required_conditions=["dangerous_action", "security_violation"],
            minimum_confidence=0.95,
            policy_match_required=True,
        ),
        "theologian": VetoRequirement(
            authorized_members=["theologian"],
            required_conditions=["blasphemy", "dangerous_spiritual_claim", "divisive_content"],
            minimum_confidence=0.95,
            policy_match_required=True,
        ),
    }
    
    def is_valid_hard_veto(self, vote: PersonaVote) -> bool:
        """Check if a hard veto is valid."""
        if vote.veto_type != VetoType.HARD:
            return False
        
        requirement = self.HARD_VETO_REQUIREMENTS.get(vote.member)
        if not requirement:
            return False
        
        if vote.member not in requirement.authorized_members:
            return False
        
        if vote.confidence < requirement.minimum_confidence:
            return False
        
        return True
    
    def is_valid_soft_veto(self, vote: PersonaVote) -> bool:
        """Check if a soft veto is valid."""
        if vote.veto_type != VetoType.SOFT:
            return False
        
        return vote.confidence >= 0.70
    
    def validate_vetoes(self, votes: List[PersonaVote]) -> List[PersonaVote]:
        """Filter invalid vetoes."""
        valid = []
        for vote in votes:
            if vote.veto_type == VetoType.HARD and self.is_valid_hard_veto(vote):
                valid.append(vote)
            elif vote.veto_type == VetoType.SOFT and self.is_valid_soft_veto(vote):
                valid.append(vote)
        return valid