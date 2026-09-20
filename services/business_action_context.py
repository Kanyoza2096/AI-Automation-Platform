"""V73.8 trusted business-action context resolution.

This layer supplies only deterministic, business-owned facts required to turn a
policy decision into an executable ActionIntent. AI output, raw customer text,
and arbitrary webhook fields are never treated as scheduling authority.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping


class BusinessActionContextResolver:
    VERSION = "v73.8.1"
    DEFAULT_FOLLOW_UP_HOURS = 24
    MAX_FOLLOW_UP_HOURS = 720

    def __init__(self, *, workspace_policy: Mapping[str, Mapping[str, Any]] | None = None):
        self.workspace_policy = dict(workspace_policy or {})

    def resolve(self, *, workspace_id: str, action_type: str, customer_id: str,
                trusted_context: Mapping[str, Any] | None = None) -> dict[str, Any]:
        ws = str(workspace_id or "").strip()
        action = str(action_type or "").strip()
        customer = str(customer_id or "").strip()
        supplied = dict(trusted_context or {}) if isinstance(trusted_context, Mapping) else {}
        policy = self.workspace_policy.get(ws, {})

        # Explicit trusted context wins. It must come from a trusted internal
        # source; this resolver never interprets AI text as a date or contact.
        merged: dict[str, Any] = {}
        for source in (policy, supplied):
            for key in ("contact", "follow_up_at", "date", "follow_up_hours"):
                if key in source and source[key] not in (None, ""):
                    merged[key] = source[key]

        if action != "crm_schedule_followup":
            return {"status": "ready", "context": merged, "reason": "no_additional_context_required"}

        contact = str(merged.get("contact") or customer).strip()
        if not contact:
            return {"status": "needs_review", "context": {}, "reason": "customer_contact_missing"}

        explicit_date = str(merged.get("follow_up_at") or merged.get("date") or "").strip()
        if explicit_date:
            return {
                "status": "ready",
                "context": {"contact": contact, "follow_up_at": explicit_date},
                "reason": "trusted_followup_schedule_supplied",
            }

        raw_hours = merged.get("follow_up_hours", self.DEFAULT_FOLLOW_UP_HOURS)
        try:
            hours = float(raw_hours)
        except (TypeError, ValueError):
            return {"status": "needs_review", "context": {}, "reason": "follow_up_hours_invalid"}
        if hours < 1 or hours > self.MAX_FOLLOW_UP_HOURS:
            return {"status": "needs_review", "context": {}, "reason": "follow_up_hours_out_of_bounds"}

        follow_up_at = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
        return {
            "status": "ready",
            "context": {"contact": contact, "follow_up_at": follow_up_at},
            "reason": "governed_followup_schedule_derived_from_business_policy",
            "policy": {"follow_up_hours": hours, "version": self.VERSION},
        }
