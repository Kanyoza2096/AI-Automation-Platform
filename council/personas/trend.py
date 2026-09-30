"""Trend Spotter persona."""

from council.models import Proposal, PersonaVote
from council.personas.base import Persona


class TrendSpotter(Persona):
    name = "trend_spotter"
    role = "Trend Voice"
    weight = 2.0
    
    TRENDING_WORDS = ("viral", "trending", "challenge", "new", "launch")
    
    def evaluate(self, proposal: Proposal) -> PersonaVote:
        topic = str(proposal.metadata.get("topic", "")).lower()
        
        for trend in self.TRENDING_WORDS:
            if trend in topic:
                return self._vote_with_behavior(
                    proposal.id, "approve",
                    f"Trend detected: '{trend}' — don't miss out!",
                    0.85, {"matched_term": trend},
                )
        
        if str(proposal.metadata.get("topic_source", "")).lower() == "trend_intelligence":
            return self._vote_with_behavior(
                proposal.id, "approve",
                "Sourced from Trend Intelligence.",
                0.80, {"topic_source": "trend_intelligence"},
            )
        
        return self._vote_with_behavior(
            proposal.id, "abstain",
            "No trend signal.",
            0.40,
        )