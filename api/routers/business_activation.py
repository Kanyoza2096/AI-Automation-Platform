from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from api.dependencies import auth_user_email_var, require_auth, require_workspace
from api.v1_helpers import _ok, _safe_err
from core.unified_bus import UnifiedEventBus
from domain.business_activation.facts import (
    BusinessAgentAnswerRequest, BusinessKnowledgeReadQuery, ConfirmFactsRequest, CurrentFactsQuery, ExtractionRequest, ReviewReextractRequest,
)
from repositories.business_activation_knowledge_repository import BusinessActivationKnowledgeRepository
from services.business_activation_extraction import extract_business_facts, reextract_review
from services.business_activation_knowledge import BusinessActivationKnowledgeService

router = APIRouter()


def _service(request: Request) -> BusinessActivationKnowledgeService:
    from services.supabase import supabase_service
    db = getattr(supabase_service, "service_client", None) or getattr(supabase_service, "client", None)
    bus = getattr(getattr(request.app, "state", None), "container", None)
    event_bus = None
    if bus is not None:
        try:
            event_bus = bus.try_resolve(UnifiedEventBus)
        except Exception:
            event_bus = None
    return BusinessActivationKnowledgeService(BusinessActivationKnowledgeRepository(db), event_bus)


@router.post("/business-activation/extractions")
async def extract_business_activation_facts(
    body: ExtractionRequest,
    request: Request,
    _auth: bool = Depends(require_auth),
    authenticated_workspace: str = Depends(require_workspace),
):
    """Extract and audit a proposal; extraction never confirms a fact."""
    try:
        if body.workspace_id != authenticated_workspace:
            raise HTTPException(status_code=403, detail="workspace_id does not match authenticated workspace")
        result = extract_business_facts(body)
        _service(request).record_extraction(result, body.operator_text, body.workspace_id, [item.value for item in body.fact_types_in_scope])
        return _ok(result.model_dump(mode="json"), 200)
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/business-activation/extractions")


@router.post("/business-activation/review/re-extract")
async def reextract_business_activation_review(
    body: ReviewReextractRequest,
    request: Request,
    _auth: bool = Depends(require_auth),
    authenticated_workspace: str = Depends(require_workspace),
):
    """Re-extract one fact type and audit the proposal; no knowledge is confirmed."""
    try:
        if body.workspace_id != authenticated_workspace:
            raise HTTPException(status_code=403, detail="workspace_id does not match authenticated workspace")
        result = reextract_review(body)
        _service(request).record_extraction(result, f"{body.operator_text.strip()}\n{body.correction_text.strip()}", body.workspace_id, [body.fact_type.value])
        return _ok(result.model_dump(mode="json"), 200)
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/business-activation/review/re-extract")


@router.post("/business-activation/confirm")
async def confirm_business_activation_facts(
    body: ConfirmFactsRequest,
    request: Request,
    _auth: bool = Depends(require_auth),
    authenticated_workspace: str = Depends(require_workspace),
):
    """Durably confirm reviewed facts into the Knowledge Plane."""
    try:
        if body.workspace_id != authenticated_workspace:
            raise HTTPException(status_code=403, detail="workspace_id does not match authenticated workspace")
        service = _service(request)
        facts, event_id, replay = await service.confirm(body.workspace_id, body.extraction_id, body.proposed_fact_ids, auth_user_email_var.get() or "authenticated-principal")
        return _ok({
            "confirmed": True,
            "idempotent_replay": replay,
            "facts": [f.model_dump(mode="json") for f in facts],
            "knowledge_updated_event_id": event_id,
        }, 200)
    except HTTPException:
        raise
    except ValueError as exc:
        code = str(exc)
        status = 404 if code in {"extraction_not_found", "proposed_fact_not_found"} else 409
        return _safe_err(exc, f"POST /api/v1/business-activation/confirm:{code}")
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/business-activation/confirm")


@router.post("/business-activation/agent/answer")
async def answer_business_activation_question(
    body: BusinessAgentAnswerRequest,
    request: Request,
    _auth: bool = Depends(require_auth),
    authenticated_workspace: str = Depends(require_workspace),
):
    """Produce a deterministic answer grounded only in confirmed current Knowledge Plane facts.

    This is a runtime consumption boundary, not an execution authority. Missing or
    ambiguous knowledge is a first-class refusal/handoff state; no model inference
    is used to fill the gap.
    """
    try:
        if body.workspace_id != authenticated_workspace:
            raise HTTPException(status_code=403, detail="workspace_id does not match authenticated workspace")
        result = _service(request).answer(body)
        return _ok(result.model_dump(mode="json"), 200)
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/business-activation/agent/answer")


@router.post("/business-activation/knowledge/read")
async def read_business_activation_knowledge(
    body: BusinessKnowledgeReadQuery,
    request: Request,
    _auth: bool = Depends(require_auth),
    authenticated_workspace: str = Depends(require_workspace),
):
    """Runtime-safe read of confirmed current Knowledge Plane facts.

    No result is a first-class MISSING_KNOWLEDGE state. Callers must refuse or
    hand off rather than infer from absence, stale UI state, or unconfirmed proposals.
    """
    try:
        if body.workspace_id != authenticated_workspace:
            raise HTTPException(status_code=403, detail="workspace_id does not match authenticated workspace")
        result = _service(request).runtime_read(body)
        return _ok(result.model_dump(mode="json"), 200)
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/business-activation/knowledge/read")


@router.post("/business-activation/facts/current")
async def current_business_activation_facts(
    body: CurrentFactsQuery,
    request: Request,
    _auth: bool = Depends(require_auth),
    authenticated_workspace: str = Depends(require_workspace),
):
    """Read only confirmed, non-superseded Business Activation knowledge by default."""
    try:
        if body.workspace_id != authenticated_workspace:
            raise HTTPException(status_code=403, detail="workspace_id does not match authenticated workspace")
        facts = _service(request).current(body)
        return _ok({
            "facts": [f.model_dump(mode="json") for f in facts],
            "count": len(facts),
            "source": "knowledge_plane",
        }, 200)
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/business-activation/facts/current")
