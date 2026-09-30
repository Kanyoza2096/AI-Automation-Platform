"""Translate AI interpretation data into KDO-owned intent, never provider-owned intent."""
from __future__ import annotations
from typing import Any, Mapping

from services.action_kernel import ActionIntent
from services.ai_provider import AIReceipt


def formulate_action_intent_from_ai(
    receipt: AIReceipt,
    *,
    workspace_id: str,
    allowed_actions: Mapping[str, Mapping[str, Any]],
    idempotency_key: str,
    decision_id: str | None = None,
) -> ActionIntent:
    if receipt.status != "succeeded":
        raise ValueError("AI receipt must be successful before KDO can formulate intent")
    data = receipt.structured_output or {}
    candidate = data.get("recommended_action")
    if not candidate:
        candidates = data.get("recommended_actions") or []
        candidate = candidates[0] if candidates else None
    if not isinstance(candidate, str) or candidate not in allowed_actions:
        raise ValueError("AI recommendation is not a KDO-permitted action")
    rule = allowed_actions[candidate]
    parameters = dict(rule.get("parameters") or {})
    return ActionIntent(
        workspace_id=workspace_id,
        action_type=candidate,
        parameters=parameters,
        risk_level=str(rule.get("risk_level", "low")),
        decision_id=decision_id,
        correlation_id=receipt.correlation_id,
        idempotency_key=idempotency_key,
        source="kdo.ai_interpretation",
    )
