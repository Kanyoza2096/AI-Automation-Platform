"""Church MIS Plugin — Kanyoza AI Platform."""

import logging
from typing import Any

from plugins.base import IndustryPlugin
class ChurchMISPlugin(IndustryPlugin):
    def __init__(self, supabase_client=None, redis_client=None):
        super().__init__(supabase_client, redis_client)
        from .repository import ChurchMISRepository
        from .commands import ChurchMISCommands
        self.repo = ChurchMISRepository(supabase_client)
        self.commands = ChurchMISCommands(self.repo, redis_client)
logger = logging.getLogger("kanyoza.plugins.church_mis")


class ChurchMISPlugin(IndustryPlugin):
    """
    Church Management Information System plugin.

    Handles:
      - Member registration and management
      - Service attendance tracking
      - Donation and tithe recording
      - Event scheduling
      - WhatsApp announcements to members
    """

    def __init__(self, supabase_client=None, redis_client=None):
        super().__init__(supabase_client, redis_client)
        self.repo = ChurchMISRepository(supabase_client)
        self.commands = ChurchMISCommands(self.repo, redis_client)

    def get_commands(self) -> list[dict]:
        return self.commands.list_all()

    def execute(self, command: str, params: dict, workspace_id: str = "default") -> dict:
        return self.commands.handle(command, params, workspace_id)

    def get_webhooks(self) -> list[dict]:
        return [
            {
                "path": "/webhooks/church/member",
                "method": "POST",
                "handler": self._handle_member_webhook,
                "description": "Member registration from external form",
            },
        ]

    def _handle_member_webhook(self, data: dict) -> dict:
        """Handle incoming member registration webhook."""
        workspace_id = data.get("workspace_id", "default")
        result = self.commands.handle("add_member", data, workspace_id)
        logger.info("Church webhook processed: %s", result.get("message"))
        return result
