"""Hospital MIS Plugin — Kanyoza AI Platform."""

import logging
from typing import Any

from plugins.industries.base import IndustryPlugin

logger = logging.getLogger("kanyoza.plugins.hospital")


class HospitalMISPlugin(IndustryPlugin):
    """
    Hospital / Clinic Management Information System plugin.

    Handles:
      - Patient registration and management
      - Vitals check-in tracking
      - Billing and payments
      - Event/shift scheduling
      - WhatsApp announcements to patients/staff
    """

    def __init__(self, supabase_client=None, redis_client=None):
        super().__init__(supabase_client, redis_client)
        # Lazy imports to avoid circular dependencies
        from .repository import HospitalMISRepository
        from .commands import HospitalMISCommands
        
        self.repo = HospitalMISRepository(supabase_client)
        self.commands_handler = HospitalMISCommands(self.repo, redis_client)

    def get_commands(self) -> list[dict]:
        return self.commands_handler.list_all()

    def execute(self, command: str, params: dict, workspace_id: str = "default") -> dict:
        return self.commands_handler.handle(command, params, workspace_id)

    def get_webhooks(self) -> list[dict]:
        return [
        {
            "path": "/webhooks/hospital/patient",
            "method": "POST",
            "handler": "handle_patient_webhook",  # ← String, not function
            "description": "Patient registration from external form",
        },
    ]

    def _handle_patient_webhook(self, data: dict) -> dict:
        """Handle incoming patient registration webhook."""
        workspace_id = data.get("workspace_id", "default")
        result = self.commands_handler.handle("add_patient", data, workspace_id)
        logger.info("Hospital webhook processed: %s", result.get("message"))
        return result

