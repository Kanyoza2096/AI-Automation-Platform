"""Youth Advocate persona."""

from council.models import Proposal, PersonaVote
from council.personas.base import Persona


class YouthAdvocate(Persona):
    name = "youth_advocate"
    role = "Youth Voice (Age 22)"
    weight = 2.0
    
    YOUTH_INTERESTS = {
        "church": ("youth", "worship", "fellowship", "bible", "praise"),
        "school": ("study", "career", "scholarship", "success", "exam"),
        "hospital": ("fitness", "nutrition", "mental health", "wellness"),
        "tech": ("coding", "ai", "startup", "digital", "innovation"),
        "retail": ("style", "trending", "new", "sale", "fashion"),
        "general": ("opportunity", "growth", "future", "skills"),
    }
    BORING_WORDS = ("policy", "regulation", "compliance", "formal notice")
    
    def evaluate(self, proposal: Proposal) -> PersonaVote:
        topic = str(proposal.metadata.get("topic", "")).lower()
        industry = str(proposal.metadata.get("industry", "general")).lower() or "general"
        
        interests = self.YOUTH_INTERESTS.get(industry, self.YOUTH_INTERESTS["general"])
        
        for boring in self.BORING_WORDS:
            if boring in topic:
                return self._vote_with_behavior(
                    proposal.id, "reject",
                    f"Too formal for young audience: '{boring}'.",
                    0.85, {"matched_term": boring},
                )
        
        for interest in interests:
            if interest in topic:
                return self._vote_with_behavior(
                    proposal.id, "approve",
                    f"Relevant to youth in {industry}.",
                    0.80, {"matched_interest": interest},
                )
        
        hour = int(proposal.metadata.get("hour", 12) or 12)
        if 18 <= hour <= 23:
            return self._vote_with_behavior(
                proposal.id, "approve",
                "Evening timing is good for youth.",
                0.70, {"hour": hour},
            )
        
        return self._vote_with_behavior(
            proposal.id, "abstain",
            "No strong youth signal.",
            0.40,
        )