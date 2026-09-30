"""
GuardianEngine — higher-level evaluation engine.

Provides an object-oriented interface for the Guardian evaluation,
matching the import path expected by the publish pipeline:

    from guardian.engine import GuardianEngine
    eng = GuardianEngine()
    result = eng.evaluate(action="publish", workspace_id=..., payload=...)
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .evaluate import evaluate_action


class GuardianEngine:
    """Stateful Guardian evaluation engine."""

    def __init__(self, strict: bool = True) -> None:
        self.strict = strict
        self._evaluations: list = []
        self._blocks: int = 0
        self._allows: int = 0
        self._warns: int = 0

    def evaluate(
        self,
        action: str,
        workspace_id: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Evaluate an action and record the outcome."""
        result = evaluate_action(action, workspace_id, payload)

        self._evaluations.append({
            "action": action,
            "workspace_id": workspace_id,
            "status": result.get("status"),
            "reason": result.get("reason"),
        })

        status = result.get("status", "allow")
        if status == "block":
            self._blocks += 1
        elif status == "warn":
            self._warns += 1
        else:
            self._allows += 1

        result["mode"] = "GuardianEngine"
        return result

    def get_stats(self) -> Dict[str, Any]:
        """Return engine statistics."""
        return {
            "evaluations": len(self._evaluations),
            "blocks": self._blocks,
            "allows": self._allows,
            "warns": self._warns,
        }

    def reset(self) -> None:
        """Reset statistics."""
        self._evaluations.clear()
        self._blocks = 0
        self._allows = 0
        self._warns = 0


__all__ = ["GuardianEngine"]
