"""Governed adapter for connector side effects.

Every externally visible connector mutation (publish/edit/delete) can be
routed through one durable execution contract.  Read-only connector methods
are intentionally not wrapped here.
"""
from __future__ import annotations

import hashlib
from typing import Any, Optional

from services.connectors.capabilities import ConnectorCapabilities, capabilities
from services.execution_ledger import ExecutionLedger
from services.external_action_executor import ExternalActionExecutor


class GovernedConnector:
    """Wrap a concrete connector with durable idempotency and leases."""

    MUTATING_ACTIONS = {"publish", "edit", "delete"}

    def __init__(self, connector: Any, *, workspace_id: str, ledger: ExecutionLedger):
        if not workspace_id:
            raise ValueError("workspace_id is required")
        self.connector = connector
        self.workspace_id = workspace_id
        self.ledger = ledger
        self.executor = ExternalActionExecutor(ledger)

    @property
    def platform(self) -> str:
        return str(getattr(self.connector, "platform", "unknown"))

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return capabilities(self.connector)

    def _key(self, action: str, supplied: Optional[str], *parts: Any) -> str:
        if supplied:
            return supplied
        material = "|".join(str(p or "") for p in (self.workspace_id, self.platform, action, *parts))
        return ExecutionLedger.make_idempotency_key(material)

    async def publish(self, content: str, *, idempotency_key: Optional[str] = None, **kwargs: Any) -> dict[str, Any]:
        # Destination identity is part of publish idempotency. Different pages,
        # channels, or accounts in one workspace must never share a replay key.
        destination = (kwargs.get("social_account_id") or kwargs.get("account_id") or
                       kwargs.get("page_id") or kwargs.get("channel_id") or
                       kwargs.get("phone_number_id") or kwargs.get("recipient_id") or "")
        key = self._key("publish", idempotency_key, destination,
                         hashlib.sha256(content.encode()).hexdigest(), kwargs.get("image_url", ""))
        return await self._execute("publish", key, lambda: self.connector.publish(content, **kwargs))

    async def edit(self, post_id: str, content: str, *, idempotency_key: Optional[str] = None) -> dict[str, Any]:
        key = self._key("edit", idempotency_key, post_id, hashlib.sha256(content.encode()).hexdigest())
        return await self._execute("edit", key, lambda: self.connector.edit(post_id, content))

    async def delete(self, post_id: str, *, idempotency_key: Optional[str] = None) -> dict[str, Any]:
        key = self._key("delete", idempotency_key, post_id)
        return await self._execute("delete", key, lambda: self.connector.delete(post_id))

    async def _execute(self, action: str, key: str, operation) -> dict[str, Any]:
        if action not in self.MUTATING_ACTIONS:
            raise ValueError(f"Unsupported governed action: {action}")
        result = await self.executor.execute(
            workspace_id=self.workspace_id,
            action=f"connector.{action}",
            idempotency_key=key,
            provider=self.platform,
            operation=operation,
            metadata={"platform": self.platform, "action": action},
        )
        if result.get("status") == "uncertain":
            result["reconciliation_required"] = True
        return result

    def lookup_post(self, post_id: str) -> Any:
        lookup = getattr(self.connector, "lookup_post", None)
        if not callable(lookup):
            return None
        try:
            return lookup(post_id)
        except Exception:
            return None

    def health(self):
        return self.connector.health()

    def validate(self):
        return self.connector.validate()

    def fetch_comments(self, post_id: str, limit: int = 50):
        return self.connector.fetch_comments(post_id, limit)

    def fetch_messages(self, limit: int = 50):
        return self.connector.fetch_messages(limit)
