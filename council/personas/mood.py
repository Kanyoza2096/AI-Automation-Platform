"""Mood Ring persona."""

from council.models import Proposal, PersonaVote
from council.personas.base import Persona


class MoodRing(Persona):
    name = "mood_ring"
    role = "System Mood"
    weight = 1.0
    
    def evaluate(self, proposal: Proposal) -> PersonaVote:
        system_health = float(proposal.metadata.get("system_health", 80) or 80)
        engagement_trend = float(proposal.metadata.get("engagement_trend", 0) or 0)
        hour = int(proposal.metadata.get("hour", 12) or 12)
        
        if hour < 5:
            return self._vote_with_behavior(
                proposal.id, "reject",
                "System is asleep. Don't post now.",
                0.85, {"hour": hour},
            )
        
        if system_health > 90 and engagement_trend > 0.1:
            return self._vote_with_behavior(
                proposal.id, "approve",
                "System thriving — go bold!",
                0.85, {"system_health": system_health},
            )
        
        if system_health > 70:
            return self._vote_with_behavior(
                proposal.id, "approve",
                "System stable.",
                0.70, {"system_health": system_health},
            )
        
        if system_health > 50:
            return self._vote_with_behavior(
                proposal.id, "abstain",
                "System under pressure.",
                0.50, {"system_health": system_health},
            )
        
        return self._vote_with_behavior(
            proposal.id, "reject",
            "System in crisis — hold.",
            0.90, {"system_health": system_health},
        )