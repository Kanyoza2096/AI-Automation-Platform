"""
Council Integration Layer
=========================

Wires the Democracy Engine into all autonomous platform engines.
Every action passes through the Council before execution.

v1.1 — COMPAT ADAPTER:
  Adds a legacy-compatible adapter around CouncilOrchestrator so that
  routes/council_routes.py (which expects .members, .vote_on,
  .get_statistics, .get_history, .get_summary) works unchanged.
"""

from __future__ import annotations

import asyncio
import logging
from functools import wraps
from typing import Any, Callable, Dict, List, Optional

from council import CouncilOrchestrator
from council.models import Proposal, DecisionState, RiskLevel, PersonaVote

logger = logging.getLogger("kanyoza.council.integration")


# ═══════════════════════════════════════════════════════════════════════════
# LEGACY COMPAT ADAPTER
# ═══════════════════════════════════════════════════════════════════════════

class _CouncilAdapter:
    """
    Adapter that gives CouncilOrchestrator the shape the legacy routes expect.

    Legacy routes call:
      - .members                            → list of persona instances
      - .vote_on(brand_context, proposal)   → dict
      - .get_statistics()                   → dict
      - .get_history(limit=...)             → list
      - .get_summary(vote_result)           → str

    CouncilOrchestrator natively exposes:
      - .deliberate(proposal)               → DeliberationResult (async)
      - .get_status()                       → dict
      - .record_outcome(...)
      - .is_available / .set_available(...)
    """

    def __init__(self, orchestrator: CouncilOrchestrator):
        self._orch = orchestrator

        # Instantiate all 12 personas (with Theologian + AffiliateMarketer)
        from council.personas import ALL_PERSONAS
        self.members: List[Any] = [cls() for cls in ALL_PERSONAS]

        logger.info(
            "Council adapter initialized — %d members: %s",
            len(self.members),
            [getattr(m, "name", "unknown") for m in self.members],
        )

    # ── Public compat surface ────────────────────────────────────────────

    def vote_on(self, brand_context: dict, proposal: dict) -> dict:
        """
        Sync-compatible entry used by routes/council_routes.py.

        Translates the legacy (brand_context, proposal_dict) call into a
        Proposal, runs the orchestrator, then reshapes DeliberationResult
        back into the legacy dict.
        """
        p = self._build_proposal(brand_context or {}, proposal or {})
        result = self._run_deliberate(p)

        decision_enum = getattr(result, "decision", None)
        decision_str = (
            decision_enum.value.upper()
            if hasattr(decision_enum, "value")
            else str(decision_enum or "NEEDS_REVISION").upper()
        )

        votes = [
            {
                "member": getattr(v, "member", ""),
                "role": getattr(v, "role", ""),
                "vote": getattr(v, "vote", "abstain"),
                "confidence": float(getattr(v, "confidence", 0.0) or 0.0),
                "reason": getattr(v, "reason", ""),
                "weight": float(getattr(v, "weight", 1.0) or 1.0),
                "veto_type": (
                    getattr(v.veto_type, "value", "none")
                    if hasattr(v, "veto_type")
                    else "none"
                ),
            }
            for v in (getattr(result, "votes", []) or [])
        ]

        is_blocked = decision_str in ("BLOCKED", "REJECTED")
        veto_member = None
        if is_blocked:
            for v in votes:
                if v.get("vote") == "reject":
                    veto_member = v.get("member")
                    break

        return {
            "decision": decision_str,
            "reason": (
                "; ".join(getattr(result, "warnings", []) or [])
                or f"Council {decision_str}"
            ),
            "approve_ratio": float(getattr(result, "approve_ratio", 0.0) or 0.0),
            "reject_ratio": float(getattr(result, "reject_ratio", 0.0) or 0.0),
            "quorum_ratio": float(getattr(result, "quorum_ratio", 0.0) or 0.0),
            "confidence": float(getattr(result, "confidence", 0.0) or 0.0),
            "votes": votes,
            "veto": is_blocked,
            "veto_member": veto_member,
            "proposal_id": getattr(result, "proposal_id", p.id),
            "risk_level": (
                getattr(result.risk_level, "value", "medium")
                if hasattr(result, "risk_level")
                else "medium"
            ),
            "tier_used": int(getattr(result, "tier_used", 0) or 0),
            "deliberation_time_ms": float(
                getattr(result, "deliberation_time_ms", 0.0) or 0.0
            ),
            "fallback_used": bool(getattr(result, "fallback_used", False)),
        }

    def get_statistics(self) -> dict:
        """Return aggregate statistics for the legacy routes."""
        total = 0
        try:
            if hasattr(self._orch, "memory"):
                total = int(getattr(self._orch.memory, "size", 0) or 0)
        except Exception:
            pass

        return {
            "total_votes": total,
            "decisions": {},
            "approval_rate": 0.0,
            "rejection_rate": 0.0,
            "revision_rate": 0.0,
            "veto_rate": 0.0,
        }

    def get_history(self, limit: int = 20) -> list:
        """No persistent history yet — return empty list."""
        return []

    def get_summary(self, vote_result: dict) -> str:
        """Human-readable summary for the legacy routes."""
        decision = vote_result.get("decision", "UNKNOWN")
        approve = vote_result.get("approve_ratio", 0.0)
        reject = vote_result.get("reject_ratio", 0.0)
        quorum = vote_result.get("quorum_ratio", 0.0)
        return (
            f"Council Decision: {decision}\n"
            f"  Approve: {approve:.0%} | Reject: {reject:.0%} | Quorum: {quorum:.0%}"
        )

    # ── Delegation to the underlying orchestrator ────────────────────────

    def __getattr__(self, name: str) -> Any:
        """
        Delegate any attribute we don't override directly to the orchestrator.

        This preserves .deliberate, .get_status, .record_outcome,
        .is_available, .set_available, .router, .reliability, etc.
        """
        return getattr(self._orch, name)

    # ── Helpers ──────────────────────────────────────────────────────────

    def _build_proposal(self, brand_context: dict, proposal: dict) -> Proposal:
        risk_map = {
            "low": RiskLevel.LOW,
            "medium": RiskLevel.MEDIUM,
            "high": RiskLevel.HIGH,
            "critical": RiskLevel.CRITICAL,
            "trivial": RiskLevel.TRIVIAL,
        }
        raw_risk = str(proposal.get("risk", proposal.get("risk_level", "medium"))).lower()
        risk_level = risk_map.get(raw_risk, RiskLevel.MEDIUM)

        action_name = (
            proposal.get("action_name")
            or proposal.get("topic")
            or proposal.get("content")
            or "content_decision"
        )

        description = (
            proposal.get("content")
            or proposal.get("topic")
            or proposal.get("description")
            or ""
        )

        # Preserve brand/workspace so the orchestrator's cache key is stable
        metadata = {
            "brand_id": brand_context.get("brand_id"),
            "workspace_id": brand_context.get("workspace_id"),
            "industry": brand_context.get("industry"),
            "has_cta": proposal.get("has_cta", False),
            "topic_source": proposal.get("topic_source"),
            "hour": proposal.get("hour"),
            "day": proposal.get("day"),
        }

        return Proposal(
            action_name=str(action_name)[:200],
            source_engine=str(
                proposal.get("source_engine")
                or brand_context.get("source_engine")
                or "legacy_routes"
            ),
            description=str(description)[:500],
            proposal_type=str(proposal.get("proposal_type", "content")),
            action_params={
                "brand_context": brand_context,
                "system_health": proposal.get("system_health"),
                "engagement_ratio": proposal.get("engagement_ratio"),
                "sample_count": proposal.get("sample_count"),
                "engagement_trend": proposal.get("engagement_trend"),
            },
            metadata={k: v for k, v in metadata.items() if v is not None},
            risk_level=risk_level,
        )

    def _run_deliberate(self, proposal: Proposal):
        """
        Run the orchestrator's async deliberate from a sync context.

        - If we're inside a running event loop (rare for the sync call path),
          we schedule the coroutine on a private loop and wait.
        - Otherwise we use asyncio.run.
        """
        coro = self._orch.deliberate(proposal)
        try:
            asyncio.get_running_loop()
            # Inside a running loop: use a fresh loop in a thread to avoid
            # "asyncio.run() cannot be called from a running event loop".
            import concurrent.futures

            def _in_thread() -> Any:
                new_loop = asyncio.new_event_loop()
                try:
                    return new_loop.run_until_complete(
                        self._orch.deliberate(proposal)
                    )
                finally:
                    new_loop.close()

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(_in_thread).result()
        except RuntimeError:
            # No running loop — safe to call asyncio.run
            return asyncio.run(coro)


# ═══════════════════════════════════════════════════════════════════════════
# GLOBAL COUNCIL (ADAPTER-WRAPPED)
# ═══════════════════════════════════════════════════════════════════════════

council = _CouncilAdapter(CouncilOrchestrator())


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════

class CouncilDecision:
    """Result of a council deliberation."""

    def __init__(self, result):
        self.result = result
        self.decision = getattr(result, "decision", None)
        self.approve_ratio = float(getattr(result, "approve_ratio", 0.0) or 0.0)
        self.confidence = float(getattr(result, "confidence", 0.0) or 0.0)
        self.warnings = list(getattr(result, "warnings", None) or [])
        raw_dissent = getattr(result, "dissent", None)
        self.dissent = list(raw_dissent) if raw_dissent else []
        self.proposal_id = getattr(result, "proposal_id", None)
        self.decision_id = getattr(result, "decision_id", None)
        self.reason = self._get_reason()

    def _get_reason(self) -> str:
        if self.dissent:
            first = self.dissent[0]
            if isinstance(first, dict):
                return str(first.get("reason", "No dissent"))
            return str(getattr(first, "reason", first) or "No dissent")
        if self.warnings:
            return str(self.warnings[0])
        return "Approved by Council"

    @property
    def is_approved(self) -> bool:
        return self.decision in (
            DecisionState.APPROVED,
            DecisionState.APPROVED_WITH_WARNINGS,
        )

    @property
    def is_blocked(self) -> bool:
        return self.decision in (
            DecisionState.BLOCKED,
            DecisionState.REJECTED,
        )

    @property
    def needs_escalation(self) -> bool:
        return self.decision in (
            DecisionState.ESCALATED,
            DecisionState.NEEDS_REVISION,
        )

    def to_dict(self) -> dict:
        return {
            "decision": self.decision.value if self.decision else "unknown",
            "approve_ratio": self.approve_ratio,
            "confidence": self.confidence,
            "reason": self.reason,
            "warnings": self.warnings,
            "dissent": self.dissent,
        }


def require_council(
    action_name: str,
    proposal_type: str = "general",
    risk_level: Optional[RiskLevel] = None,
):
    """
    Decorator that requires Council approval before executing a function.
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            proposal = Proposal(
                action_name=action_name,
                action_params=kwargs,
                source_engine=func.__module__,
                description=kwargs.get("description", f"Execute {action_name}"),
                proposal_type=proposal_type,
                metadata=kwargs.get("metadata", {}),
            )

            if risk_level:
                proposal.risk_level = risk_level

            result = await council.deliberate(proposal)
            decision = CouncilDecision(result)

            if decision.is_blocked:
                logger.warning(
                    "Council BLOCKED %s: %s", action_name, decision.reason,
                )
                return {
                    "ok": False,
                    "blocked": True,
                    "council_decision": decision.to_dict(),
                }

            if decision.needs_escalation:
                logger.warning(
                    "Council ESCALATED %s: %s", action_name, decision.reason,
                )
                return {
                    "ok": False,
                    "escalated": True,
                    "council_decision": decision.to_dict(),
                }

            logger.info(
                "Council APPROVED %s (%.0f%% confidence)",
                action_name,
                decision.confidence * 100,
            )

            try:
                result = await func(*args, **kwargs)

                if isinstance(result, dict) and result.get("ok") is not None:
                    outcome = "success" if result.get("ok") else "failure"
                    council.record_outcome(
                        proposal_id=proposal.id,
                        decision=decision.decision,
                        actual_outcome=outcome,
                        success_metric=1.0 if result.get("ok") else 0.0,
                    )

                if isinstance(result, dict):
                    result["council_decision"] = decision.to_dict()

                return result
            except Exception:
                council.record_outcome(
                    proposal_id=proposal.id,
                    decision=decision.decision,
                    actual_outcome="failure",
                    success_metric=0.0,
                )
                raise

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            proposal = Proposal(
                action_name=action_name,
                action_params=kwargs,
                source_engine=func.__module__,
                description=kwargs.get("description", f"Execute {action_name}"),
                proposal_type=proposal_type,
                metadata=kwargs.get("metadata", {}),
            )

            if risk_level:
                proposal.risk_level = risk_level

            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(council.deliberate(proposal))
            finally:
                loop.close()

            decision = CouncilDecision(result)

            if decision.is_blocked:
                return {"ok": False, "blocked": True, "council_decision": decision.to_dict()}

            if decision.needs_escalation:
                return {"ok": False, "escalated": True, "council_decision": decision.to_dict()}

            result = func(*args, **kwargs)

            if isinstance(result, dict):
                result["council_decision"] = decision.to_dict()

            return result

        import inspect
        if inspect.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


async def deliberate_action(
    action_name: str,
    source_engine: str,
    description: str,
    proposal_type: str = "general",
    metadata: Optional[Dict[str, Any]] = None,
    action_params: Optional[Dict[str, Any]] = None,
) -> CouncilDecision:
    """Deliberate on an action without executing it."""
    proposal = Proposal(
        action_name=action_name,
        action_params=action_params or {},
        source_engine=source_engine,
        description=description,
        proposal_type=proposal_type,
        metadata=metadata or {},
    )

    result = await council.deliberate(proposal)
    return CouncilDecision(result)


def get_council() -> _CouncilAdapter:
    """Get the global Council instance (adapter-wrapped)."""
    return council


def get_council_status() -> dict:
    """Get Council status for health checks."""
    return council.get_status()


__all__ = [
    "council",
    "CouncilDecision",
    "require_council",
    "deliberate_action",
    "get_council",
    "get_council_status",
]
