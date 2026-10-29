"""
Rule Engine API Routes — v2.0
REST endpoints for the Autonomous Condition Engine.

Endpoints:
    GET    /rules              — List all rules
    POST   /rules              — Create a new rule
    PUT    /rules/:id          — Update an existing rule
    DELETE /rules/:id          — Delete a rule
    POST   /rules/:id/toggle   — Toggle rule active/paused
    POST   /rules/:id/test     — Dry-run a single rule
    POST   /rules/test         — Test all rules against context
    GET    /rules/history      — Execution history
    GET    /rules/metrics      — Engine telemetry
    GET    /rules/health       — Engine health status
    GET    /rules/events       — Recent processed events
    POST   /rules/events/simulate — Simulate event against all rules
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

logger = logging.getLogger("kanyoza.routes.rules")

from core.security.deps import require_species_privileges

router = APIRouter(prefix="/api/v1/rules", tags=["rules"], dependencies=[Depends(require_species_privileges)])


# ═══════════════════════════════════════════════════════════════════════════
# MODELS
# ═══════════════════════════════════════════════════════════════════════════

class ConditionModel(BaseModel):
    field: str
    op: str
    value: Any = None


class ConditionGroupModel(BaseModel):
    all: Optional[List[ConditionModel]] = None
    any: Optional[List[ConditionModel]] = None


class RuleActionModel(BaseModel):
    action: str
    value: Any = None
    params: Optional[Dict[str, Any]] = None


class RuleModel(BaseModel):
    id: str
    name: str
    description: str = ""
    priority: str = "medium"
    status: str = "active"
    when: ConditionGroupModel
    then: List[RuleActionModel]
    cooldown_seconds: int = 0
    max_firings_per_hour: Optional[int] = None
    max_firings_per_day: Optional[int] = None
    tags: Optional[List[str]] = None
    firing_count: Optional[int] = 0
    success_count: Optional[int] = 0
    failure_count: Optional[int] = 0
    last_fired_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ToggleRequest(BaseModel):
    status: str


class TestRequest(BaseModel):
    context: Dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = True


class SimulateRequest(BaseModel):
    event: str
    context: Dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = True


# ═══════════════════════════════════════════════════════════════════════════
# APPLICATION BOUNDARY
# ═══════════════════════════════════════════════════════════════════════════

from application.rules import (
    ConditionEngineAdapter,
    InMemoryRuleStore,
    RuleManagementApplicationService,
    RuleNotFoundError,
)

_rule_store = InMemoryRuleStore()
# Compatibility aliases for legacy code/tests that inspect the stores directly.
_rules_store = _rule_store.rules
_firing_history = _rule_store.firing_history
_event_log = _rule_store.event_log


def _get_engine():
    """Get the condition engine singleton if available."""
    try:
        from services.condition_engine import autonomous_engine
        return autonomous_engine
    except ImportError:
        return None


def _get_application_service() -> RuleManagementApplicationService:
    return RuleManagementApplicationService(
        store=_rule_store,
        engine=ConditionEngineAdapter(_get_engine),
    )


# ═══════════════════════════════════════════════════════════════════════════
# ROUTES — HTTP translation only
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/")
async def list_rules():
    return _get_application_service().list_rules()


@router.post("/")
async def create_rule(rule: RuleModel):
    rule_data = rule.model_dump()
    result = _get_application_service().create(rule_data)
    logger.info("Rule created: %s (%s)", result["rule"]["name"], result["rule"]["id"])
    return result


@router.put("/{rule_id}")
async def update_rule(rule_id: str, rule: RuleModel):
    result = _get_application_service().update(rule_id, rule.model_dump())
    logger.info("Rule updated: %s (%s)", result["rule"]["name"], rule_id)
    return result


@router.delete("/{rule_id}")
async def delete_rule(rule_id: str):
    result = _get_application_service().delete(rule_id)
    logger.info("Rule deleted: %s", rule_id)
    return result


@router.post("/{rule_id}/toggle")
async def toggle_rule(rule_id: str, request: ToggleRequest):
    try:
        result = _get_application_service().toggle(rule_id, request.status)
    except RuleNotFoundError:
        raise HTTPException(status_code=404, detail="Rule not found")
    logger.info("Rule toggled: %s → %s", rule_id, request.status)
    return result


@router.post("/{rule_id}/test")
async def test_rule(rule_id: str, request: TestRequest):
    try:
        return _get_application_service().test(rule_id, request.context)
    except RuleNotFoundError:
        raise HTTPException(status_code=404, detail="Rule not found")


@router.post("/test")
async def test_all_rules(request: TestRequest):
    return _get_application_service().test_all(request.context)


@router.get("/history")
async def get_history(limit: int = Query(50, ge=1, le=200)):
    return _get_application_service().history(limit)


@router.get("/metrics")
async def get_metrics():
    return _get_application_service().metrics()


@router.get("/health")
async def get_health():
    return _get_application_service().health()


@router.get("/events")
async def get_events():
    return {"events": _event_log[-50:]}


def _is_within_24h(value: str) -> bool:
    # Legacy helper retained for compatibility with callers outside the route.
    from application.rules import _within_24h
    return _within_24h(value)
