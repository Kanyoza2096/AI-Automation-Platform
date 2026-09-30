"""
Kanyoza Council API Routes v1.0
================================

Complete REST + WebSocket API for the Democracy Engine.

ENDPOINTS:
  POST   /api/v1/council/decisions          — Submit action for deliberation
  GET    /api/v1/council/decisions/{id}     — Get decision status
  GET    /api/v1/council/decisions/{id}/votes — Get votes
  GET    /api/v1/council/decisions/{id}/risk  — Get risk assessment
  GET    /api/v1/council/decisions/{id}/dissent — Get dissent opinions
  WS     /api/v1/council/decisions/{id}/stream — Live deliberation stream
  GET    /api/v1/council/status             — Council health
  GET    /api/v1/council/statistics         — Council statistics
  GET    /api/v1/council/members            — List members
  GET    /api/v1/council/history            — Decision history
  POST   /api/v1/council/outcome            — Record real-world outcome
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from council import CouncilOrchestrator
from council.models import Proposal, DecisionState, RiskLevel

logger = logging.getLogger("kanyoza.council.routes")

router = APIRouter(prefix="/api/v1/council", tags=["council"])

# Global Council instance
council = CouncilOrchestrator()

# In-memory decision store (persist to Supabase in production)
_decision_store: Dict[str, Dict[str, Any]] = {}
_decision_lock = asyncio.Lock()


# ═══════════════════════════════════════════════════════════════════════════
# AUTHENTICATION
# ═══════════════════════════════════════════════════════════════════════════

def _require_auth(request: Request) -> bool:
    """Authenticate API requests."""
    from config.settings import settings
    
    if getattr(settings, "disable_council_auth", False):
        return True
    
    token = (
        request.headers.get("Authorization", "").replace("Bearer ", "").strip()
        or request.headers.get("X-API-Key", "").strip()
    )
    
    if not token:
        raise HTTPException(401, "Missing authentication token")
    
    master_token = getattr(settings, "master_api_token", "")
    if not master_token:
        # Phase 0: fail closed — missing master token is not "open access"
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="MASTER_API_TOKEN not configured")
    
    if not hmac.compare_digest(token.encode(), master_token.encode()):
        raise HTTPException(401, "Unauthorized")
    
    return True


# ═══════════════════════════════════════════════════════════════════════════
# PYDANTIC SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════

class DecisionRequest(BaseModel):
    """Request to submit an action for deliberation."""
    action_name: str = Field(min_length=1, max_length=100)
    action_params: Dict[str, Any] = Field(default_factory=dict)
    source_engine: str = Field(default="api", max_length=50)
    description: str = Field(min_length=1, max_length=500)
    proposal_type: str = Field(default="general", max_length=50)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class OutcomeRequest(BaseModel):
    """Request to record real-world outcome."""
    decision_id: str = Field(min_length=1)
    actual_outcome: str = Field(min_length=1, max_length=50)
    success_metric: float = Field(default=0.5, ge=0.0, le=1.0)
    notes: str = Field(default="", max_length=500)


# ═══════════════════════════════════════════════════════════════════════════
# DECISION SUBMISSION
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/decisions")
async def submit_decision(
    req: DecisionRequest,
    request: Request,
    _auth=Depends(_require_auth),
):
    """Submit an action for Council deliberation."""
    
    proposal = Proposal(
        action_name=req.action_name,
        action_params=req.action_params,
        source_engine=req.source_engine,
        description=req.description,
        proposal_type=req.proposal_type,
        metadata=req.metadata,
    )
    
    # Store initial state
    decision_id = proposal.id
    async with _decision_lock:
        _decision_store[decision_id] = {
            "decision_id": decision_id,
            "action": req.action_name,
            "status": "QUEUED",
            "proposal": proposal,
            "result": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    
    # Start deliberation in background
    asyncio.create_task(_run_deliberation(decision_id, proposal))
    
    return {
        "decision_id": decision_id,
        "status": "QUEUED",
        "action": req.action_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


async def _run_deliberation(decision_id: str, proposal: Proposal) -> None:
    """Run deliberation in background."""
    async with _decision_lock:
        if decision_id in _decision_store:
            _decision_store[decision_id]["status"] = "RUNNING"
    
    try:
        result = await council.deliberate(proposal)
        
        async with _decision_lock:
            if decision_id in _decision_store:
                _decision_store[decision_id]["result"] = result
                _decision_store[decision_id]["status"] = "COMPLETE"
    except Exception as exc:
        logger.error("Deliberation failed for %s: %s", decision_id, exc)
        async with _decision_lock:
            if decision_id in _decision_store:
                _decision_store[decision_id]["status"] = "FAILED"
                _decision_store[decision_id]["error"] = str(exc)


# ═══════════════════════════════════════════════════════════════════════════
# DECISION STATUS
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/decisions/{decision_id}")
async def get_decision(
    decision_id: str,
    request: Request,
    _auth=Depends(_require_auth),
):
    """Get decision status."""
    async with _decision_lock:
        stored = _decision_store.get(decision_id)
    
    if not stored:
        raise HTTPException(404, "Decision not found")
    
    result = stored.get("result")
    
    return {
        "decision_id": decision_id,
        "action": stored["action"],
        "status": stored["status"],
        "created_at": stored["created_at"],
        "result": _serialize_result(result) if result else None,
    }


@router.get("/decisions/{decision_id}/votes")
async def get_decision_votes(
    decision_id: str,
    request: Request,
    _auth=Depends(_require_auth),
):
    """Get votes for a decision."""
    async with _decision_lock:
        stored = _decision_store.get(decision_id)
    
    if not stored or not stored.get("result"):
        raise HTTPException(404, "Decision votes not available yet")
    
    result = stored["result"]
    return {
        "votes": [
            {
                "member": v.member,
                "role": v.role,
                "vote": v.vote,
                "confidence": v.confidence,
                "reason": v.reason,
                "weight": v.weight,
                "veto_type": v.veto_type.value if hasattr(v.veto_type, 'value') else str(v.veto_type),
            }
            for v in result.votes
        ]
    }


@router.get("/decisions/{decision_id}/risk")
async def get_decision_risk(
    decision_id: str,
    request: Request,
    _auth=Depends(_require_auth),
):
    """Get risk assessment for a decision."""
    async with _decision_lock:
        stored = _decision_store.get(decision_id)
    
    if not stored:
        raise HTTPException(404, "Decision not found")
    
    return {
        "decision_id": decision_id,
        "risk_level": stored.get("proposal", Proposal).risk_level.value,
    }


@router.get("/decisions/{decision_id}/dissent")
async def get_decision_dissent(
    decision_id: str,
    request: Request,
    _auth=Depends(_require_auth),
):
    """Get dissent opinions for a decision."""
    async with _decision_lock:
        stored = _decision_store.get(decision_id)
    
    if not stored or not stored.get("result"):
        raise HTTPException(404, "Dissent not available yet")
    
    result = stored["result"]
    return {"dissent": result.dissent}


# ═══════════════════════════════════════════════════════════════════════════
# WEBSOCKET STREAM
# ═══════════════════════════════════════════════════════════════════════════

@router.websocket("/decisions/{decision_id}/stream")
async def decision_stream(websocket: WebSocket, decision_id: str):
    """Live deliberation stream via WebSocket."""
    await websocket.accept()
    
    try:
        stages = [
            "REQUEST_RECEIVED",
            "CLASSIFYING",
            "RISK_ASSESSMENT",
            "ROUTING",
            "MEMBERS_SELECTED",
            "DELIBERATING",
            "CONSENSUS",
            "ESCALATION_CHECK",
            "DECISION",
        ]
        
        for stage in stages:
            await websocket.send_json({
                "decision_id": decision_id,
                "stage": stage,
                "status": "RUNNING",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            await asyncio.sleep(0.5)  # Simulate pipeline progress
        
        # Send final result
        async with _decision_lock:
            stored = _decision_store.get(decision_id)
        
        if stored and stored.get("result"):
            await websocket.send_json({
                "decision_id": decision_id,
                "stage": "DECISION",
                "status": "COMPLETE",
                "result": _serialize_result(stored["result"]),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        else:
            await websocket.send_json({
                "decision_id": decision_id,
                "stage": "DECISION",
                "status": "FAILED",
                "error": "Deliberation failed or timed out",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        
    except WebSocketDisconnect:
        logger.debug("WebSocket client disconnected: %s", decision_id)
    except Exception as exc:
        logger.error("WebSocket error for %s: %s", decision_id, exc)
        try:
            await websocket.send_json({"error": str(exc)})
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════
# COUNCIL STATUS
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/status")
async def get_council_status(
    request: Request,
    _auth=Depends(_require_auth),
):
    """Get Council health and status."""
    return council.get_status()


@router.get("/statistics")
async def get_council_statistics(
    request: Request,
    _auth=Depends(_require_auth),
):
    """Get Council statistics."""
    status = council.get_status()
    return {
        "version": status.get("version"),
        "available": status.get("available"),
        "persona_count": status.get("personas"),
        "persona_names": status.get("persona_names"),
        "reliability": status.get("reliability"),
        "cache_size": status.get("cache_size"),
        "memory_size": status.get("memory_size"),
    }


@router.get("/members")
async def get_council_members(
    request: Request,
    _auth=Depends(_require_auth),
):
    """List all Council members with reliability."""
    status = council.get_status()
    reliability = status.get("reliability", {})
    
    members = []
    for name in status.get("persona_names", []):
        members.append({
            "name": name,
            "reliability": reliability.get(name, 0.5),
        })
    
    return {"members": members, "total": len(members)}


@router.get("/history")
async def get_decision_history(
    limit: int = Query(default=50, ge=1, le=200),
    request: Request = None,
    _auth=Depends(_require_auth),
):
    """Get decision history."""
    async with _decision_lock:
        decisions = list(_decision_store.values())[-limit:]
    
    return {
        "decisions": [
            {
                "decision_id": d["decision_id"],
                "action": d["action"],
                "status": d["status"],
                "created_at": d["created_at"],
                "decision": d.get("result").decision.value if d.get("result") else None,
            }
            for d in decisions
        ],
        "total": len(decisions),
    }


# ═══════════════════════════════════════════════════════════════════════════
# OUTCOME RECORDING
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/outcome")
async def record_outcome(
    req: OutcomeRequest,
    request: Request,
    _auth=Depends(_require_auth),
):
    """Record real-world outcome for learning."""
    async with _decision_lock:
        stored = _decision_store.get(req.decision_id)
    
    if not stored or not stored.get("result"):
        raise HTTPException(404, "Decision not found or not completed")
    
    result = stored["result"]
    council.record_outcome(
        proposal_id=req.decision_id,
        decision=result.decision,
        actual_outcome=req.actual_outcome,
        success_metric=req.success_metric,
    )
    
    return {"ok": True, "message": "Outcome recorded"}


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _serialize_result(result) -> Dict[str, Any]:
    """Serialize DeliberationResult to JSON-safe dict."""
    if not result:
        return None
    
    return {
        "proposal_id": result.proposal_id,
        "decision": result.decision.value,
        "approve_ratio": result.approve_ratio,
        "reject_ratio": result.reject_ratio,
        "quorum_ratio": result.quorum_ratio,
        "confidence": result.confidence,
        "risk_level": result.risk_level.value,
        "tier_used": result.tier_used,
        "early_terminated": result.early_terminated,
        "fallback_used": result.fallback_used,
        "deliberation_time_ms": result.deliberation_time_ms,
        "votes": [
            {
                "member": v.member,
                "role": v.role,
                "vote": v.vote,
                "confidence": v.confidence,
                "reason": v.reason,
            }
            for v in result.votes
        ],
        "dissent": result.dissent,
        "warnings": result.warnings,
        "correlation_id": result.correlation_id,
        "voted_at": result.voted_at.isoformat(),
    }