"""
Translate AI interpretation data into KDO-owned intent, never provider-owned intent.

This module is the boundary between "the model produced something" and
"KDO has decided to consider doing something." Everything downstream of
it — Council, DecisionGate, Action Kernel, Ledger, Reconciliation —
assumes that the thing it receives is a KDO ActionIntent, not a
provider's suggestion. This function guarantees that assumption.

Design invariants (each is load-bearing):

  1. The AI cannot act. It produces an AIReceipt (data), not an
     execution. This function never executes anything.

  2. The allowlist is the security boundary. The set of permissible
     actions is defined by the sector pack, not by the model. An AI
     recommendation not in the allowlist raises ValueError — the
     violation is loud, not silently resolved.

  3. Parameters come from the rule, not the AI. The AI can recommend
     which action to consider, but the parameters that reach the
     ActionIntent are the ones the rule specifies. AI-supplied
     parameters are never trusted.

  4. No-action is a valid outcome. When the AI interprets the input
     correctly and no action is warranted (informational input,
     a request that is not KDO's to fulfill, etc.), the function
     returns None. The caller decides how to record the observation;
     this function does not fabricate an intent.

  5. Provenance is preserved. Every intent carries the receipt's
     correlation_id, so the specific AI call that inspired it is one
     hop away from the resulting action.
"""
from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

from services.action_kernel import ActionIntent
from services.ai_provider import AIReceipt

logger = logging.getLogger("kanyoza.ai_intent")


# The set of ways the AI may signal that no action is required. All three
# are treated identically. Providers differ in which they emit; the
# formulation layer normalizes.
_NO_ACTION_FLAG_KEYS = ("action_required", "requires_action", "actionable")


def _ai_signals_no_action(data: Mapping[str, Any]) -> bool:
    """Return True if the AI's structured output explicitly disclaims action.

    Recognizes three conventions:
      - action_required: false
      - requires_action: false
      - actionable: false

    An empty recommendation list also counts as no-action (handled by the
    caller below), but this helper only looks at explicit flags.
    """
    for key in _NO_ACTION_FLAG_KEYS:
        value = data.get(key)
        if value is False:
            return True
    return False


def _select_candidate_action(data: Mapping[str, Any]) -> Optional[str]:
    """Extract the AI's preferred action name, or None if it named no action.

    Preference order:
      1. data["recommended_action"]     — explicit single recommendation
      2. data["recommended_actions"][0] — first in the ordered list

    Returns None when neither produces a string.
    """
    candidate = data.get("recommended_action")
    if isinstance(candidate, str) and candidate.strip():
        return candidate.strip()

    candidates = data.get("recommended_actions") or []
    if isinstance(candidates, list):
        for entry in candidates:
            if isinstance(entry, str) and entry.strip():
                return entry.strip()
    return None


def formulate_action_intent_from_ai(
    receipt: AIReceipt,
    *,
    workspace_id: str,
    allowed_actions: Mapping[str, Mapping[str, Any]],
    idempotency_key: str,
    decision_id: Optional[str] = None,
) -> Optional[ActionIntent]:
    """Translate an AI interpretation into a KDO intent, or None for no-action.

    Returns:
      - ActionIntent — the AI's interpretation maps to a permitted action,
        and the rule fully specifies the parameters for it.
      - None — the AI's interpretation is valid but no action is warranted.
        Callers should record the observation and continue.

    Raises:
      - ValueError — the receipt is not a success, the AI's recommendation
        is malformed, the AI recommends an action not in the allowlist, or
        the allowlist rule is missing required parameters. These are all
        policy or configuration violations; the caller must not proceed.

    The `allowed_actions` mapping is the sector pack's allowlist. Each key
    is a permitted action_type. Each value is a rule with these fields:
      - risk_level: str (default "low")
      - parameters: dict (default {})   — the parameter values to use
      - required_parameters: tuple (default ()) — keys that must be present

    AI-supplied parameters are never read. The rule's parameters are the
    source of truth. This prevents the model from injecting values into
    a permitted action.
    """
    # ── Precondition: the receipt must be a success ────────────────────
    if receipt.status != "succeeded":
        raise ValueError(
            f"AI receipt must be successful before KDO can formulate intent; "
            f"got status={receipt.status!r}"
        )

    data = receipt.structured_output or {}
    if not isinstance(data, Mapping):
        raise ValueError(
            f"AI receipt structured_output is not a mapping; "
            f"got {type(data).__name__}"
        )

    # ── No-action paths ────────────────────────────────────────────────
    # 1) Explicit disclaimer from the AI
    if _ai_signals_no_action(data):
        logger.debug(
            "AI interpretation explicitly disclaims action; "
            "no intent formulated correlation_id=%s provider=%s",
            receipt.correlation_id, receipt.provider,
        )
        return None

    # 2) No recommendation at all
    candidate = _select_candidate_action(data)
    if candidate is None:
        logger.debug(
            "AI interpretation contains no recommended action; "
            "no intent formulated correlation_id=%s provider=%s",
            receipt.correlation_id, receipt.provider,
        )
        return None

    # ── Allowlist enforcement ──────────────────────────────────────────
    if candidate not in allowed_actions:
        permitted = sorted(allowed_actions.keys())
        raise ValueError(
            f"AI recommended action {candidate!r} which is not in the "
            f"KDO allowlist for this context; permitted: {permitted}"
        )

    rule = allowed_actions[candidate]
    if not isinstance(rule, Mapping):
        raise ValueError(
            f"allowlist rule for {candidate!r} is not a mapping; "
            f"got {type(rule).__name__}"
        )

    # ── Parameter construction ─────────────────────────────────────────
    # Parameters are taken from the rule, not from the AI. The AI's
    # recommendation may name a permitted action; it may not name the
    # parameters for that action.
    rule_parameters = rule.get("parameters")
    parameters: dict[str, Any] = (
        dict(rule_parameters) if isinstance(rule_parameters, Mapping) else {}
    )

    # ── Rule completeness check ────────────────────────────────────────
    # Required parameters must be present and non-empty. This catches a
    # misconfigured allowlist at formulation time — before the intent
    # reaches the kernel, where the failure would be less traceable.
    required = rule.get("required_parameters") or ()
    if required:
        missing = [
            key for key in required
            if parameters.get(key) in (None, "")
        ]
        if missing:
            raise ValueError(
                f"allowlist rule for {candidate!r} is missing required "
                f"parameters {missing}; rule definition is misconfigured"
            )

    # ── Build the intent ───────────────────────────────────────────────
    intent = ActionIntent(
        workspace_id=workspace_id,
        action_type=candidate,
        parameters=parameters,
        risk_level=str(rule.get("risk_level", "low")),
        decision_id=decision_id,
        correlation_id=receipt.correlation_id,
        idempotency_key=idempotency_key,
        source="kdo.ai_interpretation",
    )

    logger.info(
        "AI interpretation formulated into intent: action=%s workspace=%s "
        "provider=%s model=%s correlation_id=%s",
        candidate, workspace_id, receipt.provider, receipt.model,
        receipt.correlation_id,
    )

    return intent


__all__ = ["formulate_action_intent_from_ai"]
