"""CRM Plugin — Kanyoza AI Platform."""

import logging
from typing import Any, Optional

from plugins.industries.base import IndustryPlugin

logger = logging.getLogger("kanyoza.plugins.crm")


class CRMPlugin(IndustryPlugin):
    pack_id = "crm"
    pack_version = "1.1"
    display_name = "Customer Relationship Management"
    capabilities = ("contacts", "deals", "followups", "meetings", "messaging", "activities", "outcomes")
    resources = ("contacts", "deals", "followups", "meetings", "activities", "customer_outcomes")
    policies = ("crm.workspace_access", "crm.contact_access", "crm.activity_integrity", "crm.outcome_integrity")
    workflows = ("lead_followup", "deal_progression", "customer_engagement", "crm_outcome_loop")
    events = ("contact.created", "contact.updated", "deal.created", "deal.updated", "followup.created", "meeting.created", "activity.recorded", "crm.outcome.observed")
    external_actions = ("crm.send_message", "crm.record_activity", "crm.record_outcome")

    """
    Customer Relationship Management plugin.

    Handles:
      - Contact and lead management
      - Sales deal tracking
      - Follow-up scheduling
      - Meeting coordination
      - Bulk messaging to contacts

    Species Law: workspace_id for execution comes from RequestContext when
    bound; client-provided workspace is only used by species operators.
    """

    def __init__(self, supabase_client=None, redis_client=None):
        super().__init__(supabase_client, redis_client)
        from .repository import CRMRepository
        from .commands import CRMCommands

        self.repo = CRMRepository(supabase_client)
        self.commands_handler = CRMCommands(self.repo, redis_client)

    def get_commands(self) -> list[dict]:
        return self.commands_handler.list_all()

    def execute(self, command: str, params: dict, workspace_id: str = "default") -> dict:
        workspace_id = self.resolve_workspace(workspace_id)
        safe_params = {k: v for k, v in (params or {}).items() if k != "workspace_id"}
        return self.commands_handler.handle(command, safe_params, workspace_id)

    def get_webhooks(self) -> list[dict]:
        return [
            {
                "path": "/webhooks/crm/contact",
                "method": "POST",
                "handler": "handle_contact_webhook",
                "description": "Contact creation from external form or website",
            },
        ]

    def _handle_contact_webhook(self, data: dict) -> dict:
        # Webhooks must authenticate at the HTTP layer before this runs.
        # Prefer RequestContext; fall back only when no context (legacy path).
        workspace_id = self.resolve_workspace(data.get("workspace_id", "default"))
        safe = {k: v for k, v in (data or {}).items() if k != "workspace_id"}
        result = self.commands_handler.handle("add_contact", safe, workspace_id)
        logger.info("CRM webhook processed: %s", result.get("message"))
        return result
