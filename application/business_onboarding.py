"""Application use cases for business onboarding.

The HTTP layer supplies authenticated workspace context; infrastructure and
persistence remain behind an application port. This keeps onboarding policy
separate from FastAPI and database concerns.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol


class BusinessOnboardingPort(Protocol):
    async def understand(self, description: str) -> Any: ...
    async def plan(self, workspace_id: str, description: str) -> dict[str, Any]: ...
    def provision(self, workspace_id: str, plan: Mapping[str, Any], *, activate: bool = False) -> dict[str, Any]: ...


@dataclass(frozen=True)
class OnboardingContext:
    workspace_id: str

    def validate(self) -> str:
        workspace = self.workspace_id.strip()
        if not workspace:
            raise ValueError("authenticated workspace scope required")
        return workspace


class BusinessOnboardingApplicationService:
    """Application orchestration for business onboarding."""

    def __init__(self, onboarding_port: BusinessOnboardingPort):
        self._port = onboarding_port

    async def understand(self, description: str) -> Any:
        return await self._port.understand(description)

    async def plan(self, context: OnboardingContext, description: str) -> dict[str, Any]:
        workspace_id = context.validate()
        return await self._port.plan(workspace_id, description)

    def provision(
        self,
        context: OnboardingContext,
        plan: Mapping[str, Any],
        *,
        activate: bool = False,
    ) -> dict[str, Any]:
        workspace_id = context.validate()
        return self._port.provision(workspace_id, dict(plan), activate=activate)
