"""CRM Plugin — Kanyoza AI Platform."""

import logging
from typing import Any, Optional

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
        workspace_id = self._resolve_workspace(workspace_id, params)
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
        workspace_id = self._resolve_workspace(
            data.get("workspace_id", "default"), data
        )
        safe = {k: v for k, v in (data or {}).items() if k != "workspace_id"}
        result = self.commands_handler.handle("add_contact", safe, workspace_id)
        logger.info("CRM webhook processed: %s", result.get("message"))
        return result

    def _resolve_workspace(self, provided: Optional[str], params: Optional[dict]) -> str:
        try:
            from core.plugin_execution import resolve_plugin_workspace

            return resolve_plugin_workspace(provided)
        except Exception:
            # No RequestContext (worker / offline) — use provided with audit log
            logger.warning(
                "CRM execute without RequestContext; using provided workspace=%s",
                provided,
            )
            return provided or "default"
