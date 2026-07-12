"""Church MIS Plugin — Kanyoza AI Platform."""

import logging
from typing import Any

from plugins.industries.base import IndustryPlugin

logger = logging.getLogger("kanyoza.plugins.school_mis")


class SchoolMISPlugin(IndustryPlugin):
    """
    Church Management Information System plugin.

    Handles:
      - Student registration and management
      - Service attendance tracking
      - Donation and tithe recording
      - Event scheduling
      - WhatsApp announcements to students
    """

    def __init__(self, supabase_client=None, redis_client=None):
        super().__init__(supabase_client, redis_client)
        # Lazy imports to avoid circular dependencies
        from .repository import SchoolMISRepository
        from .commands import SchoolMISCommands
        
        self.repo = SchoolMISRepository(supabase_client)
        self.commands_handler = SchoolMISCommands(self.repo, redis_client)

    def get_commands(self) -> list[dict]:
        return self.commands_handler.list_all()

    def execute(self, command: str, params: dict, workspace_id: str = "default") -> dict:
        return self.commands_handler.handle(command, params, workspace_id)

    def get_webhooks(self) -> list[dict]:
        return [
        {
            "path": "/webhooks/church/student",
            "method": "POST",
            "handler": "handle_student_webhook",  # ← String, not function
            "description": "Student registration from external form",
        },
    ]

    def _handle_student_webhook(self, data: dict) -> dict:
        """Handle incoming student registration webhook."""
        workspace_id = data.get("workspace_id", "default")
        result = self.commands_handler.handle("add_student", data, workspace_id)
        logger.info("Church webhook processed: %s", result.get("message"))
        return result
