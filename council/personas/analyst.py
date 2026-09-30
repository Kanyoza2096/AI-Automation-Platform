"""Data Analyst persona."""

from council.models import Proposal, PersonaVote
from council.personas.base import Persona


class DataAnalyst(Persona):
    name = "data_analyst"
    role = "Data Voice"
    weight = 2.0
    
    def evaluate(self, proposal: Proposal) -> PersonaVote:
        engagement_ratio = float(proposal.metadata.get("engagement_ratio", 1.0) or 1.0)
        sample_count = int(proposal.metadata.get("sample_count", 0) or 0)
        
        if sample_count < 5:
            return self._vote_with_behavior(
                proposal.id, "abstain",
                f"Insufficient data (n={sample_count}).",
                0.20, {"sample_count": sample_count},
            )
        
        if engagement_ratio >= 1.5:
            return self._vote_with_behavior(
                proposal.id, "approve",
                f"Strong engagement ({engagement_ratio:.2f}x baseline).",
                0.90, {"engagement_ratio": round(engagement_ratio, 3)},
            )
        
        if engagement_ratio <= 0.5:
            return self._vote_with_behavior(
                proposal.id, "reject",
                f"Poor engagement ({engagement_ratio:.2f}x).",
                0.85, {"engagement_ratio": round(engagement_ratio, 3)},
            )
        
        return self._vote_with_behavior(
            proposal.id, "abstain",
            "Data inconclusive.",
            0.50, {"engagement_ratio": round(engagement_ratio, 3)},
        )