"""Base Persona class with human-like traits."""

from __future__ import annotations

import hashlib
import random
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from council.models import PersonaVote, VetoType, Proposal


class Persona:
    """Base persona with controlled behavioral variance."""
    
    name: str = "persona"
    role: str = "Community Member"
    weight: float = 1.0
    veto_power: bool = False
    hard_veto_power: bool = False
    
    MOOD_GOOD = "good"
    MOOD_NEUTRAL = "neutral"
    MOOD_TIRED = "tired"
    MOOD_GRUMPY = "grumpy"
    MOOD_EXCITED = "excited"
    
    def __init__(self):
        self._vote_count = 0
        self._mood = self.MOOD_NEUTRAL
        self._fatigue_threshold = 50
        self._good_day = True
        self._distraction_rate = 0.10
        self._reliability = 0.5
        self._memory: Dict[str, Any] = {}
    
    def _seeded_random(self, proposal_id: str, member_name: str) -> random.Random:
        """Seeded randomness for reproducible behavior."""
        seed_payload = f"{proposal_id}:{member_name}:{self.name}"
        seed = int(hashlib.sha256(seed_payload.encode()).hexdigest()[:8], 16)
        return random.Random(seed)
    
    def _vote_with_behavior(
        self,
        proposal_id: str,
        base_vote: str,
        reason: str,
        confidence: float,
        evidence: Optional[Dict[str, Any]] = None,
        veto_type: VetoType = VetoType.NONE,
    ) -> PersonaVote:
        """Apply behavioral variance to vote."""
        self._vote_count += 1
        
        # Fatigue effect
        if self._vote_count > self._fatigue_threshold:
            confidence = confidence * 0.6
            if base_vote == "approve":
                base_vote = "abstain"
                reason = f"{reason} (Fatigued)"
        
        return PersonaVote(
            member=self.name,
            role=self.role,
            vote=base_vote,
            confidence=round(max(0.0, min(1.0, confidence)), 3),
            reason=reason[:500],
            weight=self.weight,
            reliability=self._reliability,
            veto_type=veto_type,
            evidence=evidence,
            evidence_quality=0.8 if evidence else 0.45,
            relevance=1.0,
        )
    
    def _is_distracted(self, proposal_id: str = "") -> bool:
        """Probabilistic distraction (uses seeded RNG when proposal_id given)."""
        rate = getattr(self, "_distraction_rate", 0.10)
        if not rate or rate <= 0:
            return False
        if proposal_id:
            rng = self._seeded_random(proposal_id, self.name)
            return rng.random() < rate
        return random.random() < rate

    def evaluate(self, proposal: Proposal) -> PersonaVote:
        raise NotImplementedError
    
    def update_reliability(self, accuracy: float) -> None:
        """Update persona reliability based on outcomes."""
        self._reliability = accuracy
    
    @property
    def reliability(self) -> float:
        return self._reliability