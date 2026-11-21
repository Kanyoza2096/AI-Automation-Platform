"""
Publish authorization — DecisionGate enforcement for all publish paths.

Contract:
  - strict=True  → missing/invalid decision_id DENIES (never mints authority)
  - strict=False → legacy soft path may issue LEGACY decision
  - AUTO_ISSUE_PUBLISH_DECISION only applies when strict is False/None and
    env allows it; workers that need DIRECTOR_AUTO must call decision_gate.issue
    explicitly *before* assert_publish_allowed.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from core.decision_gate import (
    Decision,
    DecisionGateError,
    DecisionSource,
    decision_gate,
)

logger = logging.getLogger("kanyoza.publish_authorization")


def _strict_default() -> bool:
    v = os.getenv("PUBLISH_STRICT", "1").lower()
    return v in ("1", "true", "yes", "on")


def _auto_issue_enabled() -> bool:
    """Only used on non-strict/legacy paths."""
    v = os.getenv("AUTO_ISSUE_PUBLISH_DECISION", "0").lower()
    return v in ("1", "true", "yes", "on")


def assert_publish_allowed(
    decision_id: Optional[str],
    workspace_id: str,
    *,
    strict: Optional[bool] = None,
    consume: bool = False,
) -> Decision:
    """
    Validate that publish is authorized by DecisionGate.

    When strict=True (default via PUBLISH_STRICT or explicit):
      missing decision_id → DecisionGateError (no auto-issue).

    When strict=False and AUTO_ISSUE_PUBLISH_DECISION=1:
      missing decision_id → issue DIRECTOR_AUTO then validate.

    Workers/schedulers that need auto authority must issue explicitly
    before calling this helper with strict=True.
    """
    use_strict = _strict_default() if strict is None else bool(strict)

    if not decision_id:
        if use_strict:
            raise DecisionGateError(
                "Publish blocked: missing decision_id "
                "(Council/Director/Reflex must authorize first)"
            )
        if _auto_issue_enabled():
            issued = decision_gate.issue(
                source=DecisionSource.DIRECTOR_AUTO,
                workspace_id=workspace_id or "platform",
                action_name="publish",
                approved=True,
                reason="auto_issue_non_strict",
                risk_level="low",
            )
            decision_id = issued.decision_id
            logger.info(
                "Non-strict auto-issued DIRECTOR_AUTO decision_id=%s workspace=%s",
                decision_id,
                workspace_id,
            )
        else:
            raise DecisionGateError(
                "Publish blocked: missing decision_id and AUTO_ISSUE_PUBLISH_DECISION disabled"
            )

    try:
        decision = decision_gate.require_for_publish(
            decision_id,
            workspace_id=workspace_id,
            strict=use_strict,
        )
    except DecisionGateError:
        logger.warning(
            "Publish denied workspace=%s decision_id=%s strict=%s",
            workspace_id,
            decision_id,
            use_strict,
        )
        raise

    if consume and hasattr(decision_gate, "consume"):
        try:
            decision_gate.consume(decision_id)
        except Exception as exc:
            logger.debug("decision consume soft-fail: %s", exc)

    return decision


def issue_internal_publish_decision(
    *,
    workspace_id: str,
    source: str,
    risk_level: str = "low",
    correlation_id: str | None = None,
    brand_id: str | None = None,
    platform: str = "facebook",
) -> Decision:
    """Issue a bounded server-side publish authorization for trusted triggers.

    Scheduled/manual workflow triggers are already authenticated by their
    respective entry points. This helper turns that trusted intent into a
    normal DecisionGate record so the execution plane never has to invent
    authority. Only low-risk publishing is auto-authorized; higher-risk work
    must arrive with an explicit Council/Director decision.
    """
    ws = (workspace_id or "").strip()
    if not ws:
        raise DecisionGateError("Publish authorization requires workspace_id")
    risk = (risk_level or "low").lower()
    if risk not in {"low", "trivial"}:
        raise DecisionGateError(
            f"Automatic publish authorization is limited to low risk; got '{risk}'"
        )
    trigger = (source or "workflow").strip().lower()
    allowed = {"manual", "scheduled", "workflow", "outbox", "dead_letter_retry", "queued_retry"}
    if trigger not in allowed:
        raise DecisionGateError(f"Untrusted publish trigger source: {source}")
    return decision_gate.issue(
        source=DecisionSource.DIRECTOR_AUTO,
        workspace_id=ws,
        action_name="publish",
        approved=True,
        correlation_id=correlation_id,
        risk_level=risk,
        reason=f"trusted_{trigger}_publish_trigger",
        metadata={
            "trigger_source": trigger,
            "brand_id": brand_id,
            "platform": (platform or "facebook").lower(),
            "bounded_auto_authorization": True,
        },
    )
