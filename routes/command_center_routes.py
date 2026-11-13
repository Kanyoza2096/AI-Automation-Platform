"""V72.2 canonical Command Center read-model endpoint.

v72.4.1 (2026-10-11) — merged repairs:
  - _sources() no longer imports get_supabase from a nonexistent module.
    The canonical resolver is services.supabase.supabase_service.
  - Truth-hydration failures are logged, not silently swallowed.
  - Added GET /situation — compact operator view over read-model headline.
  - Added GET /diagnostics — runtime layer status report.
  - All handlers use the canonical _db() resolver.
  - Preserved every route from the previous revision:
      /read-model
      /executions/{execution_id}
      /investigations/executions/{execution_id}
      /platform-control-plane
      /intelligence/recommendations
      /intelligence
      /intelligence/learning
      /intelligence/operating-loop
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from core.request_context import RequestContext
from core.security.deps import require_platform_or_species_privileges

logger = logging.getLogger("kanyoza.routes.command_center")

router = APIRouter(prefix="/api/v1/command-center", tags=["command-center"])


# ═══════════════════════════════════════════════════════════════════════════
# Shared helpers
# ═══════════════════════════════════════════════════════════════════════════


def _auth_dep():
    from api.v1 import require_auth
    return require_auth


def _workspace_id(request: Request) -> str:
    from core.request_context import require_request_context
    ctx = require_request_context()
    if not ctx.workspace_id:
        raise HTTPException(403, detail="Authenticated request has no workspace scope")
    return str(ctx.workspace_id)


def _db():
    """Resolve the canonical service-role Supabase client.

    The previous implementation imported `get_supabase` from a
    `core.supabase_client` module that does not exist in this codebase; the
    ImportError was silently swallowed, so the read-model always fell back
    to UNKNOWN sections. This uses the shim exported by services.supabase,
    which is the canonical entry point for backend database access.
    """
    try:
        from services.supabase import supabase_service
        return supabase_service.service_client or supabase_service.client
    except Exception as exc:
        logger.warning("command_center: Supabase client unavailable: %s", exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Source composition
# ═══════════════════════════════════════════════════════════════════════════


def _sources(request: Request) -> dict:
    """Resolve only already-owned runtime read surfaces; never execute actions."""
    sources: dict[str, Any] = {}

    runtime = getattr(request.app.state, "production_runtime", None)
    if runtime is not None:
        try:
            sources["workforce"] = runtime.status()
        except Exception as exc:
            logger.warning("command_center: runtime.status() failed: %s", exc)

    wiring = getattr(request.app.state, "route_event_wiring", None)
    if wiring is not None:
        try:
            sources["governance"] = wiring.status()
        except Exception as exc:
            logger.warning("command_center: wiring.status() failed: %s", exc)

    try:
        from services.v72_4_business_truth_adapters import build_business_truth
        db = _db()
        if db is None:
            logger.warning(
                "command_center: build_business_truth skipped — no Supabase client. "
                "Sections will report UNKNOWN."
            )
            return sources
        truth = build_business_truth(
            _workspace_id(request), db=db, runtime=runtime,
        )
        sources.update({key: value.data for key, value in truth.items()})
        sources["_truth_sections"] = truth
    except Exception as exc:
        logger.exception(
            "command_center: build_business_truth failed for workspace=%s: %s",
            _workspace_id(request), exc,
        )
    return sources


# ═══════════════════════════════════════════════════════════════════════════
# Routes — read-model
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/read-model", dependencies=[Depends(_auth_dep())])
async def command_center_read_model(request: Request):
    from services.v72_2_command_center_read_model import CommandCenterReadModel
    workspace_id = _workspace_id(request)
    sources = _sources(request)
    truth_sections = sources.pop("_truth_sections", None)
    if truth_sections:
        from services.v72_2_command_center_read_model import SECTIONS
        sections = {key: value.as_dict() for key, value in truth_sections.items()}
        live = sum(1 for key in SECTIONS if sections[key]["state"] == "LIVE")
        unknown = len(SECTIONS) - live
        from core.evidence_provenance import attach_evidence_provenance
        payload = {
            "schema": "CommandCenterReadModel/v1", "version": "v72.4",
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "workspaceId": workspace_id,
            "state": "LIVE" if live == len(SECTIONS) else ("DEGRADED" if live else "UNKNOWN"),
            "summary": {"sectionCount": len(SECTIONS), "live": live, "unknown": unknown},
            "sections": sections,
            "authority": {
                "readOnly": True,
                "actionAuthorityGranted": False,
                "consequentialCommandsMustUse": [
                    "KCPG_GATEWAY", "GOVERNANCE", "ACTION_KERNEL",
                    "DURABLE_OUTBOX", "CONNECTOR_PROVIDER", "RECONCILIATION",
                ],
            },
        }
        return attach_evidence_provenance(
            payload, source="kdo.command_center.read_model",
            authority="kdo.backend.command_center", data_class="DERIVED",
        )
    from core.evidence_provenance import attach_evidence_provenance
    payload = CommandCenterReadModel(workspace_id).build(sources)
    return attach_evidence_provenance(
        payload, source="kdo.command_center.read_model",
        authority="kdo.backend.command_center", data_class="DERIVED",
    )


# ═══════════════════════════════════════════════════════════════════════════
# Routes — situation and diagnostics
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/situation", dependencies=[Depends(_auth_dep())])
async def command_center_situation(request: Request):
    """Compact operator situation summary. Read-only. Never executes."""
    workspace_id = _workspace_id(request)
    try:
        sources = _sources(request)
        truth_sections = sources.pop("_truth_sections", None)
        if truth_sections is not None:
            sections = {key: value.as_dict() for key, value in truth_sections.items()}
        else:
            from services.v72_2_command_center_read_model import CommandCenterReadModel
            sections = CommandCenterReadModel(workspace_id).build(sources).get("sections", {})

        headline_keys = (
            "businessHealth", "objectives", "opportunities", "risks",
            "governance", "workforce", "operations", "outcomes",
            "evidence", "decisions",
        )
        headline = {
            key: sections.get(key, {}).get("state", "UNKNOWN")
            for key in headline_keys
        }
        live = sum(1 for v in headline.values() if v == "LIVE")
        unknown = sum(1 for v in headline.values() if v == "UNKNOWN")
        total = len(headline)
        state = "LIVE" if live == total else ("DEGRADED" if live else "UNKNOWN")

        from core.evidence_provenance import attach_evidence_provenance
        payload = {
            "schema": "CommandCenterSituation/v1",
            "version": "v72.4",
            "workspaceId": workspace_id,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "state": state,
            "headline": headline,
            "summary": {"sourcesKnown": live, "sourcesUnknown": unknown, "sourcesTotal": total},
            "authority": {"readOnly": True, "actionAuthorityGranted": False},
        }
        return attach_evidence_provenance(
            payload, source="kdo.command_center.situation",
            authority="kdo.backend.command_center", data_class="DERIVED",
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("command_center_situation failed for workspace=%s", workspace_id)
        raise HTTPException(status_code=503, detail=f"situation_unavailable:{type(exc).__name__}") from exc


@router.get("/diagnostics", dependencies=[Depends(_auth_dep())])
async def command_center_diagnostics(request: Request):
    """Read-only runtime diagnostics. Never executes, never grants authority."""
    workspace_id = _workspace_id(request)
    runtime = getattr(request.app.state, "production_runtime", None)
    if runtime is None:
        from core.evidence_provenance import attach_evidence_provenance
        payload = {
            "schema": "CommandCenterDiagnostics/v1",
            "version": "v72.4",
            "workspaceId": workspace_id,
            "state": "UNKNOWN",
            "reason": "production_runtime_not_installed",
            "authority": {"readOnly": True, "actionAuthorityGranted": False},
        }
        return attach_evidence_provenance(
            payload, source="kdo.command_center.diagnostics",
            authority="kdo.backend.command_center", data_class="DERIVED",
        )
    try:
        status = runtime.status()
        layers = status.get("layers", []) or []
        wired_states = {"wired", "fenced-legacy"}
        wired = sum(1 for layer in layers if layer.get("status") in wired_states)
        from core.evidence_provenance import attach_evidence_provenance
        payload = {
            "schema": "CommandCenterDiagnostics/v1",
            "version": status.get("version", "v71.13"),
            "workspaceId": workspace_id,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "state": "LIVE" if status.get("boot_verified") else "DEGRADED",
            "boot_verified": bool(status.get("boot_verified", False)),
            "layers": layers,
            "summary": {"layersTotal": len(layers), "layersWired": wired},
            "authority": {"readOnly": True, "actionAuthorityGranted": False},
        }
        return attach_evidence_provenance(
            payload, source="kdo.command_center.diagnostics",
            authority="kdo.backend.command_center", data_class="DERIVED",
        )
    except Exception as exc:
        logger.exception("command_center_diagnostics failed for workspace=%s", workspace_id)
        raise HTTPException(status_code=503, detail=f"diagnostics_unavailable:{type(exc).__name__}") from exc


# ═══════════════════════════════════════════════════════════════════════════
# Routes — execution and investigation
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/executions/{execution_id}", dependencies=[Depends(_auth_dep())])
async def command_center_execution_detail(execution_id: str, request: Request):
    """Canonical read-only Intent -> Execution -> Receipt -> Outcome -> Verification trace."""
    if not execution_id.strip():
        raise HTTPException(400, detail="execution_id is required")
    try:
        from services.vnext_execution_detail_read_model import build_execution_detail_read_model
        db = _db()
        if db is None:
            raise HTTPException(503, detail="execution_detail_unavailable:supabase_client_missing")
        workspace_id = _workspace_id(request)
        result = db.table("external_execution_ledger").select("*").eq("execution_id", execution_id).limit(1).execute()
        execution = (getattr(result, "data", None) or [None])[0]
        if not execution:
            raise HTTPException(404, detail="Execution not found")
        if str(execution.get("workspace_id")) != str(workspace_id):
            raise HTTPException(404, detail="Execution not found")
        result = db.table("action_outbox").select("*").eq("workspace_id", workspace_id).eq("execution_id", execution_id).order("created_at", desc=True).limit(1).execute()
        intent = (getattr(result, "data", None) or [None])[0]
        governance = None
        decision_id = intent.get("decision_id") if intent else None
        if decision_id:
            result = db.table("decisions").select("*").eq("workspace_id", workspace_id).eq("decision_id", decision_id).limit(1).execute()
            governance = (getattr(result, "data", None) or [None])[0]
        result = db.table("business_outcome_observations").select("*").eq("workspace_id", workspace_id).eq("execution_id", execution_id).order("observed_at", desc=True).limit(100).execute()
        observations = getattr(result, "data", None) or []
        result = db.table("business_outcome_attributions").select("*").eq("workspace_id", workspace_id).eq("execution_id", execution_id).order("created_at", desc=True).limit(100).execute()
        attributions = getattr(result, "data", None) or []
        return build_execution_detail_read_model(
            workspace_id=workspace_id, execution=execution, intent=intent,
            governance=governance, observations=observations, attributions=attributions,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, detail=f"execution_detail_unavailable:{type(exc).__name__}") from exc


@router.get("/investigations/executions/{execution_id}", dependencies=[Depends(_auth_dep())])
async def command_center_operator_investigation(execution_id: str, request: Request, limit: int = 100):
    """Canonical read-only operator investigation projection for one execution.

    This joins only backend-owned evidence. Correlation is association only;
    the endpoint never infers root cause and never mutates execution state.
    """
    if not execution_id.strip():
        raise HTTPException(400, detail="execution_id is required")
    workspace_id = _workspace_id(request)
    try:
        from services.vnext_operator_investigation_read_model import build_operator_investigation_read_model
        from services.execution_operational_state import project_execution
        db = _db()
        if db is None:
            raise HTTPException(503, detail="operator_investigation_unavailable:supabase_client_missing")
        result = db.table("external_execution_ledger").select("*").eq("workspace_id", workspace_id).eq("execution_id", execution_id).limit(1).execute()
        execution = (getattr(result, "data", None) or [None])[0]
        if not execution:
            raise HTTPException(404, detail="Execution not found")
        result = db.table("action_outbox").select("*").eq("workspace_id", workspace_id).eq("execution_id", execution_id).order("created_at", desc=True).limit(1).execute()
        intent = (getattr(result, "data", None) or [None])[0]
        governance = None
        decision_id = intent.get("decision_id") if intent else None
        if decision_id:
            result = db.table("decisions").select("*").eq("workspace_id", workspace_id).eq("decision_id", decision_id).limit(1).execute()
            governance = (getattr(result, "data", None) or [None])[0]
        result = db.table("business_outcome_observations").select("*").eq("workspace_id", workspace_id).eq("execution_id", execution_id).order("observed_at", desc=True).limit(min(max(limit, 1), 500)).execute()
        observations = getattr(result, "data", None) or []
        result = db.table("business_outcome_attributions").select("*").eq("workspace_id", workspace_id).eq("execution_id", execution_id).order("created_at", desc=True).limit(min(max(limit, 1), 500)).execute()
        attributions = getattr(result, "data", None) or []
        incident_signal = None
        try:
            operational = project_execution(type("Ledger", (), execution)())
            incident_signal = operational.incident_signal()
        except Exception:
            incident_signal = None
        events = []
        try:
            bus = getattr(request.app.state, "event_bus", None)
            if bus is not None and hasattr(bus, "replay_events"):
                events = bus.replay_events(limit=min(max(limit, 1), 500)) or []
            else:
                from core.unified_bus import get_unified_event_bus
                bus = get_unified_event_bus()
                events = bus.replay_events(limit=min(max(limit, 1), 500)) or []
        except Exception:
            events = []
        return build_operator_investigation_read_model(
            workspace_id=workspace_id, execution=execution, intent=intent,
            governance=governance, observations=observations, attributions=attributions,
            events=events, incident_signal=incident_signal,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, detail=f"operator_investigation_unavailable:{type(exc).__name__}") from exc


@router.get("/platform-authority-check")
async def platform_authority_check(
    ctx: RequestContext = Depends(require_platform_or_species_privileges),
):
    """Read-only proof that the canonical request context passed platform authorization.

    Returns no user identifiers, workspace data, credentials, or operational state.
    Machine species principals are reported distinctly from human super-admins.
    """
    is_human_super_admin = (
        ctx.principal_type == "human"
        and ctx.auth_method == "supabase_jwt"
        and ctx.platform_role == "super_admin"
        and not ctx.species_privileges
        and ctx.has_permission("platform:control_plane:access")
    )
    return {
        "authorized": True,
        "authority_type": "human_platform_super_admin" if is_human_super_admin else "machine_species",
        "principal_type": ctx.principal_type,
        "auth_method": ctx.auth_method,
        "platform_role": ctx.platform_role or None,
        "species_privileges": bool(ctx.species_privileges),
        "permission": "platform:control_plane:access",
    }


@router.get("/platform-control-plane", dependencies=[Depends(_auth_dep())])
async def platform_control_plane_read_model(request: Request):
    """Canonical vNext platform control-plane projection for the operator UI.

    Read-only. The projection composes backend-owned sources and never grants
    execution authority or calls provider mutation paths.
    """
    workspace_id = _workspace_id(request)
    try:
        from services.vnext_platform_control_plane_read_model import build_platform_control_plane_read_model
        db = _db()
        if db is None:
            raise HTTPException(503, detail="platform_control_plane_unavailable:supabase_client_missing")
        return build_platform_control_plane_read_model(
            workspace_id,
            db=db,
            runtime=getattr(request.app.state, "production_runtime", None),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, detail=f"platform_control_plane_unavailable:{type(exc).__name__}")


# ═══════════════════════════════════════════════════════════════════════════
# Routes — intelligence
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/intelligence/recommendations", dependencies=[Depends(_auth_dep())])
async def command_center_recommendation_ledger(request: Request, limit: int = 50):
    """V72.6 durable recommendation ledger; read-only cockpit surface."""
    workspace_id = _workspace_id(request)
    try:
        from services.v72_6_recommendation_lifecycle import SupabaseRecommendationRepository, RecommendationLifecycleService
        db = _db()
        if db is None:
            raise HTTPException(503, detail="recommendation_ledger_unavailable:supabase_client_missing")
        return RecommendationLifecycleService(SupabaseRecommendationRepository(db)).list(workspace_id, limit)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, detail=f"recommendation_ledger_unavailable:{str(exc)[:180]}")


@router.get("/intelligence", dependencies=[Depends(_auth_dep())])
async def command_center_intelligence(request: Request):
    """V72.5 evidence-backed business intelligence; never grants authority."""
    workspace_id = _workspace_id(request)
    runtime = getattr(request.app.state, "production_runtime", None)
    try:
        from services.v72_4_business_truth_adapters import build_business_truth
        from services.v72_5_business_intelligence_decision_surface import build_business_intelligence_decision_surface
        db = _db()
        if db is None:
            raise HTTPException(503, detail="business_intelligence_unavailable:supabase_client_missing")
        truth = build_business_truth(workspace_id, db=db, runtime=runtime)
        return build_business_intelligence_decision_surface(workspace_id, truth)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, detail=f"business_intelligence_unavailable:{str(exc)[:180]}")


@router.get("/intelligence/learning", dependencies=[Depends(_auth_dep())])
async def command_center_business_learning(request: Request):
    """V72.8 closed-loop business learning; advisory/read-only only."""
    workspace_id = _workspace_id(request)
    try:
        from services.v72_4_business_truth_adapters import build_business_truth
        from services.v72_5_business_intelligence_decision_surface import build_business_intelligence_decision_surface
        from services.v72_7_decision_intelligence_outcome_attribution import BusinessDecisionIntelligence
        from services.v72_8_business_outcome_closed_loop_learning import BusinessOutcomeClosedLoopLearning
        db = _db()
        if db is None:
            raise HTTPException(503, detail="business_learning_unavailable:supabase_client_missing")
        truth = build_business_truth(workspace_id, db=db, runtime=getattr(request.app.state, "production_runtime", None))
        intelligence = build_business_intelligence_decision_surface(workspace_id, truth)
        recs = intelligence.get("recommendations", [])
        # V72.7 accepts durable outcome-attribution-shaped evidence; this read surface
        # remains advisory and never materializes or executes side effects.
        attributed = BusinessDecisionIntelligence(workspace_id, recs, [], []).build()
        rows = []
        for rec in attributed.get("recommendations", []):
            for attr in rec.get("outcomeAttributions", []):
                rows.append(attr)
        return BusinessOutcomeClosedLoopLearning(workspace_id, rows).build()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, detail=f"business_learning_unavailable:{str(exc)[:180]}")


@router.get("/intelligence/operating-loop", dependencies=[Depends(_auth_dep())])
async def command_center_operating_loop(request: Request):
    """V72.9 human decision workspace read model; commands remain KCPG/governed."""
    workspace_id = _workspace_id(request)
    try:
        from services.v72_6_recommendation_lifecycle import SupabaseRecommendationRepository, RecommendationLifecycleService
        from services.v72_4_business_truth_adapters import build_business_truth
        from services.v72_5_business_intelligence_decision_surface import build_business_intelligence_decision_surface
        from services.v72_8_business_outcome_closed_loop_learning import BusinessOutcomeClosedLoopLearning
        from services.v72_9_business_cockpit_operating_loop import BusinessCockpitOperatingLoop
        db = _db()
        if db is None:
            raise HTTPException(503, detail="business_operating_loop_unavailable:supabase_client_missing")
        truth = build_business_truth(workspace_id, db=db, runtime=getattr(request.app.state, "production_runtime", None))
        intelligence = build_business_intelligence_decision_surface(workspace_id, truth)
        repo = SupabaseRecommendationRepository(db)
        ledger = RecommendationLifecycleService(repo).list(workspace_id, 200)
        recommendations = ledger.get("recommendations") or intelligence.get("recommendations") or []
        decisions_result = (
            db.table("decisions")
            .select("*")
            .eq("workspace_id", workspace_id)
            .order("created_at", desc=True)
            .limit(200)
            .execute()
        )
        decisions = list(getattr(decisions_result, "data", None) or [])
        learning = BusinessOutcomeClosedLoopLearning(workspace_id, []).build()
        return BusinessCockpitOperatingLoop(
            workspace_id, truth=truth, recommendations=recommendations,
            decisions=decisions, learning=learning,
        ).build()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, detail=f"business_operating_loop_unavailable:{str(exc)[:180]}")
