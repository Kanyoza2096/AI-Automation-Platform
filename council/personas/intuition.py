"""Gut Feeling persona."""

from council.models import Proposal, PersonaVote
from council.personas.base import Persona


class GutFeeling(Persona):
    name = "gut_feeling"
    role = "Intuition"
    weight = 1.0
    
    def evaluate(self, proposal: Proposal) -> PersonaVote:
        # Seeded randomness for reproducibility
        rng = self._seeded_random(proposal.id, self.name)
        bucket = rng.randint(0, 99)
        
        if bucket < 10:
            return self._vote_with_behavior(
                proposal.id, "approve",
                "Gut says YES!",
                0.60, {"bucket": bucket},
            )
        if bucket < 15:
            return self._vote_with_behavior(
                proposal.id, "approve",
                "Try something different.",
                0.55, {"bucket": bucket},
            )
        
        return self._vote_with_behavior(
            proposal.id, "abstain",
            "No strong intuition.",
            0.30, {"bucket": bucket},
        )