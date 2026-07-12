"""Church MIS Plugin — Kanyoza AI Platform."""

import logging
from typing import Any

from plugins.industries.base import IndustryPlugin

logger = logging.getLogger("kanyoza.plugins.crm")


class CRMMISPlugin(IndustryPlugin):
    """
    Church Management Information System plugin.

    Handles:
      - Contact registration and management
      - Service attendance tracking
      - Donation and tithe recording
      - Event scheduling
      - WhatsApp announcements to contacts
    """

    def __init__(self, supabase_client=None, redis_client=None):
        super().__init__(supabase_client, redis_client)
        # Lazy imports to avoid circular dependencies
        from .repository import CRMMISRepository
        from .commands import CRMMISCommands
        
        self.repo = CRMMISRepository(supabase_client)
        self.commands_handler = CRMMISCommands(self.repo, redis_client)

    def get_commands(self) -> list[dict]:
        return self.commands_handler.list_all()

    def execute(self, command: str, params: dict, workspace_id: str = "default") -> dict:
        return self.commands_handler.handle(command, params, workspace_id)

    def get_webhooks(self) -> list[dict]:
        return [
        {
            "path": "/webhooks/church/contact",
            "method": "POST",
            "handler": "handle_contact_webhook",  # ← String, not function
            "description": "Contact registration from external form",
        },
    ]

    def _handle_contact_webhook(self, data: dict) -> dict:
        """Handle incoming contact registration webhook."""
        workspace_id = data.get("workspace_id", "default")
        result = self.commands_handler.handle("add_contact", data, workspace_id)
        logger.info("Church webhook processed: %s", result.get("message"))
        return result
