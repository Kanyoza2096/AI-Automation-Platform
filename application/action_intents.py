"""Governed action-intent application use cases.

This module is intentionally framework- and provider-agnostic. HTTP adapters
translate transport requests into these use cases; the Action Kernel remains
behind an application port so the application layer does not construct or
persist infrastructure directly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Protocol


class ActionExecutionPort(Protocol):
    async def enqueue(self, **kwargs: Any) -> dict[str, Any]: ...

    async def approve(self, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True)
class OperatorProvenance:
    user_id: str
    role: str
    workspace_id: str
    authority_source: str = "kcpg"

    def validate(self, requested_workspace_id: str) -> None:
        if not self.user_id.strip() or not self.workspace_id.strip():
            raise ValueError("operator provenance requires user and workspace")
        if self.workspace_id != requested_workspace_id:
            raise PermissionError("workspace provenance mismatch")


@dataclass(frozen=True)
class EnqueueActionCommand:
    workspace_id: str
    action_type: str
    parameters: Mapping[str, Any]
    risk_level: str
    decision_id: Optional[str]
    correlation_id: Optional[str]
    idempotency_key: str
    source: str
    operator: OperatorProvenance


@dataclass(frozen=True)
class ApproveActionCommand:
    workspace_id: str
    intent_id: str
    approver: OperatorProvenance


class ActionIntentApplicationService:
    """Application orchestration for governed action intents."""

    ALLOWED_APPROVER_ROLES = frozenset({"operator", "admin", "owner", "species"})

    def __init__(self, action_port: ActionExecutionPort):
        self._action_port = action_port

    async def enqueue(self, command: EnqueueActionCommand) -> dict[str, Any]:
        command.operator.validate(command.workspace_id)
        result = await self._action_port.enqueue(
            workspace_id=command.workspace_id,
            action_type=command.action_type,
            parameters=dict(command.parameters),
            risk_level=command.risk_level,
            decision_id=command.decision_id,
            correlation_id=command.correlation_id,
            idempotency_key=command.idempotency_key,
            source=f"kcpg:{command.source}",
        )
        result["operator"] = {
            "user_id": command.operator.user_id,
            "role": command.operator.role,
        }
        result["authority_source"] = command.operator.authority_source
        result["accepted_without_inline_side_effect"] = True
        return result

    async def approve(self, command: ApproveActionCommand) -> dict[str, Any]:
        command.approver.validate(command.workspace_id)
        role = command.approver.role.strip().lower()
        if role not in self.ALLOWED_APPROVER_ROLES:
            raise PermissionError("operator authorization required for action approval")
        result = await self._action_port.approve(
            workspace_id=command.workspace_id,
            intent_id=command.intent_id,
            approver_id=command.approver.user_id,
            approver_role=role,
            approval_source=command.approver.authority_source,
        )
        result["authority_source"] = command.approver.authority_source
        return result
