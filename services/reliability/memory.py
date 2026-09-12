"""Incident memory — learn successful remediations over time."""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Any, Deque, Dict, List, Optional

logger = logging.getLogger("kanyoza.reliability.memory")


class IncidentMemory:
    """Bounded in-process memory of incidents and outcomes."""

    def __init__(self, maxlen: int = 500):
        self._lock = threading.RLock()
        self._records: Deque[Dict[str, Any]] = deque(maxlen=maxlen)

    def record(
        self,
        *,
        incident_id: str,
        cause: str,
        action: str,
        outcome: str,
        confidence: float = 0.0,
        detail: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self._lock:
            self._records.append({
                "id": incident_id,
                "cause": cause,
                "action": action,
                "outcome": outcome,
                "confidence": confidence,
                "detail": detail or {},
            })

    def find_similar(self, cause: str, limit: int = 5) -> List[Dict[str, Any]]:
        with self._lock:
            matches = [r for r in self._records if r.get("cause") == cause]
        return list(reversed(matches))[:limit]

    def best_action_for(self, cause: str) -> Optional[str]:
        similar = self.find_similar(cause, limit=20)
        successes = [s for s in similar if s.get("outcome") == "success"]
        if not successes:
            return None
        # Prefer most recent successful action
        return successes[0].get("action")

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            total = len(self._records)
            success = sum(1 for r in self._records if r.get("outcome") == "success")
        return {"total": total, "successes": success, "success_rate": (success / total) if total else 0.0}
