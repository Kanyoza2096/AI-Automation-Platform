"""Rippler persona — momentum tracker."""

from council.models import Proposal, PersonaVote
from council.personas.base import Persona


class Rippler(Persona):
    name = "rippler"
    role = "Momentum Tracker"
    weight = 2.0
    
    def evaluate(self, proposal: Proposal) -> PersonaVote:
        similarity = float(proposal.metadata.get("similarity_to_success", 0) or 0)
        momentum = float(proposal.metadata.get("engagement_momentum", 0) or 0)
        
        if proposal.metadata.get("recent_success") and similarity > 0.7:
            return self._vote_with_behavior(
                proposal.id, "approve",
                f"Ride the wave! ({similarity:.0%} match)",
                0.85, {"similarity": round(similarity, 3)},
            )
        
        if momentum > 0.2:
            return self._vote_with_behavior(
                proposal.id, "approve",
                f"Positive momentum ({momentum:.2f}).",
                0.75, {"momentum": momentum},
            )
        
        if momentum < -0.2:
            return self._vote_with_behavior(
                proposal.id, "reject",
                f"Negative momentum ({momentum:.2f}).",
                0.70, {"momentum": momentum},
            )
        
        return self._vote_with_behavior(
            proposal.id, "abstain",
            "No momentum signal.",
            0.40,
        )