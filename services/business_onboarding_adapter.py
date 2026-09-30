"""Infrastructure adapter for the business-onboarding application port."""
from __future__ import annotations

from typing import Any

from services.business_onboarding import BusinessOnboardingKernel, understand_business


class BusinessOnboardingServiceAdapter:
    """Adapts the existing onboarding kernel to the application port."""

    def __init__(self, db: Any):
        self._kernel = BusinessOnboardingKernel(db)

    async def understand(self, description: str):
        return await understand_business(description)

    async def plan(self, workspace_id: str, description: str) -> dict[str, Any]:
        return await self._kernel.plan(workspace_id, description)

    def provision(self, workspace_id: str, plan: dict[str, Any], *, activate: bool = False) -> dict[str, Any]:
        return self._kernel.provision(workspace_id, plan, activate=activate)
