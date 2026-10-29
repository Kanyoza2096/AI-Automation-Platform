"""Outcome memory for the Democracy Engine."""

from __future__ import annotations

from collections import deque
from typing import List, Optional

from council.models import DecisionState, OutcomeRecord


class OutcomeMemory:
    """Stores real-world outcomes for learning."""
    
    def __init__(self, max_entries: int = 1000):
        self._records: deque = deque(maxlen=max_entries)
    
    def record(
        self,
        proposal_id: str,
        decision: DecisionState,
        actual_outcome: str,
        success_metric: float = 0.5,
        notes: str = "",
    ) -> None:
        """Record an outcome."""
        self._records.append(OutcomeRecord(
            proposal_id=proposal_id,
            decision=decision,
            actual_outcome=actual_outcome,
            success_metric=success_metric,
            notes=notes,
        ))
    
    def get_recent(self, limit: int = 50) -> List[OutcomeRecord]:
        """Get recent outcomes."""
        return list(self._records)[-limit:]
    
    @property
    def size(self) -> int:
        return len(self._records)