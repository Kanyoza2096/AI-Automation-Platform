"""
Sprint 2 — Outcome feedback loop.

Closes the organism loop:
  decision → action → real outcome → Dreams / Council reliability

Call record_action_outcome(...) from:
  - publish_orchestration (immediate success/failure)
  - engagement_tracker (later quality signal)
  - human override API
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("kanyoza.core.outcome_feedback")


async def record_action_outcome(
    *,
    outcome: str,  # success | failure | partial
    decision_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    platform_post_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    score: Optional[float] = None,
    source: str = "system",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Record outcome, update decision, notify bus, nudge council/dreams.
    """
    from core.decision_gate import decision_gate

    # Validate workspace ownership BEFORE mutating the decision.
    # Otherwise a cross-workspace feedback request could mutate the decision
    # and only then raise PermissionError.
    candidate = None
    if decision_id:
        candidate = decision_gate.get(decision_id)
    if candidate is None and correlation_id:
        candidate = decision_gate.find_by_correlation(correlation_id)
    if candidate is not None and workspace_id and candidate.workspace_id not in (workspace_id, "platform"):
        raise PermissionError("Outcome workspace mismatch")

    decision = decision_gate.record_outcome(
        decision_id,
        correlation_id=correlation_id,
        outcome=outcome,
        score=score,
        source=source,
        platform_post_id=platform_post_id,
        metadata=metadata,
    )

    ws = workspace_id or (decision.workspace_id if decision else None) or "default"
    corr = correlation_id or (decision.correlation_id if decision else None)
    dec_id = decision.decision_id if decision else decision_id

    # Durable append-only outcome record. This is deliberately separate from
    # the mutable decision row so multiple later quality signals remain auditable.
    try:
        db = getattr(decision_gate, "_get_db", lambda: None)()
        if db is not None and dec_id:
            db.table("decision_outcomes").insert({
                "decision_id": dec_id,
                "workspace_id": ws,
                "correlation_id": corr,
                "outcome": outcome,
                "score": score,
                "source": source,
                "platform_post_id": platform_post_id,
                "metadata": metadata or {},
            }).execute()
    except Exception as exc:
        logger.warning("Outcome persistence failed: %s", exc)
        if getattr(decision_gate, "_is_production", lambda: False)():
            raise RuntimeError("Outcome persistence unavailable in production") from exc

    # Bus event for timeline + Dreams collectors
    try:
        from core.unified_bus import get_bus_sync
        bus = get_bus_sync()
        if bus:
            await bus.publish(
                "outcome.recorded",
                {
                    "workspace_id": ws,
                    "decision_id": dec_id,
                    "correlation_id": corr,
                    "outcome": outcome,
                    "score": score,
                    "source": source,
                    "platform_post_id": platform_post_id,
                    **(metadata or {}),
                },
                source="outcome_feedback",
                workspace_id=ws,
                correlation_id=corr or "",
                decision_id=dec_id,
            )
    except Exception as exc:
        logger.debug("outcome bus soft: %s", exc)

    # Council reliability nudge
    await _nudge_council(ws, outcome, score, decision)

    # Organism stats
    try:
        from core.organism import organism_registry
        org = organism_registry.get(ws)
        if org:
            key = "outcomes_success" if outcome == "success" else "outcomes_failure"
            if outcome == "partial":
                key = "outcomes_partial"
            org.stats[key] = org.stats.get(key, 0) + 1
    except Exception:
        pass

    return {
        "ok": decision is not None,
        "decision_id": dec_id,
        "correlation_id": corr,
        "outcome": outcome,
        "score": score,
    }


async def _nudge_council(workspace_id: str, outcome: str, score: Optional[float], decision) -> None:
    try:
        from core.organism import organism_registry
        org = organism_registry.get_or_create(workspace_id)
        council = org.council
        if not council:
            return
        # Map outcome to reliability sample
        if score is not None:
            success_rate = max(0.0, min(1.0, float(score)))
        else:
            success_rate = 1.0 if outcome == "success" else (0.5 if outcome == "partial" else 0.0)

        personas = getattr(council, "_personas", None) or getattr(council, "personas", None)
        if isinstance(personas, dict):
            for p in personas.values():
                if hasattr(p, "update_reliability"):
                    try:
                        p.update_reliability(success_rate)
                    except Exception:
                        pass

        # Store outcome in council memory if available
        mem = getattr(council, "memory", None)
        if mem and hasattr(mem, "record"):
            try:
                mem.record({
                    "decision_id": decision.decision_id if decision else None,
                    "actual_outcome": "success" if success_rate >= 0.6 else "failure",
                    "success_metric": success_rate,
                })
            except Exception:
                pass
        elif mem and hasattr(mem, "add"):
            try:
                mem.add(decision_id=getattr(decision, "decision_id", None), outcome=outcome, score=success_rate)
            except Exception:
                pass
    except Exception as exc:
        logger.debug("council nudge soft: %s", exc)


def engagement_to_outcome(engagement_score: float, *, low: float = 0.01, high: float = 0.05) -> tuple:
    """Map engagement ratio to outcome + normalized score."""
    if engagement_score >= high:
        return "success", min(1.0, engagement_score / high)
    if engagement_score >= low:
        return "partial", 0.5 + 0.5 * (engagement_score - low) / max(high - low, 1e-9)
    return "failure", max(0.0, engagement_score / max(low, 1e-9) * 0.4)
