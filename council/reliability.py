"""Reliability tracking for personas."""

from __future__ import annotations

from typing import Dict, Optional


class ReliabilityTracker:
    """Tracks persona accuracy over time."""
    
    def __init__(self, minimum_samples: int = 10):
        self.minimum_samples = minimum_samples
        self._stats: Dict[str, Dict[str, float]] = {}
    
    def record_outcome(
        self,
        persona_name: str,
        vote: str,
        actual_outcome: str,
    ) -> None:
        """Update persona reliability."""
        stats = self._stats.setdefault(persona_name, {
            "correct": 0, "total": 0, "accuracy": 0.5,
        })
        
        was_correct = (
            (vote == "approve" and actual_outcome == "success")
            or (vote == "reject" and actual_outcome == "failure")
            or (vote == "abstain")
        )
        
        stats["total"] += 1
        if was_correct:
            stats["correct"] += 1
        stats["accuracy"] = stats["correct"] / stats["total"]
    
    def get_reliability(self, persona_name: str) -> float:
        stats = self._stats.get(persona_name)
        if not stats or stats["total"] < self.minimum_samples:
            return 0.5  # Default reliability
        return stats["accuracy"]
    
    def get_all(self) -> Dict[str, float]:
        return {
            name: self.get_reliability(name)
            for name in self._stats
        }