"""CRM Plugin — Kanyoza AI Platform."""

import logging
from typing import Any

from plugins.industries.base import IndustryPlugin

logger = logging.getLogger("kanyoza.plugins.crm")


class CRMPlugin(IndustryPlugin):
    """
    Customer Relationship Management plugin.

    Handles:
      - Contact and lead management
      - Sales deal tracking
      - Follow-up scheduling
      - Meeting coordination
      - Bulk messaging to contacts
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
        return self.commands_handler.handle(command, params, workspace_id)

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
        workspace_id = data.get("workspace_id", "default")
        result = self.commands_handler.handle("add_contact", data, workspace_id)
        logger.info("CRM webhook processed: %s", result.get("message"))
        return result
