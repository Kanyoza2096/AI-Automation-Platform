"""Customer Voice persona."""

from council.models import Proposal, PersonaVote
from council.personas.base import Persona


class CustomerVoice(Persona):
    name = "customer_voice"
    role = "Audience Representative"
    weight = 3.0
    
    def evaluate(self, proposal: Proposal) -> PersonaVote:
        content = str(proposal.description)
        
        # Distraction effect
        if self._is_distracted(getattr(proposal, 'id', '') or ''):
            truncated = content[:100]
            if "?" in truncated:
                return self._vote_with_behavior(
                    proposal.id, "approve",
                    "Saw a question. Good enough.",
                    0.55, {"distracted": True},
                )
            return self._vote_with_behavior(
                proposal.id, "abstain",
                "Didn't finish reading.",
                0.30, {"distracted": True},
            )
        
        if "?" in content:
            return self._vote_with_behavior(
                proposal.id, "approve",
                "Invites audience participation.",
                0.80, {"question_detected": True},
            )
        
        if len(content) > 500:
            return self._vote_with_behavior(
                proposal.id, "reject",
                "Too long for fast scrolling.",
                0.75, {"char_count": len(content)},
            )
        
        if 0 < len(content) < 200:
            return self._vote_with_behavior(
                proposal.id, "approve",
                "Short and punchy!",
                0.70, {"char_count": len(content)},
            )
        
        return self._vote_with_behavior(
            proposal.id, "abstain",
            "Average length.",
            0.50,
        )