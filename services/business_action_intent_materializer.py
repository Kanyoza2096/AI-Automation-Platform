"""V73.2 trusted business-action intent materialization.

Turns a validated V65.9 policy handoff into the narrow Action Kernel parameter
set required for one registered action. This layer never copies arbitrary model
output and never performs an external side effect.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MaterializationResult:
    status: str
    action_type: str
    parameters: dict[str, Any]
    reason: str


class BusinessActionIntentMaterializer:
    VERSION = "v73.2.1"

    @staticmethod
    def materialize(handoff: dict[str, Any], *, context_resolver=None) -> MaterializationResult:
        from services.business_decision_action_handoff import validate_action_kernel_handoff

        validate_action_kernel_handoff(handoff)
        action = str(handoff["action_type"]).strip()
        customer_id = str(handoff.get("customer_id") or "").strip()
        message_id = str(handoff.get("parameters", {}).get("message_id") or handoff.get("message_id") or "").strip()
        correlation_id = str(handoff.get("parameters", {}).get("correlation_id") or "").strip()
        intent = str(handoff.get("intent") or "general_inquiry").strip().lower()

        if not customer_id:
            return MaterializationResult("needs_review", action, {}, "customer_identity_missing")

        if action == "crm_add_meeting":
            # The Action Kernel contract only requires a title. Scheduling fields
            # are optional and must never be invented from AI text.
            params: dict[str, Any] = {
                "title": "Customer-requested sales meeting",
                "description": "Customer requested a sales meeting.",
                "customer_id": customer_id,
            }
            if message_id:
                params["message_id"] = message_id
            if correlation_id:
                params["correlation_id"] = correlation_id
            return MaterializationResult("ready", action, params, "trusted_meeting_intent_materialized")

        if action == "notify_operator":
            messages = {
                "price_request": "Customer requested pricing or a quotation.",
                "objection": "Customer raised a commercial objection requiring review.",
                "opt_out": "Customer requested no further contact; suppression review is required.",
                "general_inquiry": "Customer inquiry requires operator review.",
            }
            message = messages.get(intent, "Customer business signal requires operator review.")
            params = {
                "message": f"{message} customer_id={customer_id}",
                "level": "error" if intent == "opt_out" else "warning",
                "channels": ["email"],
                "customer_id": customer_id,
            }
            if message_id:
                params["message_id"] = message_id
            return MaterializationResult("ready", action, params, "trusted_operator_notification_materialized")

        if action == "crm_schedule_followup":
            if context_resolver is not None:
                resolved = context_resolver.resolve(
                    workspace_id=str(handoff.get("workspace_id") or ""),
                    action_type=action,
                    customer_id=customer_id,
                    trusted_context=handoff.get("trusted_context") or {},
                )
                if resolved.get("status") != "ready":
                    return MaterializationResult("needs_review", action, {}, str(resolved.get("reason") or "trusted_action_context_unavailable"))
                trusted = resolved.get("context") or {}
            else:
                trusted = handoff.get("trusted_context") or {}
                if not isinstance(trusted, dict):
                    trusted = {}
            date = str(trusted.get("follow_up_at") or trusted.get("date") or "").strip()
            if not date:
                return MaterializationResult(
                    "needs_review", action, {}, "follow_up_date_missing; refusing to invent scheduling data"
                )
            contact = str(trusted.get("contact") or customer_id).strip()
            params = {
                "contact": contact,
                "date": date,
                "notes": "Governed follow-up from customer purchase-interest signal.",
            }
            return MaterializationResult("ready", action, params, "trusted_followup_materialized")

        return MaterializationResult("blocked", action, {}, "action_not_supported_by_v73_materializer")
