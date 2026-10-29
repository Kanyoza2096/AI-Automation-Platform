"""V1.7 backend onboarding HTTP adapter.

Transport concerns stay here; application orchestration and persistence are
owned by the application service and its infrastructure adapter.

V1.7 (2026-10-28) — SELF-HEALING SUPABASE CLIENT:

  `_resolve_supabase_client()` now:
    1. Tries the module shim (`supabase_service.service_client or .client`)
    2. Falls back to `get_service_client_from_settings()` if the shim is
       empty — that function is idempotent and creates the client from
       settings if it wasn't initialized at boot.

  The previous version silently passed None to the adapter when the shim
  was empty, causing provision() to fail with:

      'NoneType' object has no attribute 'table'

  A diagnostic log line prints the shim state before resolving so a
  future incident of this class is immediately visible in logs.

  The adapter now raises ValueError on construction with db=None, so a
  misconfigured caller fails fast at the route boundary with a specific
  503 instead of crashing deep inside the provision method.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from application.business_onboarding import (
    BusinessOnboardingApplicationService,
    OnboardingContext,
)
from services.business_onboarding_adapter import BusinessOnboardingServiceAdapter

logger = logging.getLogger("kanyoza.routes.business_onboarding")

router = APIRouter(prefix="/api/v1/onboarding", tags=["business-onboarding"])


# ═══════════════════════════════════════════════════════════════════════════
# Auth + workspace resolution
# ═══════════════════════════════════════════════════════════════════════════


def _auth_dep():
    """Return the canonical require_auth dependency (imported lazily)."""
    from api.v1 import require_auth
    return require_auth


def _workspace_id() -> str:
    """Return the authenticated workspace, raising 403 if not scoped."""
    from core.request_context import require_request_context
    ctx = require_request_context()
    if not ctx.workspace_id:
        raise HTTPException(
            status_code=403,
            detail="Authenticated request has no workspace scope",
        )
    return str(ctx.workspace_id)


# ═══════════════════════════════════════════════════════════════════════════
# Supabase client resolution (self-healing)
# ═══════════════════════════════════════════════════════════════════════════


def _resolve_supabase_client() -> Any:
    """Resolve a Supabase client, self-healing if the shim is empty.

    Resolution order:
      1. `supabase_service.service_client` — the pooled service-role client
         (initialized by _boot_supabase when SUPABASE_SERVICE_ROLE_KEY is set)
      2. `supabase_service.client` — falls back to anon if service-role is
         not initialized
      3. `get_service_client_from_settings()` — force-initialize from settings.
         Idempotent; returns the existing singleton if already initialized,
         or creates a new one if not.

    Returns None only if every path fails (SUPABASE_URL or SERVICE_ROLE_KEY
    missing from config). The caller (adapter constructor) then raises a
    specific error instead of crashing deep inside provision().
    """
    # Diagnostic — logs once per request so shim state is visible in logs
    try:
        from services.supabase import (
            supabase_service,
            _service_client as _svc,
            _anon_client as _anon,
        )
        logger.info(
            "business_onboarding client state — "
            "_service_client=%r _anon_client=%r "
            "shim.service_client=%r shim.client=%r",
            _svc is not None,
            _anon is not None,
            supabase_service.service_client is not None,
            supabase_service.client is not None,
        )
    except Exception as exc:
        logger.debug(
            "business_onboarding: diagnostic shim read failed: %s", exc
        )

    # 1. Try the shim
    try:
        from services.supabase import supabase_service
        client = supabase_service.service_client or supabase_service.client
        if client is not None:
            return client
    except Exception as exc:
        logger.warning(
            "business_onboarding: shim read failed (%s) — attempting re-init",
            exc,
        )

    # 2. Force re-initialization — idempotent, safe to call from any context
    try:
        from services.supabase import get_service_client_from_settings
        client = get_service_client_from_settings()
        if client is not None:
            logger.warning(
                "business_onboarding: shim returned None — re-initialized "
                "Supabase client via get_service_client_from_settings()"
            )
            return client
        logger.error(
            "business_onboarding: get_service_client_from_settings() returned "
            "None — check SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY env vars"
        )
    except Exception as exc:
        logger.error(
            "business_onboarding: get_service_client_from_settings() raised: %s",
            exc,
        )

    return None


# ═══════════════════════════════════════════════════════════════════════════
# Application service factory
# ═══════════════════════════════════════════════════════════════════════════


def _application_service(request: Request) -> BusinessOnboardingApplicationService:
    """Build the application service with the resolved adapter.

    If `request.app.state.business_onboarding_application_factory` is set
    (e.g. by a test fixture or a custom wiring), it takes precedence. The
    default path constructs the adapter from the resolved Supabase client.

    The adapter now raises ValueError on db=None, which we surface as a
    specific 503 (not a mysterious runtime error in provision()).
    """
    factory = getattr(request.app.state, "business_onboarding_application_factory", None)
    if factory is not None:
        return factory()

    db = _resolve_supabase_client()
    try:
        return BusinessOnboardingApplicationService(
            BusinessOnboardingServiceAdapter(db)
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"business_onboarding_unavailable:{str(exc)[:180]}",
        ) from exc


# ═══════════════════════════════════════════════════════════════════════════
# Request models
# ═══════════════════════════════════════════════════════════════════════════


class DescribeRequest(BaseModel):
    description: str = Field(min_length=10, max_length=10000)


class ProvisionRequest(BaseModel):
    plan: dict
    activate: bool = False


# ═══════════════════════════════════════════════════════════════════════════
# Endpoints
# ═══════════════════════════════════════════════════════════════════════════


@router.post("/understand", dependencies=[Depends(_auth_dep())])
async def understand(payload: DescribeRequest, request: Request):
    """Extract a business profile from a natural-language description.

    Backend: understand_business() → AI gateway (structured_json) → BusinessProfile.
    Falls back to deterministic keyword extraction if the AI gateway is
    unavailable. Response source field tells you which path ran.
    """
    result = await _application_service(request).understand(payload.description)
    return result.__dict__


@router.post("/plan", dependencies=[Depends(_auth_dep())])
async def plan(payload: DescribeRequest, request: Request):
    """Produce an operating plan from a business description.

    Backend: understand_business() → load_playbook() → build_business_plan().
    Returns workers, connectors, content_pillars, rules, and readiness state.
    """
    try:
        return await _application_service(request).plan(
            OnboardingContext(_workspace_id()), payload.description
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/provision", dependencies=[Depends(_auth_dep())])
async def provision(payload: ProvisionRequest, request: Request):
    """Persist the plan into workspace state.

    Creates:
      - business_onboarding_plans row (idempotent — replays by plan_id)
      - brand row (with generated brand_id)
      - agent_identities rows for each worker
      - business_automation_rules rows for each rule

    Returns provisioned IDs, required_connectors, and activation_allowed.
    Activation is only allowed when all required connectors are connected.
    """
    try:
        return _application_service(request).provision(
            OnboardingContext(_workspace_id()),
            payload.plan,
            activate=payload.activate,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"business_onboarding_unavailable:{str(exc)[:180]}",
        ) from exc
