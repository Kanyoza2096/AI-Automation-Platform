"""Business Owner persona."""

from council.models import Proposal, PersonaVote
from council.personas.base import Persona


class BusinessOwner(Persona):
    name = "business_owner"
    role = "Business Voice (Age 45)"
    weight = 3.0
    
    PRACTICAL_WORDS = ("how to", "tips", "guide", "simple", "easy", "steps")
    TECHNICAL_WORDS = ("api", "algorithm", "backend", "database", "code")
    
    def evaluate(self, proposal: Proposal) -> PersonaVote:
        topic = str(proposal.metadata.get("topic", "")).lower()
        content = str(proposal.description).lower()
        combined = f"{topic} {content}"
        
        # Grudge memory
        topic_key = f"topic:{topic}"
        if topic_key in self._memory:
            if self._memory[topic_key].get("failed_count", 0) >= 2:
                return self._vote_with_behavior(
                    proposal.id, "reject",
                    f"We've tried '{topic}' twice and it failed. Never again.",
                    0.90, {"grudge": True},
                )
        
        for tech in self.TECHNICAL_WORDS:
            if tech in topic:
                return self._vote_with_behavior(
                    proposal.id, "reject",
                    f"Too technical for business audience: '{tech}'.",
                    0.85, {"matched_term": tech},
                )
        
        for practical in self.PRACTICAL_WORDS:
            if practical in combined:
                return self._vote_with_behavior(
                    proposal.id, "approve",
                    f"Practical value communicated.",
                    0.80, {"matched_term": practical},
                )
        
        if proposal.metadata.get("has_cta"):
            return self._vote_with_behavior(
                proposal.id, "approve",
                "Clear call-to-action present.",
                0.75, {"has_cta": True},
            )
        
        return self._vote_with_behavior(
            proposal.id, "abstain",
            "Business value not explicit.",
            0.50,
        )