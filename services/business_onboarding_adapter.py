"""Infrastructure adapter for the business-onboarding application port.

V1.6 (2026-10-28) — FAIL-FAST ON MISSING DB CLIENT:

  The adapter now rejects `db=None` at construction. The previous version
  accepted None silently, causing a runtime failure at the first
  `.table(...)` call inside `provision()` with:

      'NoneType' object has no attribute 'table'

  That message misattributed the cause to the provision method. The real
  cause was upstream — the caller passed None because the Supabase shim
  was empty (see routes/business_onboarding_routes.py V1.7).

  Failing at construction turns a slow, misattributed failure into a
  specific, actionable one at the HTTP boundary (503 with reason).
"""
from __future__ import annotations

from typing import Any

from services.business_onboarding import BusinessOnboardingKernel, understand_business


class BusinessOnboardingServiceAdapter:
    """Adapts the existing onboarding kernel to the application port."""

    def __init__(self, db: Any):
        if db is None:
            raise ValueError(
                "BusinessOnboardingServiceAdapter requires a non-None db client. "
                "The caller must supply the pooled Supabase client from "
                "services.supabase. Check that SUPABASE_URL and "
                "SUPABASE_SERVICE_ROLE_KEY (or SUPABASE_ANON_KEY) are set, "
                "and that _boot_supabase ran successfully. This is a "
                "caller-side bug (shim initialization failure or missing "
                "env config), not an adapter-side bug."
            )
        self._kernel = BusinessOnboardingKernel(db)

    async def understand(self, description: str):
        """Extract a business profile via the internal LLM gateway.

        Delegates to the module-level understand_business(), which calls
        get_llm_gateway().complete_async_structured() with capability=
        structured_json. Falls back to deterministic keyword extraction
        if the gateway is unavailable.
        """
        return await understand_business(description)

    async def plan(self, workspace_id: str, description: str) -> dict[str, Any]:
        """Produce an operating plan from a description.

        The kernel calls understand_business() to extract the profile,
        then loads the playbook and builds the plan. The db client is not
        used here — plans are deterministic from the profile + playbook.
        """
        return await self._kernel.plan(workspace_id, description)

    def provision(
        self,
        workspace_id: str,
        plan: dict[str, Any],
        *,
        activate: bool = False,
    ) -> dict[str, Any]:
        """Persist the plan into workspace state.

        Idempotent — replays by plan_id. Creates the brand row, agent
        identities, and automation rules. Returns the provisioned IDs
        and activation gate state.
        """
        return self._kernel.provision(workspace_id, plan, activate=activate)
