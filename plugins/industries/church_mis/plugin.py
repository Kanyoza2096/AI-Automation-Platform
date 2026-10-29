"""Church MIS Plugin — Kanyoza AI Platform."""

import logging
from typing import Any

from plugins.industries.base import IndustryPlugin

logger = logging.getLogger("kanyoza.plugins.church_mis")


class ChurchMISPlugin(IndustryPlugin):
    pack_id = "church_mis"
    pack_version = "1.0"
    display_name = "Church Management Information System"
    capabilities = ("members", "attendance", "donations", "scheduling", "messaging")
    resources = ("members", "attendance", "donations", "events")
    policies = ("church.workspace_access", "church.member_access")
    workflows = ("member_registration", "attendance", "donation_recording")
    events = ("member.created", "attendance.recorded", "donation.recorded")

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
        # Lazy imports to avoid circular dependencies
        from .repository import ChurchMISRepository
        from .commands import ChurchMISCommands
        
        self.repo = ChurchMISRepository(supabase_client)
        self.commands_handler = ChurchMISCommands(self.repo, redis_client)

    def get_commands(self) -> list[dict]:
        return self.commands_handler.list_all()

    def execute(self, command: str, params: dict, workspace_id: str = "default") -> dict:
        workspace_id = self.resolve_workspace(workspace_id)
        safe_params = {k: v for k, v in (params or {}).items() if k != "workspace_id"}
        return self.commands_handler.handle(command, safe_params, workspace_id)

    def get_webhooks(self) -> list[dict]:
        return [
        {
            "path": "/webhooks/church/member",
            "method": "POST",
            "handler": "handle_member_webhook",  # ← String, not function
            "description": "Member registration from external form",
        },
    ]

    def _handle_member_webhook(self, data: dict) -> dict:
        """Handle incoming member registration webhook."""
        workspace_id = data.get("workspace_id", "default")
        result = self.commands_handler.handle("add_member", data, workspace_id)
        logger.info("Church webhook processed: %s", result.get("message"))
        return result
