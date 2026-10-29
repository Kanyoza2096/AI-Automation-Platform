"""
Phase B — Synapses (the closed loop).

Wires organs onto UnifiedEventBus so one correlation_id can travel:

  condition.changed
       → director.proposal
       → action.proposed  (Reflex evaluates)
       → council.decided  (if not blocked and risk > trivial)
       → job.* / post.published
       → pulse.* / memory
       → dream.insight

Canonical event names (prefer these for new publishers):

  heartbeat.beat
  pulse.stagnant | pulse.cleared | pulse.report
  circulation.blockage
  reflex.fired | reflex.triggered
  condition.changed
  director.proposal | director.briefing
  action.proposed
  council.decided
  job.completed | job.failed
  post.published | content.failed
  dream.insight
  memory.stored

Legacy aliases are accepted and re-emitted under canonical names.

v1.1 (2026-10-20) — propose-only-real-capabilities:
  - _on_condition no longer defaults action_name to the legacy
    "review_content_strategy" fallback. The v16.4 publish fence disabled
    that handler; proposing it anyway produced a continuous stream of
    approved-but-inexecutable decisions with outcome=null, which is
    exactly the "pretend autonomy" pattern we cannot ship.
  - _on_director_proposal now validates that the proposed action
    corresponds to a real Director capability (or a known synapse-local
    canonical action) before forwarding to action.proposed. Unknown
    actions increment events_rejected and are dropped at debug level.
  - When Director capability resolution is unavailable (e.g., boot
    ordering), we fall back to a small allowlist of synapse-local
    canonical actions rather than approving blindly.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Set

logger = logging.getLogger("kanyoza.core.synapses")


# ── Bus envelope unwrapper ────────────────────────────────────────────────
#
# UnifiedEventBus._invoke_handler passes handlers an `event_data` envelope:
#
#     {
#         "event_name": "<canonical>",
#         "payload":    <the real payload>,
#         "source":     "...",
#         "event_id":   "...",
#         "workspace_id":    "...",
#         "correlation_id":  "...",
#         "brand_id":        ...,
#         "decision_id":     ...,
#         "parent_event_id": ...,
#     }
#
# Older handlers expected the raw payload directly. This helper normalizes
# both shapes so a handler works whether it receives the envelope or the
# raw payload, without depending on the bus internals.
#
def _unwrap_event(payload: Any, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """Return the real payload from either the bus envelope or a raw dict."""
    if not isinstance(payload, dict):
        payload = kwargs.get("payload") or {}
    if not isinstance(payload, dict):
        return {}
    inner = payload.get("payload")
    if isinstance(inner, dict) and "event_name" in payload:
        return inner
    return payload


# ── Canonical event catalog ───────────────────────────────────────────────

CANONICAL_EVENTS = frozenset({
    "heartbeat.beat",
    "pulse.stagnant",
    "pulse.cleared",
    "pulse.report",
    "circulation.blockage",
    "reflex.fired",
    "reflex.triggered",
    "condition.changed",
    "director.proposal",
    "director.briefing",
    "action.proposed",
    "council.decided",
    "job.completed",
    "job.failed",
    "post.published",
    "content.failed",
    "dream.insight",
    "memory.stored",
    "species.boot",
})

# Legacy → canonical
ALIASES = {
    "service_heartbeat": "heartbeat.beat",
    "post_published": "post.published",
    "content_failed": "content.failed",
    "publish_request": "action.proposed",
    "reflex.triggered": "reflex.fired",
    "task_completed": "job.completed",
    "task_failed": "job.failed",
    "brand_health_updated": "condition.changed",
    "engagement_data": "condition.changed",
}

# Synapse-local canonical actions.
#
# These are actions this module itself emits via condition.changed events
# (e.g., _on_pulse_stagnant emits suggested_action="force_post_or_heal_publisher").
# They are NOT Director capabilities — they're handled by other handlers
# downstream. Validating against this set lets us reject actions that are
# neither a Director capability nor a known synapse-local action.
#
# Do NOT add legacy actions here (like "review_content_strategy") unless
# there is a real handler registered somewhere for them. This list is the
# synapse layer's promise to downstream consumers that the action has a
# handler somewhere in the system.
SYNAPSE_LOCAL_ACTIONS: Set[str] = {
    "force_post_or_heal_publisher",
}


class SynapseMatrix:
    """
    Registers the organism closed-loop handlers on UnifiedEventBus.

    Does not replace Director/Council/Pulse internals — it connects them.
    """

    VERSION = "1.1.0"

    def __init__(self, container=None, event_bus=None) -> None:
        self._container = container
        self._bus = event_bus
        self._wired = False
        self._unsubs = []
        self._stats = {
            "events_seen": 0,
            "proposals_routed": 0,
            "reflex_blocks": 0,
            "council_decisions": 0,
            "publishes_observed": 0,
            "events_rejected": 0,
        }

    @property
    def service_name(self) -> str:
        return "synapse_matrix"

    async def wire(self, bus=None) -> None:
        """Subscribe all synapses. Idempotent."""
        if self._wired:
            return
        bus = bus or self._bus
        if bus is None and self._container:
            from core.unified_bus import UnifiedEventBus
            bus = self._container.try_resolve(UnifiedEventBus)
        if bus is None:
            logger.error("SynapseMatrix: no UnifiedEventBus — cannot wire")
            return

        self._bus = bus

        subscriptions = [
            ("heartbeat.beat", self._on_heartbeat),
            ("service_heartbeat", self._on_heartbeat),  # legacy
            ("condition.changed", self._on_condition),
            ("brand_health_updated", self._on_condition),
            ("engagement_data", self._on_condition),
            ("director.proposal", self._on_director_proposal),
            ("action.proposed", self._on_action_proposed),
            ("publish_request", self._on_action_proposed),
            ("council.decided", self._on_council_decided),
            ("reflex.fired", self._on_reflex),
            ("reflex.triggered", self._on_reflex),
            ("post.published", self._on_post_published),
            ("post_published", self._on_post_published),
            ("job.completed", self._on_job_terminal),
            ("job.failed", self._on_job_terminal),
            ("task_completed", self._on_job_terminal),
            ("task_failed", self._on_job_terminal),
            ("content.failed", self._on_content_failed),
            ("content_failed", self._on_content_failed),
            ("circulation.blockage", self._on_blockage),
            ("dream.insight", self._on_dream_insight),
            ("pulse.stagnant", self._on_pulse_stagnant),
        ]

        for event_name, handler in subscriptions:
            try:
                unsub = await bus.subscribe(event_name, handler)
                self._unsubs.append(unsub)
            except Exception as exc:
                logger.warning("Synapse subscribe failed for %s: %s", event_name, exc)

        self._wired = True
        logger.info(
            "SynapseMatrix v%s wired — %d synapses on UnifiedEventBus",
            self.VERSION,
            len(subscriptions),
        )

        # Announce species boot on the nerve
        try:
            await bus.publish(
                "species.boot",
                {"component": "synapse_matrix", "version": self.VERSION},
                source="synapses",
                workspace_id="platform",
            )
        except Exception:
            pass

    async def unwire(self) -> None:
        for unsub in self._unsubs:
            try:
                unsub()
            except Exception:
                pass
        self._unsubs.clear()
        self._wired = False

    def get_stats(self) -> Dict[str, Any]:
        return {"wired": self._wired, **self._stats}

    # ── Helpers ───────────────────────────────────────────────────────────

    def _ws(self, payload: Dict[str, Any]) -> str:
        if not isinstance(payload, dict):
            return "default"
        return (
            payload.get("workspace_id")
            or (payload.get("_nerve") or {}).get("workspace_id")
            or "default"
        )

    def _corr(self, payload: Dict[str, Any]) -> str:
        if not isinstance(payload, dict):
            return uuid.uuid4().hex
        return (
            payload.get("correlation_id")
            or (payload.get("_nerve") or {}).get("correlation_id")
            or uuid.uuid4().hex
        )

    async def _publish(
        self,
        event_name: str,
        payload: Dict[str, Any],
        *,
        source: str,
        workspace_id: str,
        correlation_id: str,
        decision_id: Optional[str] = None,
    ) -> None:
        if not self._bus:
            return
        try:
            await self._bus.publish(
                event_name,
                payload,
                source=source,
                workspace_id=workspace_id,
                correlation_id=correlation_id,
                decision_id=decision_id,
            )
        except Exception as exc:
            logger.error("Synapse publish %s failed: %s", event_name, exc)

    async def _is_valid_action(self, action_name: str) -> bool:
        """Return True iff `action_name` corresponds to a real handler.

        The Director's capability registry is authoritative. If the Director
        cannot be resolved (e.g., during early boot before it's registered on
        the container), fall back to the synapse-local allowlist. In either
        path, unregistered actions are rejected — we never approve an action
        whose handler does not exist.

        Why this exists (v1.1): the previous code had a fallback of
        "review_content_strategy" for conditions that lacked an explicit
        suggested_action. That action's handler was disabled by the v16.4
        publish fence, but the fallback kept emitting proposals for it every
        30 minutes. Each proposal was auto-approved at low risk, stored with
        outcome=null, and never executed. That is the "pretend autonomy"
        failure mode and it must not recur.
        """
        name = (action_name or "").strip()
        if not name:
            return False

        # Try Director capability registry first.
        director = None
        if self._container:
            try:
                from services.director import get_director
                director = await get_director()
            except Exception:
                director = None
            if director is None:
                try:
                    from services.director import Director
                    director = self._container.try_resolve(Director)
                except Exception:
                    director = None

        if director is not None:
            caps = getattr(director, "capabilities", None)
            if isinstance(caps, dict):
                return name in caps

        # Fallback allowlist for synapse-local actions emitted by this module.
        return name in SYNAPSE_LOCAL_ACTIONS

    # ── Handlers ──────────────────────────────────────────────────────────

    async def _on_heartbeat(self, payload: Dict[str, Any] = None, **kwargs) -> None:
        payload = _unwrap_event(payload, kwargs)
        self._stats["events_seen"] += 1
        # Forward legacy → canonical if needed
        # Pulse / AutonomousLoop already listen; we just ensure nerve fields exist
        logger.debug(
            "Synapse heartbeat ws=%s service=%s",
            self._ws(payload),
            payload.get("service") or payload.get("service_name"),
        )

    async def _on_condition(self, payload: Dict[str, Any] = None, **kwargs) -> None:
        """Condition change → ask Director for a proposal (if available).

        v1.1: `action_name` is now the raw suggested_action from the
        condition, without a fallback. If the condition does not name an
        action, the proposal carries action_name=None, and
        _on_director_proposal rejects it before it reaches action.proposed.
        This means conditions like brand_health_updated (which don't name
        an action) no longer spawn a stream of useless proposals.
        """
        payload = _unwrap_event(payload, kwargs)
        self._stats["events_seen"] += 1
        ws = self._ws(payload)
        corr = self._corr(payload)

        director = None
        if self._container:
            try:
                from services.director import Director
                director = self._container.try_resolve(Director)
            except Exception:
                director = None
            if director is None:
                # Soft resolve by registered type name
                try:
                    for key in self._container.registered_types():
                        name = getattr(key, "__name__", "")
                        if name in ("Director", "DirectorService"):
                            try:
                                director = self._container.resolve(key)
                                break
                            except Exception:
                                pass
                except Exception:
                    pass

        # Skeleton proposal — action_name comes from the condition only.
        # No fallback. Downstream validation drops it if the action is
        # unknown or missing.
        proposal = {
            "proposal_id": f"prop_{uuid.uuid4().hex[:12]}",
            "workspace_id": ws,
            "correlation_id": corr,
            "trigger": "condition.changed",
            "condition": payload,
            "action_name": payload.get("suggested_action"),  # may be None
            "risk_level": payload.get("risk_level") or "low",
            "authority": payload.get("authority") or "suggest",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if director and hasattr(director, "on_condition_changed"):
            try:
                result = director.on_condition_changed(payload)
                if hasattr(result, "__await__"):
                    result = await result
                if isinstance(result, dict):
                    proposal.update(result)
            except Exception as exc:
                logger.debug("Director on_condition_changed: %s", exc)

        self._stats["proposals_routed"] += 1
        await self._publish(
            "director.proposal",
            proposal,
            source="synapses",
            workspace_id=ws,
            correlation_id=corr,
        )

    async def _on_director_proposal(self, payload: Dict[str, Any] = None, **kwargs) -> None:
        """Director proposal → action.proposed for Reflex + Council path.

        v1.1: validates that `action_name` corresponds to a real handler
        before forwarding. Unknown or missing actions are dropped (counted
        in events_rejected) rather than approved and stored with no
        executable handler.
        """
        payload = _unwrap_event(payload, kwargs)
        self._stats["events_seen"] += 1
        ws = self._ws(payload)
        corr = self._corr(payload)

        action_name = str(payload.get("action_name") or "").strip()
        if not action_name or action_name.lower() == "unknown":
            logger.debug(
                "Skipping director.proposal with no concrete action ws=%s correlation=%s",
                ws, corr,
            )
            self._stats["events_rejected"] += 1
            return

        if not await self._is_valid_action(action_name):
            logger.debug(
                "Skipping director.proposal for unregistered action=%s ws=%s correlation=%s",
                action_name, ws, corr,
            )
            self._stats["events_rejected"] += 1
            return

        action = {
            "action_name": action_name,
            "action_params": payload.get("action_params") or payload,
            "workspace_id": ws,
            "correlation_id": corr,
            "risk_level": payload.get("risk_level") or "medium",
            "proposal_id": payload.get("proposal_id"),
            "source_engine": "director",
        }
        await self._publish(
            "action.proposed",
            action,
            source="synapses",
            workspace_id=ws,
            correlation_id=corr,
        )

    async def _on_action_proposed(self, payload: Dict[str, Any] = None, **kwargs) -> None:
        """
        action.proposed → Reflex first; if blocked stop; else Council or auto-approve.
        """
        payload = _unwrap_event(payload, kwargs)
        self._stats["events_seen"] += 1
        ws = self._ws(payload)
        corr = self._corr(payload)
        action_name = str(payload.get("action_name") or "").strip()
        action_params = payload.get("action_params") or {}
        if not action_name or action_name.lower() == "unknown":
            logger.error("Rejecting action.proposed without a concrete action workspace=%s", ws)
            self._stats["events_rejected"] = self._stats.get("events_rejected", 0) + 1
            return
        risk = (payload.get("risk_level") or "medium").lower()

        from core.decision_gate import decision_gate, DecisionSource

        # ── Reflex ────────────────────────────────────────────────────
        reflex = None
        if self._container:
            try:
                from core.reflexes import ReflexSystem
                reflex = self._container.try_resolve(ReflexSystem)
            except Exception:
                pass
        if reflex is None:
            try:
                from core.reflexes import ReflexSystem
                reflex = ReflexSystem(event_bus=self._bus, container=self._container)
            except Exception:
                reflex = None

        blocked = False
        block_reason = None
        if reflex and hasattr(reflex, "evaluate_action"):
            try:
                result = await reflex.evaluate_action(action_name, action_params)
                blocked = bool(result.get("blocked"))
                block_reason = result.get("reason")
            except Exception as exc:
                logger.debug("Reflex evaluate failed: %s", exc)

        if blocked:
            self._stats["reflex_blocks"] += 1
            decision = decision_gate.issue(
                source=DecisionSource.REFLEX_ALLOW,
                workspace_id=ws,
                action_name=action_name,
                approved=False,
                correlation_id=corr,
                risk_level=risk,
                reason=block_reason or "reflex_block",
            )
            await self._publish(
                "council.decided",
                {
                    **decision.to_dict(),
                    "action": action_name,
                    "blocked_by": "reflex",
                    "workspace_id": ws,
                    "correlation_id": corr,
                },
                source="synapses",
                workspace_id=ws,
                correlation_id=corr,
                decision_id=decision.decision_id,
            )
            return

        # ── Trivial / low risk → Director auto ────────────────────────
        if risk in ("trivial", "low", "none"):
            decision = decision_gate.issue(
                source=DecisionSource.DIRECTOR_AUTO,
                workspace_id=ws,
                action_name=action_name,
                approved=True,
                correlation_id=corr,
                risk_level=risk,
                reason="auto_approve_low_risk",
            )
            self._stats["council_decisions"] += 1
            await self._publish(
                "council.decided",
                {
                    **decision.to_dict(),
                    "action": action_name,
                    "workspace_id": ws,
                    "correlation_id": corr,
                    "tier": 0,
                },
                source="synapses",
                workspace_id=ws,
                correlation_id=corr,
                decision_id=decision.decision_id,
            )
            return

        # Phase C — plan metabolism may auto-approve medium for business
        try:
            from core.organism import organism_registry
            org = organism_registry.get_or_create(ws)
            if org.director_may_auto(risk):
                decision = decision_gate.issue(
                    source=DecisionSource.DIRECTOR_AUTO,
                    workspace_id=ws,
                    action_name=action_name,
                    approved=True,
                    correlation_id=corr,
                    risk_level=risk,
                    reason=f"auto_approve_plan_{org.config.plan}",
                )
                self._stats["council_decisions"] += 1
                await self._publish(
                    "council.decided",
                    {
                        **decision.to_dict(),
                        "action": action_name,
                        "workspace_id": ws,
                        "correlation_id": corr,
                        "tier": 1,
                    },
                    source="synapses",
                    workspace_id=ws,
                    correlation_id=corr,
                    decision_id=decision.decision_id,
                )
                return
        except Exception:
            pass

        # ── Medium+ → Council ─────────────────────────────────────────
        council_result = await self._deliberate_council(payload, ws, corr, action_name, risk)
        self._stats["council_decisions"] += 1
        await self._publish(
            "council.decided",
            council_result,
            source="synapses",
            workspace_id=ws,
            correlation_id=corr,
            decision_id=council_result.get("decision_id"),
        )

    async def _deliberate_council(
        self,
        payload: Dict[str, Any],
        ws: str,
        corr: str,
        action_name: str,
        risk: str,
    ) -> Dict[str, Any]:
        from core.decision_gate import decision_gate, DecisionSource

        approved = False
        reason = "council_deliberation_required"
        votes = []
        confidence = 0.0

        try:
            from council.engine import DemocracyEngine
            from council.models import Proposal, RiskLevel

            engine = None
            # Phase C — prefer this workspace's Council
            try:
                from core.organism import organism_registry
                org = organism_registry.get(ws)
                if org and org.council is not None:
                    engine = org.council
                    org.touch()
                    org.stats["decisions"] = org.stats.get("decisions", 0) + 1
            except Exception:
                pass
            if engine is None and self._container:
                try:
                    engine = self._container.try_resolve(DemocracyEngine)
                except Exception:
                    pass
            if engine is None:
                engine = DemocracyEngine()

            risk_map = {
                "trivial": RiskLevel.TRIVIAL,
                "low": RiskLevel.LOW,
                "medium": RiskLevel.MEDIUM,
                "high": RiskLevel.HIGH,
                "critical": RiskLevel.CRITICAL,
            }
            proposal = Proposal(
                id=payload.get("proposal_id") or f"prop_{uuid.uuid4().hex[:10]}",
                action_name=action_name,
                source_engine="synapses",
                risk_level=risk_map.get(risk, RiskLevel.MEDIUM),
                workspace_id=ws,
                payload=payload,
            ) if _proposal_supports_workspace() else Proposal(
                id=payload.get("proposal_id") or f"prop_{uuid.uuid4().hex[:10]}",
                action_name=action_name,
                source_engine="synapses",
                risk_level=risk_map.get(risk, RiskLevel.MEDIUM),
            )

            result = await engine.deliberate(proposal)
            decision_state = getattr(result, "decision", None)
            approved = str(getattr(decision_state, "value", decision_state)).lower() in (
                "approved",
                "approved_with_warnings",
                "DecisionState.APPROVED",
            ) or "APPROVED" in str(decision_state).upper()
            confidence = float(getattr(result, "confidence", 0.7) or 0.7)
            reason = "council_deliberation"
            votes = [
                {
                    "persona": getattr(v, "persona_name", getattr(v, "name", "unknown")),
                    "vote": getattr(v, "vote", None),
                    "confidence": getattr(v, "confidence", None),
                }
                for v in (getattr(result, "votes", None) or [])
            ]
        except Exception as exc:
            logger.warning("Council deliberation fallback: %s", exc)
            # Governance infrastructure failure is never an approval signal.
            # Low/trivial actions have already been handled by the explicit
            # low-risk branch above; every action reaching Council fails closed.
            approved = False
            confidence = 0.0
            reason = f"council_unavailable_fail_closed:{type(exc).__name__}"

        decision = decision_gate.issue(
            source=DecisionSource.COUNCIL,
            workspace_id=ws,
            action_name=action_name,
            approved=approved,
            correlation_id=corr,
            risk_level=risk,
            reason=reason,
            votes=votes,
            metadata={"confidence": confidence},
        )
        return {
            **decision.to_dict(),
            "action": action_name,
            "workspace_id": ws,
            "correlation_id": corr,
            "confidence": confidence,
        }

    async def _on_council_decided(self, payload: Dict[str, Any] = None, **kwargs) -> None:
        payload = _unwrap_event(payload, kwargs)
        self._stats["events_seen"] += 1
        approved = payload.get("approved")
        if not isinstance(approved, bool):
            # Approval is an authoritative control-plane field. Do not silently
            # propagate None/strings as if they were a real decision. Preserve
            # the original record for diagnosis while marking the propagation
            # invalid and fail closed.
            logger.error(
                "Invalid council.decided approval state id=%s approved=%r ws=%s",
                payload.get("decision_id"), approved, self._ws(payload),
            )
            payload = {**payload, "approved": False, "approval_state": "invalid_missing_or_non_boolean"}

        logger.info(
            "Synapse council.decided id=%s approved=%s ws=%s",
            payload.get("decision_id"),
            payload.get("approved"),
            self._ws(payload),
        )
        # Memory store hook
        await self._publish(
            "memory.stored",
            {
                "kind": "decision",
                "record": payload,
                "workspace_id": self._ws(payload),
                "correlation_id": self._corr(payload),
            },
            source="synapses",
            workspace_id=self._ws(payload),
            correlation_id=self._corr(payload),
            decision_id=payload.get("decision_id"),
        )

    async def _on_reflex(self, payload: Dict[str, Any] = None, **kwargs) -> None:
        payload = _unwrap_event(payload, kwargs)
        self._stats["events_seen"] += 1
        self._stats["reflex_blocks"] += 1
        logger.warning(
            "Synapse reflex.fired type=%s action=%s",
            payload.get("type"),
            payload.get("action_name"),
        )

    async def _on_post_published(self, payload: Dict[str, Any] = None, **kwargs) -> None:
        payload = _unwrap_event(payload, kwargs)
        self._stats["events_seen"] += 1
        self._stats["publishes_observed"] += 1
        ws = self._ws(payload)
        corr = self._corr(payload)
        # Pulse clear signal
        await self._publish(
            "pulse.cleared",
            {
                "organ": "publisher",
                "workspace_id": ws,
                "correlation_id": corr,
                "post_id": payload.get("post_id") or payload.get("platform_post_id"),
            },
            source="synapses",
            workspace_id=ws,
            correlation_id=corr,
            decision_id=payload.get("decision_id"),
        )

    async def _on_job_terminal(self, payload: Dict[str, Any] = None, **kwargs) -> None:
        payload = _unwrap_event(payload, kwargs)
        self._stats["events_seen"] += 1

    async def _on_content_failed(self, payload: Dict[str, Any] = None, **kwargs) -> None:
        payload = _unwrap_event(payload, kwargs)
        self._stats["events_seen"] += 1
        ws = self._ws(payload)
        corr = self._corr(payload)
        await self._publish(
            "pulse.stagnant",
            {
                "organ": "content_pipeline",
                "workspace_id": ws,
                "correlation_id": corr,
                "reason": payload.get("error") or payload.get("reason") or "content_failed",
            },
            source="synapses",
            workspace_id=ws,
            correlation_id=corr,
        )

    async def _on_blockage(self, payload: Dict[str, Any] = None, **kwargs) -> None:
        payload = _unwrap_event(payload, kwargs)
        self._stats["events_seen"] += 1
        # Severe blockages already handled by Reflex elsewhere; log for matrix visibility
        logger.warning(
            "Synapse circulation.blockage location=%s severity=%s",
            payload.get("location"),
            payload.get("severity"),
        )

    async def _on_dream_insight(self, payload: Dict[str, Any] = None, **kwargs) -> None:
        payload = _unwrap_event(payload, kwargs)
        self._stats["events_seen"] += 1
        # Future: feed Director morning briefing
        logger.info(
            "Synapse dream.insight ws=%s keys=%s",
            self._ws(payload),
            list(payload.keys())[:8],
        )

    async def _on_pulse_stagnant(self, payload: Dict[str, Any] = None, **kwargs) -> None:
        payload = _unwrap_event(payload, kwargs)
        self._stats["events_seen"] += 1
        # Stagnation becomes a condition so Director can propose recovery
        await self._publish(
            "condition.changed",
            {
                "kind": "stagnation",
                "workspace_id": self._ws(payload),
                "correlation_id": self._corr(payload),
                "organ": payload.get("organ"),
                "reason": payload.get("reason"),
                "suggested_action": "force_post_or_heal_publisher",
                "risk_level": "medium",
            },
            source="synapses",
            workspace_id=self._ws(payload),
            correlation_id=self._corr(payload),
        )


def _proposal_supports_workspace() -> bool:
    try:
        from council.models import Proposal
        import inspect
        sig = inspect.signature(Proposal)
        return "workspace_id" in sig.parameters
    except Exception:
        return False


# Module singleton for lifecycle
synapse_matrix = SynapseMatrix()


async def wire_synapses(container, event_bus=None) -> SynapseMatrix:
    """Lifecycle entrypoint — call after UnifiedEventBus is ready."""
    matrix = SynapseMatrix(container=container, event_bus=event_bus)
    await matrix.wire(event_bus)
    global synapse_matrix
    synapse_matrix = matrix
    return matrix
