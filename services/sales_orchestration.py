"""Deterministic, bounded sales next-action orchestration.

This module is deliberately not an AI agent and never performs a side effect itself.
It maps trusted sales signals/state to one allow-listed next action, then delegates
that action to the existing Action Kernel. The result is therefore still subject to
workspace scope, authorization, idempotency, provider checks, and approval policy.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SalesDecision:
    action: str
    confidence: float
    reason: str
    requires_approval: bool


class SalesOrchestrator:
    VERSION = "1.0.0"
    EXECUTION_PLANE = "action_kernel_delegate"

    # Policy is intentionally bounded. Event payloads can supply values, but can
    # never select an arbitrary action/handler.
    POLICY = {
        "purchase_interest": ("crm_schedule_followup", 0.94, "Customer expressed purchase interest; keep momentum with a sales follow-up.", False),
        "price_request": ("notify_operator", 0.91, "Customer requested pricing; route the commercial request to an operator.", False),
        "meeting_request": ("crm_add_meeting", 0.96, "Customer requested a meeting; create governed sales work for scheduling.", False),
        "objection": ("notify_operator", 0.86, "Customer raised an objection; a human should review the commercial context.", False),
        "general_inquiry": ("notify_operator", 0.72, "Customer response needs human review before a consequential sales action.", False),
        "opt_out": ("notify_operator", 0.99, "Customer requested no further contact; surface the suppression signal immediately.", False),
    }

    @classmethod
    def decide(cls, event: dict[str, Any]) -> SalesDecision | None:
        intent = str(event.get("customer_intent") or event.get("intent") or "").strip().lower()
        if not intent:
            return None
        policy = cls.POLICY.get(intent)
        if not policy:
            return None
        action, confidence, reason, requires_approval = policy
        # Never create an external communication directly from an interpreted
        # response. Communication remains a separate, explicit governed policy.
        return SalesDecision(action, confidence, reason, requires_approval)

    @classmethod
    def parameters_for(cls, decision: SalesDecision, event: dict[str, Any]) -> dict[str, Any]:
        intent = str(event.get("customer_intent") or event.get("intent") or "").strip().lower()
        phone = str(event.get("phone") or "").strip()
        name = str(event.get("name") or event.get("customer_name") or phone or "customer").strip()
        if decision.action == "crm_schedule_followup":
            return {
                "contact": event.get("contact") or name,
                "date": event.get("follow_up_at") or event.get("date"),
                "notes": f"KDO sales orchestration: {intent}",
            }
        if decision.action == "crm_add_meeting":
            return {
                "title": "Customer-requested sales meeting",
                "description": str(event.get("message") or f"Customer {name} requested a meeting."),
                "date": str(event.get("date") or ""),
                "time": str(event.get("time") or ""),
                "location": str(event.get("location") or "Virtual"),
            }
        level = "error" if intent == "opt_out" else "warning"
        return {
            "message": f"Customer {phone or name} signal requires sales attention: {intent}.",
            "level": level,
            "channels": ["email"],
        }

    @classmethod
    async def execute(cls, *, event: dict[str, Any], workspace_id: str, idempotency_key: str,
                      approved: bool, db=None, production: bool = False, rule=None) -> dict[str, Any]:
        decision = cls.decide(event)
        if decision is None:
            return {"status": "no_action", "reason": "No bounded sales policy matched the customer signal."}

        from services.action_kernel import build_default_action_kernel
        kernel = build_default_action_kernel(db, allow_in_memory=not production)
        parameters = cls.parameters_for(decision, event)
        result = await kernel.execute(
            workspace_id=str(workspace_id),
            action_type=decision.action,
            parameters={**event, **parameters},
            risk_level="high" if decision.action == "whatsapp_message" else "medium",
            decision_id=getattr(rule, "id", None),
            correlation_id=str(event.get("correlation_id") or event.get("event_id") or ""),
            idempotency_key=str(idempotency_key),
            source="sales_orchestrator",
            approved=bool(approved),
        )
        return {
            **result,
            "orchestration": {
                "version": cls.VERSION,
                "action": decision.action,
                "confidence": decision.confidence,
                "reason": decision.reason,
                "requires_approval": decision.requires_approval,
            },
        }
