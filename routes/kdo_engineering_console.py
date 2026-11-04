"""Authenticated KDO AI Software Engineering console.

The UI is real; execution data and decisions are delegated to an explicitly
configured host provider on ``app.state.kdo_engineering_console_provider``.
This router deliberately does not fabricate tasks, agent activity, or approvals.
Without the host provider it returns 503 rather than falling back to demo state.
"""
from __future__ import annotations

import inspect
import json
import hashlib
import hmac
import os
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from api.dependencies import (
    auth_user_email_var,
    auth_workspace_id_var,
    require_auth,
    require_workspace_operator,
)

router = APIRouter(tags=["KDO Engineering Console"])
_TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "kdo_engineering_console.html"


async def _call(value: Any, *args: Any, **kwargs: Any) -> Any:
    result = value(*args, **kwargs)
    return await result if inspect.isawaitable(result) else result


def _verified_gateway_context(request: Request) -> dict[str, str] | None:
    """Verify the KCPG-signed actor/workspace envelope when supplied.

    The Python service credential authenticates the gateway-to-core channel; this
    separate HMAC binds the end-user actor and authorized workspace to that call.
    Unsigned/partial context is rejected rather than trusted from arbitrary headers.
    """
    header_names = {
        "timestamp": "x-kcpg-context-timestamp",
        "request_id": "x-kcpg-context-request-id",
        "workspace_id": "x-kcpg-context-workspace-id",
        "actor_id": "x-kcpg-context-actor-id",
        "actor_role": "x-kcpg-context-actor-role",
        "signature": "x-kcpg-context-signature",
    }
    values = {key: request.headers.get(header) for key, header in header_names.items()}
    present = [value is not None for value in values.values()]
    if not any(present):
        return None
    if not all(present):
        raise HTTPException(status_code=403, detail="incomplete_kcpg_context")
    secret = os.getenv("KCPG_CONTEXT_SIGNING_SECRET", "").strip()
    if len(secret) < 32:
        raise HTTPException(status_code=503, detail="kcpg_context_signing_not_configured")
    try:
        timestamp = int(values["timestamp"])
    except (TypeError, ValueError):
        raise HTTPException(status_code=403, detail="invalid_kcpg_context_timestamp")
    if abs(int(time.time() * 1000) - timestamp) > 300_000:
        raise HTTPException(status_code=403, detail="expired_kcpg_context")
    canonical = "|".join((values["timestamp"], values["request_id"], values["workspace_id"], values["actor_id"], values["actor_role"]))
    expected = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, values["signature"]):
        raise HTTPException(status_code=403, detail="invalid_kcpg_context_signature")
    if not values["workspace_id"].strip() or not values["actor_id"].strip() or not values["request_id"].strip():
        raise HTTPException(status_code=403, detail="invalid_kcpg_context_identity")
    return values


def _workspace_id(request: Request) -> str:
    gateway_context = _verified_gateway_context(request)
    workspace_id = gateway_context["workspace_id"] if gateway_context else auth_workspace_id_var.get()
    if not workspace_id:
        ctx = getattr(request.state, "request_context", None)
        workspace_id = getattr(ctx, "workspace_id", None) if ctx else None
    if not isinstance(workspace_id, str) or not workspace_id.strip():
        raise HTTPException(status_code=403, detail="authenticated_workspace_required")
    return workspace_id


def _actor_id(request: Request) -> str:
    gateway_context = _verified_gateway_context(request)
    if gateway_context:
        return gateway_context["actor_id"]
    return auth_user_email_var.get() or "authenticated_user"


def _provider(request: Request) -> Any:
    provider = getattr(request.app.state, "kdo_engineering_console_provider", None)
    if provider is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "engineering_runtime_not_connected",
                "message": "The engineering console UI is available, but the production task/event provider has not been wired into this application instance.",
            },
        )
    return provider


def _require_method(provider: Any, method_name: str):
    method = getattr(provider, method_name, None)
    if not callable(method):
        raise HTTPException(status_code=503, detail=f"engineering_provider_missing_{method_name}")
    return method


def _raise_provider_error(exc: Exception) -> None:
    """Translate expected persistence/workflow failures without leaking internals."""
    from services.kdo_engineering_task_repository import EngineeringTaskConflict, EngineeringTaskNotFound, EngineeringTaskScopeError
    from services.kdo_engineering_phase16_5 import Phase165Error
    from services.kdo_engineering_phase16_2 import Phase162Error
    from services.kdo_engineering_console_provider import EngineeringConsoleProviderError
    if isinstance(exc, (EngineeringTaskConflict, Phase162Error)):
        raise HTTPException(status_code=409, detail="engineering_task_revision_or_workflow_conflict") from exc
    if isinstance(exc, EngineeringTaskNotFound):
        raise HTTPException(status_code=404, detail="engineering_task_not_found") from exc
    if isinstance(exc, EngineeringTaskScopeError):
        raise HTTPException(status_code=403, detail="engineering_task_scope_rejected") from exc
    if isinstance(exc, (Phase165Error, EngineeringConsoleProviderError)):
        raise HTTPException(status_code=503, detail="engineering_persistence_or_provider_unavailable") from exc
    raise exc


@router.get("/engineering/console", response_class=HTMLResponse, include_in_schema=False)
async def engineering_console_page():
    """Serve the first-party engineering cockpit; API state is never mocked."""
    try:
        html = _TEMPLATE.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail="engineering_console_template_unavailable") from exc
    return HTMLResponse(html, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/api/v1/engineering/console/capabilities")
async def engineering_console_capabilities(
    request: Request, _auth: bool = Depends(require_auth),
):
    workspace_id = _workspace_id(request)
    provider = _provider(request)
    result = await _call(_require_method(provider, "capabilities"), workspace_id=workspace_id)
    if not isinstance(result, dict):
        raise HTTPException(status_code=502, detail="invalid_engineering_provider_response")
    # The provider reports actual runtime wiring, not UI assumptions.
    return result


@router.get("/api/v1/engineering/console/tasks")
async def engineering_console_tasks(
    request: Request, limit: int = Query(30, ge=1, le=100), _auth: bool = Depends(require_auth),
):
    workspace_id = _workspace_id(request)
    provider = _provider(request)
    result = await _call(_require_method(provider, "list_tasks"), workspace_id=workspace_id, limit=limit)
    if not isinstance(result, dict) or not isinstance(result.get("tasks"), list):
        raise HTTPException(status_code=502, detail="invalid_engineering_provider_response")
    return result


@router.post("/api/v1/engineering/console/tasks", status_code=202)
async def engineering_console_submit_task(
    request: Request, body: dict = Body(...), _auth: bool = Depends(require_auth),
):
    workspace_id = _workspace_id(request)
    objective = body.get("objective")
    criteria = body.get("acceptance_criteria", [])
    if not isinstance(objective, str) or not objective.strip() or len(objective) > 12000:
        raise HTTPException(status_code=422, detail="objective_required_max_12000_characters")
    if not isinstance(criteria, list) or len(criteria) > 30 or any(not isinstance(x, str) or not x.strip() or len(x) > 1000 for x in criteria):
        raise HTTPException(status_code=422, detail="acceptance_criteria_must_be_a_list_of_at_most_30_nonempty_strings")
    provider = _provider(request)
    try:
        result = await _call(
            _require_method(provider, "submit_task"),
            workspace_id=workspace_id,
            user_id=_actor_id(request),
            objective=objective.strip(),
            acceptance_criteria=[x.strip() for x in criteria],
            idempotency_key=body.get("idempotency_key"),
        )
    except Exception as exc:
        _raise_provider_error(exc)
    if not isinstance(result, dict) or not result.get("task_id"):
        raise HTTPException(status_code=502, detail="invalid_engineering_provider_response")
    return result


@router.get("/api/v1/engineering/console/tasks/{task_id}")
async def engineering_console_task_detail(
    task_id: str, request: Request, _auth: bool = Depends(require_auth),
):
    workspace_id = _workspace_id(request)
    provider = _provider(request)
    result = await _call(_require_method(provider, "get_task"), task_id=task_id, workspace_id=workspace_id)
    if not isinstance(result, dict):
        raise HTTPException(status_code=404, detail="engineering_task_not_found")
    return result


@router.get("/api/v1/engineering/console/tasks/{task_id}/events")
async def engineering_console_task_events(
    task_id: str, request: Request,
    after_sequence: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=500),
    _auth: bool = Depends(require_auth),
):
    workspace_id = _workspace_id(request)
    provider = _provider(request)
    result = await _call(
        _require_method(provider, "list_events"), task_id=task_id,
        workspace_id=workspace_id, after_sequence=after_sequence, limit=limit,
    )
    if not isinstance(result, dict) or not isinstance(result.get("events"), list):
        raise HTTPException(status_code=502, detail="invalid_engineering_provider_response")
    return result


@router.post("/api/v1/engineering/console/tasks/{task_id}/plan/generate")
async def engineering_console_generate_plan(
    task_id: str, request: Request, _auth: bool = Depends(require_auth),
):
    """Generate an AI plan proposal; never executes or approves the plan."""
    workspace_id = _workspace_id(request)
    provider = _provider(request)
    try:
        result = await _call(_require_method(provider, "generate_plan"),
                             task_id=task_id, workspace_id=workspace_id)
    except Exception as exc:
        _raise_provider_error(exc)
    if not isinstance(result, dict) or not isinstance(result.get("steps"), list):
        raise HTTPException(status_code=502, detail="invalid_engineering_planner_response")
    return result


@router.post("/api/v1/engineering/console/tasks/{task_id}/plan")
async def engineering_console_submit_plan(
    task_id: str, request: Request, body: dict = Body(...),
    _auth: bool = Depends(require_workspace_operator),
):
    """Persist a proposed plan before an independent operator reviews it.

    This endpoint records a plan supplied by an upstream planner/operator. It does
    not itself invoke an LLM or claim dynamic agent planning is wired.
    """
    workspace_id = _workspace_id(request)
    steps = body.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 30 or any(
        not isinstance(step, str) or not step.strip() or len(step) > 2000 for step in steps
    ):
        raise HTTPException(status_code=422, detail="steps_must_be_1_to_30_nonempty_strings_max_2000_characters")
    assignments = body.get("assignments")
    if assignments is not None and (
        not isinstance(assignments, list) or not 3 <= len(assignments) <= 12
        or any(not isinstance(item, dict) for item in assignments)
    ):
        raise HTTPException(status_code=422, detail="assignments_must_be_3_to_12_objects")
    summary = body.get("summary")
    if summary is not None and (not isinstance(summary, str) or len(summary) > 4000):
        raise HTTPException(status_code=422, detail="invalid_plan_summary")
    planner = body.get("planner")
    if planner is not None and not isinstance(planner, dict):
        raise HTTPException(status_code=422, detail="invalid_planner_evidence")
    provider = _provider(request)
    try:
        result = await _call(
            _require_method(provider, "submit_plan"), task_id=task_id,
            workspace_id=workspace_id, user_id=_actor_id(request),
            steps=[step.strip() for step in steps], assignments=assignments,
            summary=summary, planner=planner,
        )
    except Exception as exc:
        _raise_provider_error(exc)
    if not isinstance(result, dict) or not result.get("task_id"):
        raise HTTPException(status_code=502, detail="invalid_engineering_provider_response")
    return result


@router.post("/api/v1/engineering/console/tasks/{task_id}/decision")
async def engineering_console_task_decision(
    task_id: str, request: Request, body: dict = Body(...),
    _auth: bool = Depends(require_workspace_operator),
):
    workspace_id = _workspace_id(request)
    decision = body.get("decision")
    expected_revision = body.get("expected_revision")
    if decision not in {"approve", "reject"}:
        raise HTTPException(status_code=422, detail="decision_must_be_approve_or_reject")
    if not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or expected_revision < 1:
        raise HTTPException(status_code=422, detail="expected_revision_required")
    provider = _provider(request)
    try:
        result = await _call(
            _require_method(provider, "decide"), task_id=task_id, workspace_id=workspace_id,
            user_id=_actor_id(request),
            decision=decision, expected_revision=expected_revision,
        )
    except Exception as exc:
        _raise_provider_error(exc)
    if not isinstance(result, dict):
        raise HTTPException(status_code=502, detail="invalid_engineering_provider_response")
    return result
