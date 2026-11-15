"""Content Intelligence + bounded Business Automation control plane."""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Request
from api.dependencies import create_rate_limit_dependency
from core.security.deps import require_auth_context
from core.security.deps import require_workspace_admin, require_workspace
from application.business_automation import BusinessAutomationApplicationService, BusinessAutomationContext
from application.content_intelligence import ContentIntelligenceApplicationService, ContentIntelligenceContext, AnalyzeContentCommand
from services.content_intelligence_adapter import ContentIntelligenceAdapter

router = APIRouter(
    prefix="/api/v1",
    tags=["content-intelligence", "business-automation"],
    dependencies=[Depends(require_auth_context)],
)

# Machine-to-machine ingress is intentionally mounted on a separate router.
# It authenticates with the event-source secret, not a user session.
public_router = APIRouter(
    prefix="/api/v1",
    tags=["business-automation-ingress"],
    dependencies=[create_rate_limit_dependency("business-automation-ingress", max_per_minute=120)],
)


def _business_automation_application(request: Request) -> BusinessAutomationApplicationService:
    container = getattr(request.app.state, "container", None)
    if container is not None:
        from services.business_automation import BusinessAutomationEngine
        engine = container.try_resolve(BusinessAutomationEngine)
        if engine is not None:
            return BusinessAutomationApplicationService(engine)
    raise HTTPException(status_code=503, detail="Business automation service unavailable")




# ═══════════════════════════════════════════════════════════════════════════
# CONTENT INTELLIGENCE
# ═══════════════════════════════════════════════════════════════════════════

def _content_intelligence_application(request: Request) -> ContentIntelligenceApplicationService:
    container = getattr(request.app.state, "container", None)
    if container is not None:
        from services.content_intelligence import ContentIntelligenceEngine
        engine = container.try_resolve(ContentIntelligenceEngine)
        if engine is not None:
            return ContentIntelligenceApplicationService(ContentIntelligenceAdapter(engine))
    return ContentIntelligenceApplicationService(ContentIntelligenceAdapter())


@router.post("/content-intelligence/analyze")
async def analyze_content_intelligence(body: dict, request: Request, ws: str = Depends(require_workspace)):
    service = _content_intelligence_application(request)
    try:
        result = await service.analyze(
            ContentIntelligenceContext(ws),
            AnalyzeContentCommand(
                topic=str(body.get("topic") or ""),
                context=str(body.get("context") or ""),
                max_results=body.get("max_results", 8),
                fetch_top=body.get("fetch_top", 4),
            ),
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return {"ok": True, "intelligence": result.to_dict()}


# ═══════════════════════════════════════════════════════════════════════════
# BUSINESS AUTOMATION — TEMPLATES
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/business-automation/templates")
async def list_automation_templates():
    from services.business_automation_catalog import list_templates
    return {"ok": True, "templates": list_templates()}


@router.post("/business-automation/templates/{template_id}/install")
async def install_automation_template(
    template_id: str,
    request: Request,
    body: dict | None = None,
    ws: str = Depends(require_workspace),
    _admin=Depends(require_workspace_admin),
):
    from services.business_automation_catalog import get_template
    business_automation = _business_automation_application(request)

    template = get_template(template_id)
    if template is None:
        raise HTTPException(404, "automation template not found")

    overrides = body or {}
    payload = {
        "name": str(overrides.get("name") or template["name"]),
        "enabled": bool(overrides.get("enabled", False)),
        "condition": overrides.get("condition", template["condition"]),
        "action": template["action"],
        "parameters": {
            **template["parameters"],
            **(overrides.get("parameters") or {}),
            **({"trigger_events": [template["trigger"]]} if template.get("trigger") not in {"schedule", "manual"} else {}),
        },
        "risk": template["risk"],
        "schedule_at": overrides.get("schedule_at"),
        "interval_seconds": overrides.get("interval_seconds", template.get("interval_seconds")),
    }
    try:
        rule = await business_automation.create_rule(BusinessAutomationContext(ws), payload)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return {"ok": True, "template_id": template_id, "rule": rule.__dict__}


# ═══════════════════════════════════════════════════════════════════════════
# BUSINESS AUTOMATION — RULES
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/business-automation/rules")
async def list_automation_rules(request: Request, ws: str = Depends(require_workspace)):
    service = _business_automation_application(request)
    try:
        rules = await service.list_rules(BusinessAutomationContext(ws))
    except Exception as exc:
        # Do not convert RLS/database authorization failures into an empty list.
        logger = __import__("logging").getLogger("kanyoza.api.business_automation")
        logger.exception("business automation rules read failed workspace=%s", ws)
        raise HTTPException(status_code=503, detail="Business automation rules unavailable") from exc
    return {"ok": True, "rules": [r.__dict__ for r in rules]}


@router.post("/business-automation/rules")
async def create_automation_rule(
    body: dict,
    request: Request,
    ws: str = Depends(require_workspace),
    _admin=Depends(require_workspace_admin),
):
    service = _business_automation_application(request)
    try:
        rule = await service.create_rule(BusinessAutomationContext(ws), body)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return {"ok": True, "rule": rule.__dict__}


@router.patch("/business-automation/rules/{rule_id}")
async def update_automation_rule(
    rule_id: str,
    body: dict,
    request: Request,
    ws: str = Depends(require_workspace),
    _admin=Depends(require_workspace_admin),
):
    service = _business_automation_application(request)
    # KCPG binds the path parameter from the same payload it forwards to the
    # backend. Accept that redundant identity only when it exactly matches the
    # route; never let a body-supplied ID override the authenticated path scope.
    payload = dict(body)
    supplied_rule_id = payload.pop("rule_id", None)
    if supplied_rule_id is not None and str(supplied_rule_id) != str(rule_id):
        raise HTTPException(403, "Rule ID in command body does not match the route")
    try:
        rule = await service.update_rule(BusinessAutomationContext(ws), rule_id, payload)
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    return {"ok": True, "rule": rule.__dict__}


@router.post("/business-automation/run")
async def run_automation(body: dict, request: Request, ws: str = Depends(require_workspace), _admin=None):
    service = _business_automation_application(request)
    dry_run = bool(body.get("dry_run", True))
    if not dry_run:
        from core.request_context import get_request_context
        ctx = get_request_context()
        if not ctx or not (
            ctx.species_privileges
            or ctx.role in {"owner", "admin"}
            or ctx.has_permission("admin")
        ):
            raise HTTPException(403, "Workspace administrator privileges required for live automation")
    executions = await service.run(
        BusinessAutomationContext(ws),
        event=body.get("event") or {},
        rule_id=body.get("rule_id"),
        dry_run=dry_run,
        approved=bool(body.get("approved", False)),
    )
    return {"ok": True, "executions": [x.__dict__ for x in executions]}


# ═══════════════════════════════════════════════════════════════════════════
# BUSINESS AUTOMATION — EVENT SOURCES
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/business-automation/event-sources")
async def list_business_event_sources(ws: str = Depends(require_workspace)):
    from services.business_automation_event_sources import list_sources
    try:
        return {"ok": True, "sources": await list_sources(ws)}
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))


@router.post("/business-automation/event-sources")
async def create_business_event_source(
    body: dict,
    ws: str = Depends(require_workspace),
    _admin=Depends(require_workspace_admin),
):
    from services.business_automation_event_sources import create_source
    try:
        source, secret = await create_source(
            ws,
            body.get("name"),
            body.get("provider", "generic_webhook"),
            body.get("event_types") or [],
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    return {
        "ok": True,
        "source": source,
        "webhook_secret": secret,
        "ingest_path": f"/api/v1/business-automation/event-sources/{source['source_id']}/ingest",
        "warning": "Store this secret now. It is never returned again.",
    }


@router.patch("/business-automation/event-sources/{source_id}")
async def update_business_event_source(
    source_id: str,
    body: dict,
    ws: str = Depends(require_workspace),
    _admin=Depends(require_workspace_admin),
):
    from services.business_automation_event_sources import set_active
    if "active" not in body:
        raise HTTPException(422, "active is required")
    try:
        return {"ok": True, "source": await set_active(ws, source_id, bool(body["active"]))}
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))


@public_router.post("/business-automation/event-sources/{source_id}/ingest")
async def ingest_business_event(source_id: str, request: Request):
    """Generic public event ingress authenticated by the source secret."""
    from services.business_automation_event_sources import ingest_event
    secret = request.headers.get("X-KDO-Webhook-Secret", "")
    event_id = str(request.headers.get("X-KDO-Event-ID", "")).strip()
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "valid JSON body required")
    if not isinstance(body, dict):
        raise HTTPException(400, "JSON body must be an object")
    event_name = str(body.pop("event_name", "") or body.pop("event_type", "")).strip()
    event_id = str(event_id or body.pop("event_id", "")).strip()
    if not event_id:
        import uuid
        event_id = "ext_" + uuid.uuid4().hex
    try:
        return await ingest_event(
            source_id=source_id,
            secret=secret,
            event_id=event_id,
            event_name=event_name,
            payload=body,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))


@public_router.post("/business-automation/lead-capture/{source_id}")
async def capture_website_lead(source_id: str, request: Request):
    """First-party website lead adapter.

    Accepts a small stable form schema and emits exactly one ``lead.created``
    business event. No CRM write or other side effect happens at this boundary.
    """
    from services.business_automation_event_sources import ingest_event
    secret = request.headers.get("X-KDO-Webhook-Secret", "")
    event_id = str(request.headers.get("X-KDO-Event-ID", "")).strip()
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "valid JSON body required")
    if not isinstance(body, dict):
        raise HTTPException(400, "JSON body must be an object")
    name = str(body.get("name") or body.get("full_name") or "").strip()[:200]
    if not name:
        raise HTTPException(422, "name is required")
    event_id = str(event_id or body.get("event_id") or "").strip()
    if not event_id:
        import uuid
        event_id = "lead_" + uuid.uuid4().hex
    payload = {
        "name": name,
        "phone": str(body.get("phone") or "").strip()[:50],
        "email": str(body.get("email") or "").strip()[:320],
        "company": str(body.get("company") or "").strip()[:200],
        "message": str(body.get("message") or "").strip()[:2000],
        "source": str(body.get("source") or "website").strip()[:100],
        "page_url": str(body.get("page_url") or "").strip()[:2000],
    }
    try:
        return await ingest_event(
            source_id=source_id,
            secret=secret,
            event_id=event_id,
            event_name="lead.created",
            payload=payload,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))


# ═══════════════════════════════════════════════════════════════════════════
# BUSINESS AUTOMATION — HISTORY
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/business-automation/history")
async def automation_history(request: Request, limit: int = 50, ws: str = Depends(require_workspace)):
    service = _business_automation_application(request)
    history = await service.history(BusinessAutomationContext(ws), limit)
    return {"ok": True, "history": [x.__dict__ for x in history]}
