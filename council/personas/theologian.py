"""Theologian persona — spiritual/ethical advisor for sensitive content."""

from __future__ import annotations

from typing import Any, Dict, Optional

from council.models import Proposal, PersonaVote, VetoType
from council.personas.base import Persona


class Theologian(Persona):
    """Spiritual and ethical advisor — especially for church/religious content."""
    
    name = "theologian"
    role = "Spiritual & Ethics Advisor"
    weight = 2.5
    hard_veto_power = True
    
    # Content that should NEVER be posted (regardless of industry)
    BLASPHEMOUS_TERMS = {
        "mocking god", "jesus was fake", "bible is fake",
        "god is dead", "religion is stupid", "faith is worthless",
        "mock the holy", "sacrilegious", "blasphemy",
    }
    
    # Content requiring EXTREME caution for hospital/health
    DANGEROUS_SPIRITUAL_CLAIMS = {
        "faith healing only", "prayer instead of medicine",
        "god will heal without doctors", "stop taking medication",
        "miracle cure guaranteed", "faith replaces treatment",
        "divine healing only", "no need for hospital",
    }
    
    # Content that could cause division in church community
    DIVISIVE_TOPICS = {
        "denomination war", "which church is right",
        "other faiths are wrong", "political endorsement",
        "candidate is gods choice", "vote for this politician",
    }
    
    # Positive spiritual content indicators
    SPIRITUAL_VALUES = {
        "love", "grace", "mercy", "forgiveness", "hope",
        "faith", "community", "service", "compassion",
        "unity", "peace", "gratitude", "humility",
        "kindness", "generosity", "wisdom", "prayer",
    }
    
    def evaluate(self, proposal: Proposal) -> PersonaVote:
        """Evaluate proposal from spiritual/ethical perspective."""
        
        content = self._safe_str(proposal.description)
        topic = self._safe_str(proposal.metadata.get("topic", ""))
        industry = self._safe_str(proposal.metadata.get("industry", "general")).lower()
        combined_text = f"{topic} {content}".lower()
        
        # ── HARD VETO: Blasphemous content ─────────────────────────
        for term in self.BLASPHEMOUS_TERMS:
            if term in combined_text:
                return self._vote_with_behavior(
                    proposal_id=proposal.id,
                    base_vote="reject",
                    reason=f"HARD VETO: Blasphemous content detected: '{term}'.",
                    confidence=1.0,
                    veto_type=VetoType.HARD,
                    evidence={"matched_term": term, "category": "blasphemy"},
                )
        
        # ── HARD VETO: Dangerous spiritual health claims ───────────
        if industry in ("hospital", "healthcare", "medical", "church"):
            for term in self.DANGEROUS_SPIRITUAL_CLAIMS:
                if term in combined_text:
                    return self._vote_with_behavior(
                        proposal_id=proposal.id,
                        base_vote="reject",
                        reason=f"HARD VETO: Dangerous spiritual health claim: '{term}'.",
                        confidence=1.0,
                        veto_type=VetoType.HARD,
                        evidence={"matched_term": term, "category": "dangerous_health_claim"},
                    )
        
        # ── HARD VETO: Divisive religious content ─────────────────
        for term in self.DIVISIVE_TOPICS:
            if term in combined_text:
                return self._vote_with_behavior(
                    proposal_id=proposal.id,
                    base_vote="reject",
                    reason=f"HARD VETO: Divisive religious content: '{term}'.",
                    confidence=0.95,
                    veto_type=VetoType.HARD,
                    evidence={"matched_term": term, "category": "divisive"},
                )
        
        # ── POSITIVE: Spiritual values present ─────────────────────
        matched_values = [v for v in self.SPIRITUAL_VALUES if v in combined_text]
        if matched_values:
            return self._vote_with_behavior(
                proposal_id=proposal.id,
                base_vote="approve",
                reason=f"Reflects spiritual values: {', '.join(matched_values[:3])}.",
                confidence=0.80,
                evidence={"matched_values": matched_values[:5]},
            )
        
        # ── CHURCH INDUSTRY SPECIFIC ───────────────────────────────
        if industry == "church":
            day = self._safe_str(proposal.metadata.get("day", "")).lower()
            hour = self._safe_int(proposal.metadata.get("hour", 12), 12)
            
            # Sunday is for worship
            if day == "sunday" and 8 <= hour <= 12:
                return self._vote_with_behavior(
                    proposal_id=proposal.id,
                    base_vote="reject",
                    reason="Conflicts with Sunday worship window.",
                    confidence=0.75,
                    evidence={"day": day, "hour": hour},
                )
            
            # Late night church posts are inappropriate
            if hour > 22 or hour < 5:
                return self._vote_with_behavior(
                    proposal_id=proposal.id,
                    base_vote="reject",
                    reason="Inappropriate time for spiritual content.",
                    confidence=0.70,
                    evidence={"hour": hour},
                )
        
        # ── DEFAULT: Neutral ──────────────────────────────────────
        return self._vote_with_behavior(
            proposal_id=proposal.id,
            base_vote="abstain",
            reason="No specific spiritual concerns or affirmations.",
            confidence=0.45,
        )
    
    @staticmethod
    def _safe_str(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()
    
    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default