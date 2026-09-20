"""Community Elder persona with hard veto."""

from council.models import Proposal, PersonaVote, VetoType
from council.personas.base import Persona


class CommunityElder(Persona):
    name = "community_elder"
    role = "Community Elder (Age 65)"
    weight = 3.0
    hard_veto_power = True
    
    DISRESPECTFUL = ("stupid", "lazy", "worthless", "hate", "dumb", "idiot")
    DANGEROUS_HEALTH = ("miracle cure", "unproven", "fake", "stop medication")
    COMMUNITY_VALUES = ("community", "family", "together", "support", "unity")
    
    def evaluate(self, proposal: Proposal) -> PersonaVote:
        content = str(proposal.description).lower()
        topic = str(proposal.metadata.get("topic", "")).lower()
        industry = str(proposal.metadata.get("industry", "")).lower()
        combined = f"{topic} {content}"
        
        for disrespectful in self.DISRESPECTFUL:
            if disrespectful in combined:
                return self._vote_with_behavior(
                    proposal.id, "reject",
                    f"HARD VETO: Disrespectful: '{disrespectful}'.",
                    1.0, {"matched_term": disrespectful, "veto": True},
                    VetoType.HARD,
                )
        
        if industry == "hospital":
            for dangerous in self.DANGEROUS_HEALTH:
                if dangerous in combined:
                    return self._vote_with_behavior(
                        proposal.id, "reject",
                        f"HARD VETO: Dangerous health claim: '{dangerous}'.",
                        1.0, {"matched_term": dangerous, "veto": True},
                        VetoType.HARD,
                    )
        
        for value in self.COMMUNITY_VALUES:
            if value in content:
                return self._vote_with_behavior(
                    proposal.id, "approve",
                    f"Reflects community values.",
                    0.85, {"matched_value": value},
                )
        
        if industry == "church":
            day = str(proposal.metadata.get("day", "")).lower()
            hour = int(proposal.metadata.get("hour", 0) or 0)
            if day == "sunday" and 8 <= hour <= 12:
                return self._vote_with_behavior(
                    proposal.id, "reject",
                    "Conflicts with Sunday worship.",
                    0.80, {"day": day, "hour": hour},
                )
        
        return self._vote_with_behavior(
            proposal.id, "approve",
            "Socially acceptable.",
            0.65,
        )