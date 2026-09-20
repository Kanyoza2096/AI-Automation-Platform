"""V72.2 canonical Command Center read-model endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

router = APIRouter(prefix="/api/v1/command-center", tags=["command-center"])


def _auth_dep():
    from api.v1 import require_auth
    return require_auth


def _workspace_id(request: Request) -> str:
    from core.request_context import require_request_context
    ctx = require_request_context()
    if not ctx.workspace_id:
        raise HTTPException(403, detail="Authenticated request has no workspace scope")
    return str(ctx.workspace_id)


def _sources(request: Request) -> dict:
    """Resolve only already-owned runtime read surfaces; never execute actions."""
    sources = {}
    runtime = getattr(request.app.state, "production_runtime", None)
    if runtime is not None:
        sources["workforce"] = runtime.status()
    wiring = getattr(request.app.state, "route_event_wiring", None)
    if wiring is not None:
        sources["governance"] = wiring.status()
    # V72.4: hydrate business truth from durable V70/V71/V66 stores.
    try:
        from core.supabase_client import get_supabase
        from services.v72_4_business_truth_adapters import build_business_truth
        truth = build_business_truth(_workspace_id(request), db=get_supabase(), runtime=runtime)
        sources.update({key: value.data for key, value in truth.items()})
        sources["_truth_sections"] = truth
    except Exception:
        # Preserve explicit UNKNOWN semantics if the durable store is unavailable.
        pass
    return sources


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
        return {
            "schema": "CommandCenterReadModel/v1", "version": "v72.4",
            "generatedAt": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            "workspaceId": workspace_id,
            "state": "LIVE" if live == len(SECTIONS) else ("DEGRADED" if live else "UNKNOWN"),
            "summary": {"sectionCount": len(SECTIONS), "live": live, "unknown": unknown},
            "sections": sections,
            "authority": {"readOnly": True, "actionAuthorityGranted": False,
                "consequentialCommandsMustUse": ["KCPG_GATEWAY", "GOVERNANCE", "ACTION_KERNEL", "DURABLE_OUTBOX", "CONNECTOR_PROVIDER", "RECONCILIATION"]},
        }
    return CommandCenterReadModel(workspace_id).build(sources)


@router.get("/intelligence/recommendations", dependencies=[Depends(_auth_dep())])
async def command_center_recommendation_ledger(request: Request, limit: int = 50):
    """V72.6 durable recommendation ledger; read-only cockpit surface."""
    workspace_id = _workspace_id(request)
    try:
        from core.supabase_client import get_supabase
        from services.v72_6_recommendation_lifecycle import SupabaseRecommendationRepository, RecommendationLifecycleService
        return RecommendationLifecycleService(SupabaseRecommendationRepository(get_supabase())).list(workspace_id, limit)
    except Exception as exc:
        raise HTTPException(503, detail=f"recommendation_ledger_unavailable:{str(exc)[:180]}")


@router.get("/intelligence", dependencies=[Depends(_auth_dep())])
async def command_center_intelligence(request: Request):
    """V72.5 evidence-backed business intelligence; never grants authority."""
    workspace_id = _workspace_id(request)
    runtime = getattr(request.app.state, "production_runtime", None)
    try:
        from core.supabase_client import get_supabase
        from services.v72_4_business_truth_adapters import build_business_truth
        from services.v72_5_business_intelligence_decision_surface import build_business_intelligence_decision_surface
        truth = build_business_truth(workspace_id, db=get_supabase(), runtime=runtime)
        return build_business_intelligence_decision_surface(workspace_id, truth)
    except Exception as exc:
        raise HTTPException(503, detail=f"business_intelligence_unavailable:{str(exc)[:180]}")

@router.get("/intelligence/learning", dependencies=[Depends(_auth_dep())])
async def command_center_business_learning(request: Request):
    """V72.8 closed-loop business learning; advisory/read-only only."""
    workspace_id = _workspace_id(request)
    try:
        from core.supabase_client import get_supabase
        from services.v72_4_business_truth_adapters import build_business_truth
        from services.v72_5_business_intelligence_decision_surface import build_business_intelligence_decision_surface
        from services.v72_7_decision_intelligence_outcome_attribution import BusinessDecisionIntelligence
        from services.v72_8_business_outcome_closed_loop_learning import BusinessOutcomeClosedLoopLearning
        db = get_supabase()
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
    except Exception as exc:
        raise HTTPException(503, detail=f"business_learning_unavailable:{str(exc)[:180]}")

@router.get("/intelligence/operating-loop", dependencies=[Depends(_auth_dep())])
async def command_center_operating_loop(request: Request):
    """V72.9 human decision workspace read model; commands remain KCPG/governed."""
    workspace_id = _workspace_id(request)
    try:
        from core.supabase_client import get_supabase
        from services.v72_6_recommendation_lifecycle import SupabaseRecommendationRepository, RecommendationLifecycleService
        from services.v72_4_business_truth_adapters import build_business_truth
        from services.v72_5_business_intelligence_decision_surface import build_business_intelligence_decision_surface
        from services.v72_8_business_outcome_closed_loop_learning import BusinessOutcomeClosedLoopLearning
        from services.v72_9_business_cockpit_operating_loop import BusinessCockpitOperatingLoop
        db = get_supabase()
        truth = build_business_truth(workspace_id, db=db, runtime=getattr(request.app.state, "production_runtime", None))
        intelligence = build_business_intelligence_decision_surface(workspace_id, truth)
        repo = SupabaseRecommendationRepository(db)
        ledger = RecommendationLifecycleService(repo).list(workspace_id, 200)
        recommendations = ledger.get("recommendations") or intelligence.get("recommendations") or []
        decisions_result = (db.table("decisions").select("*").eq("workspace_id", workspace_id).order("created_at", desc=True).limit(200).execute())
        decisions = list(getattr(decisions_result, "data", None) or [])
        learning = BusinessOutcomeClosedLoopLearning(workspace_id, []).build()
        return BusinessCockpitOperatingLoop(workspace_id, truth=truth, recommendations=recommendations,
                                            decisions=decisions, learning=learning).build()
    except Exception as exc:
        raise HTTPException(503, detail=f"business_operating_loop_unavailable:{str(exc)[:180]}")
