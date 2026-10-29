"""
Canonical request / action pipeline — implements the final architecture flow:

  HTTP → Auth → RequestContext → Authorization → Guardian → Organism
    → Memory / Metabolism / Council → Director → Decision
    → Domain Service → Job/Outbox → Worker → Plugin → Platform
    → Result → Event / Analytics / Memory → Observability

This module is the single orchestration entry for autonomous / sensitive actions.
Route handlers should call `run_action_pipeline` rather than inventing parallel paths.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, Optional

from core.request_context import RequestContext, require_request_context
from core.event_envelope import EventEnvelope

logger = logging.getLogger("kanyoza.core.pipeline")


class Decision(str, Enum):
    BLOCK = "BLOCK"
    ALLOW = "ALLOW"
    SUGGEST = "SUGGEST"
    AUTO_LOW = "AUTO_LOW"
    AUTO_MEDIUM = "AUTO_MEDIUM"
    AUTO_HIGH = "AUTO_HIGH"
    ESCALATE = "ESCALATE"


@dataclass
class PipelineResult:
    decision: Decision
    allowed: bool
    reason: str = ""
    decision_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    correlation_id: str = ""
    workspace_id: str = ""
    organism_id: Optional[str] = None
    guardian: Dict[str, Any] = field(default_factory=dict)
    metabolism: Dict[str, Any] = field(default_factory=dict)
    council: Dict[str, Any] = field(default_factory=dict)
    director: Dict[str, Any] = field(default_factory=dict)
    job_id: Optional[str] = None
    outbox_id: Optional[str] = None
    events: list = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision.value,
            "allowed": self.allowed,
            "reason": self.reason,
            "decision_id": self.decision_id,
            "correlation_id": self.correlation_id,
            "workspace_id": self.workspace_id,
            "organism_id": self.organism_id,
            "guardian": self.guardian,
            "metabolism": self.metabolism,
            "council": self.council,
            "director": self.director,
            "job_id": self.job_id,
            "outbox_id": self.outbox_id,
            "events": self.events,
            "metadata": self.metadata,
        }


def _authorize(ctx: RequestContext, permission: str) -> Optional[str]:
    if not ctx.has_permission(permission) and permission not in ("read",):
        if ctx.species_privileges:
            return None
        if permission == "write" and ctx.has_permission("write"):
            return None
        if not ctx.has_permission(permission) and not ctx.species_privileges:
            # allow if any write/admin present for generic write actions
            if permission == "write" and any(
                p in ctx.permissions for p in ("write", "admin", "owner")
            ):
                return None
            if ctx.role in ("operator", "admin", "owner", "species"):
                return None
            return f"Missing permission: {permission}"
    return None


def _guardian_check(ctx: RequestContext, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Real Guardian integration — policy/threat signals when available."""
    try:
        # Lightweight policy: block empty destructive actions
        if action in ("publish", "delete", "scram", "token_create") and payload is None:
            return {"verdict": "WARN", "reason": "empty_payload"}

        # Optional: last scanner findings (code guardian)
        try:
            from guardian.scanner import CodeGuardianScanner

            scanner = CodeGuardianScanner()
            last = scanner.get_last_results() if hasattr(scanner, "get_last_results") else []
            critical = 0
            for r in last or []:
                critical += int(getattr(r, "critical_count", 0) or 0)
            if critical > 0 and action in ("publish", "plugin_execute", "workflow_run"):
                return {
                    "verdict": "BLOCK",
                    "reason": f"guardian_critical_findings:{critical}",
                    "critical": critical,
                }
        except Exception as exc:
            logger.debug("Guardian scanner soft: %s", exc)

        # Decision-gate awareness for publish
        if action == "publish":
            try:
                from core.decision_gate import DecisionGateError
                # Soft signal only — full gate may run in publish service
            except Exception:
                pass

        return {"verdict": "ALLOW", "reason": "guardian_clear"}
    except Exception as exc:
        logger.warning("Guardian check error: %s", exc)
        try:
            from core.fail_closed import resolve_runtime_mode
            from config.settings import settings
            mode = resolve_runtime_mode(getattr(settings, "environment", None) or "development")
            if not mode.allow_memory_fallback and action in ("publish", "plugin_execute"):
                return {"verdict": "BLOCK", "reason": f"guardian_unavailable_fail_closed:{exc}"}
        except Exception:
            pass
        return {"verdict": "ALLOW", "reason": f"guardian_error:{exc}"}


def _metabolism_check(ctx: RequestContext, action: str, cost: Dict[str, Any]) -> Dict[str, Any]:
    """Real Metabolism — assert budget before expensive work."""
    try:
        from core.metabolism import metabolism, MetabolismError

        ws = ctx.workspace_id
        if action in ("publish", "social_post", "bot_post", "force_post"):
            try:
                metabolism.assert_can_publish(ws)
                return {"allowed": True, "reason": "metabolism_publish_ok", "status": metabolism.status(ws)}
            except MetabolismError as me:
                return {"allowed": False, "reason": me.message, "code": me.code, "usage": me.usage}
        if action in ("ai_generate", "ai_chat", "image_generate"):
            tokens = int((cost or {}).get("tokens") or (cost or {}).get("estimated_tokens") or 0)
            try:
                metabolism.assert_can_think(ws, estimated_tokens=tokens)
                return {"allowed": True, "reason": "metabolism_ai_ok"}
            except MetabolismError as me:
                return {"allowed": False, "reason": me.message, "code": me.code, "usage": me.usage}
        if action in ("brand_create",):
            try:
                metabolism.assert_can_add_brand(ws)
                return {"allowed": True, "reason": "metabolism_brand_ok"}
            except MetabolismError as me:
                return {"allowed": False, "reason": me.message, "code": me.code}
        return {"allowed": True, "reason": "metabolism_not_applicable"}
    except Exception as exc:
        logger.warning("Metabolism check error: %s", exc)
        try:
            from core.fail_closed import resolve_runtime_mode
            from config.settings import settings
            mode = resolve_runtime_mode(getattr(settings, "environment", None) or "development")
            if not mode.allow_memory_fallback and action in ("publish", "ai_generate", "plugin_execute"):
                return {"allowed": False, "reason": f"metabolism_unavailable_fail_closed:{exc}"}
        except Exception:
            pass
        return {"allowed": True, "reason": f"metabolism_error_soft:{exc}"}


def _council_advise(ctx: RequestContext, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Canonical Council package — advisory for elevated risk actions."""
    try:
        from council import advise  # type: ignore

        advice = advise(workspace_id=ctx.workspace_id, action=action, payload=payload)
        if isinstance(advice, dict):
            return advice
    except Exception:
        pass

    # High-risk actions: consult orchestrator status / quick path
    elevated = action in ("publish", "scram", "token_create", "feature_toggle", "plugin_execute")
    if elevated:
        try:
            from council.orchestration.council_orchestrator import CouncilOrchestrator
            from council.models import Proposal

            orch = CouncilOrchestrator()
            if hasattr(orch, "get_status"):
                st = orch.get_status()
                if isinstance(st, dict) and st.get("paused"):
                    return {"advice": "BLOCK", "reason": "council_paused", "status": st}
            # Human-driven API operator actions still proceed; autonomous would deliberate
            if ctx.role in ("viewer",) or not ctx.has_permission("write"):
                return {"advice": "BLOCK", "reason": "council_insufficient_authority"}
            return {"advice": "PROCEED", "reason": "council_operator_path", "orchestrator": True}
        except Exception as exc:
            logger.debug("Council orchestrator: %s", exc)

    return {"advice": "PROCEED", "reason": "council_default"}


def _director_decide(
    ctx: RequestContext,
    action: str,
    guardian: Dict[str, Any],
    metabolism: Dict[str, Any],
    council: Dict[str, Any],
) -> Decision:
    if guardian.get("verdict") in ("BLOCK", "QUARANTINE"):
        return Decision.BLOCK
    if metabolism.get("allowed") is False:
        return Decision.BLOCK
    advice = str(council.get("advice", "PROCEED")).upper()
    if advice in ("BLOCK", "DENY", "REJECT"):
        return Decision.BLOCK
    if advice in ("ESCALATE", "HUMAN"):
        return Decision.ESCALATE
    if advice in ("SUGGEST",):
        return Decision.SUGGEST
    # Explicit operator/admin actions (human-driven via API) → ALLOW
    if ctx.species_privileges or ctx.role in ("admin", "owner", "operator"):
        return Decision.ALLOW
    # Autonomous / plan-bound path uses plan matrix
    try:
        from core.plan_authority import director_decision_for_plan
        plan = None
        if getattr(ctx, "metadata", None):
            plan = ctx.metadata.get("plan")
        decided = director_decision_for_plan(plan, species=False)
        for d in Decision:
            if d.value == decided:
                return d
    except Exception:
        pass
    return Decision.SUGGEST


def _enqueue_outbox(
    ctx: RequestContext,
    action: str,
    payload: Dict[str, Any],
    decision_id: str,
    correlation_id: str,
) -> Dict[str, Optional[str]]:
    try:
        from core.publish_outbox import enqueue  # type: ignore

        platform = str(payload.get("platform") or action.split(":", 1)[0] or "facebook").lower()
        result = enqueue(
            workspace_id=ctx.workspace_id,
            decision_id=decision_id,
            correlation_id=correlation_id,
            automation_job_id=payload.get("automation_job_id"),
            brand_id=payload.get("brand_id") or getattr(ctx, "brand_id", None),
            platform=platform,
            caption=payload.get("caption") or payload.get("text") or payload.get("content"),
            topic=payload.get("topic"),
            risk_level=str(payload.get("risk_level") or "low"),
            payload={**payload, "action": action},
            delay_seconds=float(payload.get("delay_seconds") or 0),
            idempotency_key=payload.get("idempotency_key"),
        )
        if isinstance(result, dict):
            return {
                "job_id": result.get("job_id"),
                "outbox_id": result.get("outbox_id") or result.get("id"),
            }
        return {"job_id": str(result), "outbox_id": None}
    except Exception as exc:
        # External side effects must never fail open in production.  If the
        # durable outbox cannot be created, the action has no safe execution
        # boundary and must be blocked rather than silently continuing.
        try:
            from core.fail_closed import resolve_runtime_mode
            from config.settings import settings
            mode = resolve_runtime_mode(
                getattr(settings, "environment", None) or os.getenv("ENVIRONMENT", "development")
            )
            if not mode.allow_memory_fallback:
                raise RuntimeError("Durable outbox unavailable; external action blocked") from exc
        except ImportError:
            pass
        logger.debug("Outbox enqueue failed: %s", exc)
        return {"job_id": None, "outbox_id": None}


def _emit_event(envelope: EventEnvelope) -> None:
    try:
        from core.unified_bus import get_bus  # type: ignore

        bus = get_bus()
        if bus and hasattr(bus, "publish"):
            bus.publish(envelope.event_type, envelope.to_dict())
            return
    except Exception:
        pass
    try:
        from core.event_bus import emit  # type: ignore

        emit(envelope.event_type, envelope.to_dict())
    except Exception as exc:
        logger.debug("Event emit soft-skip: %s", exc)


def run_action_pipeline(
    *,
    action: str,
    payload: Optional[Dict[str, Any]] = None,
    permission: str = "write",
    ctx: Optional[RequestContext] = None,
    enqueue: bool = True,
    domain_execute: Optional[Callable[[Dict[str, Any]], Any]] = None,
) -> PipelineResult:
    """
    Execute the canonical organism decision path for one action.

    If decision is ALLOW (or AUTO_*), optionally enqueue outbox and/or run domain_execute.
    """
    context = ctx or require_request_context()
    payload = dict(payload or {})
    correlation_id = context.correlation_id or str(uuid.uuid4())
    events: list = []

    # 1. Authorization
    authz_err = _authorize(context, permission)
    if authz_err:
        result = PipelineResult(
            decision=Decision.BLOCK,
            allowed=False,
            reason=authz_err,
            correlation_id=correlation_id,
            workspace_id=context.workspace_id,
        )
        _emit_event(
            EventEnvelope(
                event_type="GuardianBlockedAction",
                workspace_id=context.workspace_id,
                actor_id=context.principal_id,
                correlation_id=correlation_id,
                payload={"action": action, "reason": authz_err},
            )
        )
        return result

    # 2. Guardian
    guardian = _guardian_check(context, action, payload)

    # 3. Metabolism
    metabolism = _metabolism_check(context, action, payload.get("cost") or {})

    # 4. Council (advisory)
    council = _council_advise(context, action, payload)

    # 5. Director decision
    decision = _director_decide(context, action, guardian, metabolism, council)
    allowed = decision in (
        Decision.ALLOW,
        Decision.AUTO_LOW,
        Decision.AUTO_MEDIUM,
        Decision.AUTO_HIGH,
    )

    result = PipelineResult(
        decision=decision,
        allowed=allowed,
        reason=guardian.get("reason") or metabolism.get("reason") or council.get("reason") or decision.value,
        correlation_id=correlation_id,
        workspace_id=context.workspace_id,
        organism_id=context.workspace_id,
        guardian=guardian,
        metabolism=metabolism,
        council=council,
        director={"decision": decision.value},
    )

    # Register with DecisionGate so publish workers can require decision_id
    try:
        from core.decision_gate import DecisionGate, DecisionSource, decision_gate
        gate = decision_gate
        src = DecisionSource.DIRECTOR_AUTO
        if context.species_privileges:
            src = DecisionSource.SPECIES_OVERRIDE
        elif decision.value == "SUGGEST":
            src = DecisionSource.REFLEX_ALLOW
        issued = gate.issue(
            source=src,
            workspace_id=context.workspace_id,
            action_name=action,
            approved=allowed,
            correlation_id=correlation_id,
            risk_level=str(guardian.get("verdict", "unknown")),
            reason=result.reason,
            metadata={"pipeline_decision_id": result.decision_id},
        )
        result.metadata["decision_gate_id"] = issued.decision_id
        # Prefer gate id as authoritative decision_id for downstream
        result.decision_id = issued.decision_id
    except Exception as exc:
        logger.debug("DecisionGate issue soft: %s", exc)
        # Production autonomous/sensitive actions cannot proceed without a
        # durable/action-scoped gate record.
        try:
            from core.fail_closed import resolve_runtime_mode
            from config.settings import settings
            mode = resolve_runtime_mode(
                getattr(settings, "environment", None) or os.getenv("ENVIRONMENT", "development")
            )
            if not mode.allow_memory_fallback and action in ("publish", "plugin_execute", "workflow_run", "delete", "token_create"):
                result.allowed = False
                result.decision = Decision.BLOCK
                result.reason = "DecisionGate unavailable in production"
        except Exception:
            pass

    _emit_event(
        EventEnvelope(
            event_type="DecisionCreated",
            workspace_id=context.workspace_id,
            actor_id=context.principal_id,
            correlation_id=correlation_id,
            payload={
                "action": action,
                "decision": result.decision.value,
                "decision_id": result.decision_id,
                "approved": result.allowed,
                "guardian": guardian,
                "council": council,
                "director": result.director,
            },
        )
    )
    events.append("DecisionCreated")

    if not allowed:
        if decision == Decision.BLOCK:
            _emit_event(
                EventEnvelope(
                    event_type="GuardianBlockedAction",
                    workspace_id=context.workspace_id,
                    actor_id=context.principal_id,
                    correlation_id=correlation_id,
                    payload={"action": action, "decision_id": result.decision_id},
                )
            )
            events.append("GuardianBlockedAction")
        result.events = events
        return result

    # 6. Outbox / job boundary for external effects
    if enqueue and action in ("publish", "notify", "plugin_execute", "social_post"):
        ids = _enqueue_outbox(context, action, payload, result.decision_id, correlation_id)
        result.job_id = ids.get("job_id")
        result.outbox_id = ids.get("outbox_id")
        _emit_event(
            EventEnvelope(
                event_type="PublishRequested",
                workspace_id=context.workspace_id,
                actor_id=context.principal_id,
                correlation_id=correlation_id,
                payload={"action": action, "outbox_id": result.outbox_id, "job_id": result.job_id},
            )
        )
        events.append("PublishRequested")

    # 7. Optional synchronous domain hook (internal only — not external platform)
    if domain_execute is not None:
        try:
            domain_result = domain_execute(payload)
            result.metadata["domain_result"] = domain_result
        except Exception as exc:
            result.metadata["domain_error"] = str(exc)
            logger.exception("Domain execute failed in pipeline")

    result.events = events
    result.metadata["completed_at"] = datetime.now(timezone.utc).isoformat()
    return result
