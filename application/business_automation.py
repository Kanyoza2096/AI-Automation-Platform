"""Application boundary for workspace business automation use cases.

HTTP concerns and the concrete BusinessAutomationEngine stay outside this layer.
The engine remains the authoritative orchestration/execution implementation;
this service owns workspace-scoped application intent and use-case sequencing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol


class BusinessAutomationPort(Protocol):
    async def create_rule(self, workspace_id: str, payload: Mapping[str, Any]) -> Any: ...
    async def update_rule(self, workspace_id: str, rule_id: str, payload: Mapping[str, Any]) -> Any: ...
    async def list_rules(self, workspace_id: str) -> list[Any]: ...
    async def run(self, workspace_id: str, *, event: Mapping[str, Any], rule_id: str | None = None,
                  dry_run: bool = True, approved: bool = False) -> list[Any]: ...
    async def history(self, workspace_id: str, limit: int = 50) -> list[Any]: ...


@dataclass(frozen=True)
class BusinessAutomationContext:
    workspace_id: str

    def validate(self) -> str:
        workspace_id = self.workspace_id.strip()
        if not workspace_id:
            raise ValueError("authenticated workspace scope required")
        return workspace_id


class BusinessAutomationApplicationService:
    """Application use cases over the existing governed automation engine."""

    def __init__(self, port: BusinessAutomationPort):
        self._port = port

    async def list_rules(self, context: BusinessAutomationContext) -> list[Any]:
        return await self._port.list_rules(context.validate())

    async def create_rule(self, context: BusinessAutomationContext, payload: Mapping[str, Any]) -> Any:
        return await self._port.create_rule(context.validate(), dict(payload))

    async def update_rule(self, context: BusinessAutomationContext, rule_id: str,
                          payload: Mapping[str, Any]) -> Any:
        return await self._port.update_rule(context.validate(), rule_id, dict(payload))

    async def run(self, context: BusinessAutomationContext, *, event: Mapping[str, Any],
                  rule_id: str | None, dry_run: bool, approved: bool) -> list[Any]:
        return await self._port.run(
            context.validate(), event=dict(event), rule_id=rule_id,
            dry_run=dry_run, approved=approved,
        )

    async def history(self, context: BusinessAutomationContext, limit: int = 50) -> list[Any]:
        return await self._port.history(context.validate(), limit)
