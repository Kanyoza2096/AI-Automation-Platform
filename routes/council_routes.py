# routes/council_routes.py
# ═══════════════════════════════════════════════════════════════════════════
# KANYOZA COMMUNITY COUNCIL API ROUTES v2.0
# Real endpoints matching the frontend contract
# ═══════════════════════════════════════════════════════════════════════════

from fastapi import APIRouter, HTTPException, Depends, Request, Query
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
import hmac
import logging
from datetime import datetime, timezone

logger = logging.getLogger("kanyoza.council.api")

router = APIRouter(prefix="/api/v1/council", tags=["council"])


# ═══════════════════════════════════════════════════════════════════════════
# AUTH
# ═══════════════════════════════════════════════════════════════════════════

def _require_auth(request: Request):
    """Authenticate using master token or X-API-Key."""
    from config.settings import settings
    token = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
    if not token:
        token = request.headers.get("X-API-Key", "").strip()
    if not token or not hmac.compare_digest(token, settings.master_api_token):
        raise HTTPException(401, "Unauthorized")
    return True


# ═══════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ═══════════════════════════════════════════════════════════════════════════

class VoteRequest(BaseModel):
    proposal: str = Field(min_length=1, max_length=500)
    topic: str = Field(default="", max_length=200)
    industry: str = Field(default="general", max_length=50)
    hour: int = Field(default=12, ge=0, le=23)
    day: str = Field(default="monday", max_length=20)
    content: str = Field(default="", max_length=1000)
    has_cta: bool = False
    system_health: float = Field(default=80.0, ge=0, le=100)
    engagement_ratio: float = Field(default=1.0, ge=0)
    sample_count: int = Field(default=10, ge=0)
    topic_source: str = Field(default="director")
    engagement_trend: float = Field(default=0.0, ge=-1.0, le=1.0)


# ═══════════════════════════════════════════════════════════════════════════
# EMOJI MAP
# ═══════════════════════════════════════════════════════════════════════════

MEMBER_EMOJIS = {
    "youth_advocate": "🧑🏾‍🎓",
    "business_owner": "👨🏾‍💼",
    "community_elder": "👵🏾",
    "data_analyst": "📊",
    "risk_manager": "🛡️",
    "trend_spotter": "🔥",
    "gut_feeling": "🎲",
    "customer_voice": "💬",
    "mood_ring": "🌈",
    "rippler": "🌊",
}

PERSONALITY_MAP = {
    "youth_advocate": "visionary",
    "business_owner": "analytical",
    "community_elder": "empathic",
    "data_analyst": "analytical",
    "risk_manager": "cautious",
    "trend_spotter": "visionary",
    "gut_feeling": "creative",
    "customer_voice": "empathic",
    "mood_ring": "empathic",
    "rippler": "analytical",
}

BIAS_MAP = {
    "youth_advocate": "pro_innovation",
    "business_owner": "data_driven",
    "community_elder": "community_focused",
    "data_analyst": "data_driven",
    "risk_manager": "risk_averse",
    "trend_spotter": "pro_innovation",
    "gut_feeling": "pro_innovation",
    "customer_voice": "community_focused",
    "mood_ring": "data_driven",
    "rippler": "data_driven",
}


# ═══════════════════════════════════════════════════════════════════════════
# ROUTES
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/vote")
async def run_council_vote(data: VoteRequest, _auth=Depends(_require_auth)):
    """
    Run a community council vote on a proposal.
    
    Returns:
        - Real votes from all 10 personas
        - Enhanced analytics (consensus strength, dissenting opinion,
          predictive outcome, historical context)
    """
    from plugins.council.personas import get_council
    
    council = get_council()
    
    brand_context = {"industry": data.industry}
    proposal = {
        "id": data.proposal[:50].lower().replace(" ", "_"),
        "topic": data.topic or data.proposal,
        "content": data.content or data.proposal,
        "hour": data.hour,
        "day": data.day,
        "has_cta": data.has_cta,
        "system_health": data.system_health,
        "engagement_ratio": data.engagement_ratio,
        "sample_count": data.sample_count,
        "topic_source": data.topic_source,
        "engagement_trend": data.engagement_trend,
    }
    
    # Run the REAL vote
    result = council.vote_on(brand_context, proposal)
    import asyncio
    if asyncio.iscoroutine(result):
        result = asyncio.run(result)
    
    # ── Enhanced Analytics ──────────────────────────────────────────────
    approval = float(result.get("approve_ratio", 0))
    rejection = float(result.get("reject_ratio", 0))
    
    # Consensus strength
    diff = abs(approval - rejection)
    if diff > 0.6:
        consensus_strength = "STRONG"
    elif diff > 0.3:
        consensus_strength = "MODERATE"
    else:
        consensus_strength = "WEAK"
    
    # Dissenting opinion — find strongest reject vote
    dissenting_opinion = ""
    strongest_reject = None
    for vote in result.get("votes", []):
        if vote.get("vote") == "reject":
            confidence = float(vote.get("confidence", 0))
            if strongest_reject is None or confidence > strongest_reject.get("confidence", 0):
                strongest_reject = vote
    
    if strongest_reject:
        dissenting_opinion = (
            f"{strongest_reject['role']} dissents "
            f"({strongest_reject['confidence']:.0%} confidence): "
            f"{strongest_reject['reason']}"
        )
    
    # Historical context
    stats = council.get_statistics()
    historical_approval = float(stats.get("approval_rate", 0.5))
    total_votes = int(stats.get("total_votes", 0))
    
    if historical_approval > 0.6:
        trend = "improving"
    elif historical_approval < 0.4:
        trend = "declining"
    else:
        trend = "stable"
    
    minority_report = (
        f"{stats.get('rejection_rate', 0):.0%} historical rejection rate "
        f"across {total_votes} decisions. "
        f"{stats.get('veto_rate', 0):.0%} veto rate."
    )
    
    # Predictive outcome
    success_probability = min(0.95, max(0.1, approval * 0.6 + historical_approval * 0.4))
    
    risk_factors = []
    if approval < 0.6:
        risk_factors.append("Low council consensus")
    if result.get("veto"):
        risk_factors.append(f"Veto by {result.get('veto_member')}")
    if result.get("quorum_ratio", 0) < 0.6:
        risk_factors.append("Low quorum")
    if not risk_factors:
        risk_factors.append("Minimal identified risks")
    
    recommended_mitigations = []
    if approval < 0.5:
        recommended_mitigations.append("Revise proposal based on council feedback")
    if result.get("quorum_ratio", 0) < 0.7:
        recommended_mitigations.append("Engage more council members")
    if not recommended_mitigations:
        recommended_mitigations.append("Proceed with monitoring")
    
    # Add member metadata to votes
    enhanced_votes = []
    for vote in result.get("votes", []):
        member_name = vote.get("member", vote.get("member_name", ""))
        enhanced_votes.append({
            **vote,
            "member_id": member_name,
            "member_name": member_name,
            "emoji": MEMBER_EMOJIS.get(member_name, "🤖"),
            "historical_alignment": round(historical_approval, 3),
            "influence": round(min(1.0, vote.get("weight", 1) / 5.0), 3),
            "argument": vote.get("reason", ""),  # Full reason as argument
        })
    
    enhanced = {
        **result,
        "id": result.get("correlation_id", ""),
        "votes": enhanced_votes,
        "proposal": data.proposal,
        "voted_at": datetime.now(timezone.utc).isoformat(),
        "consensus_strength": consensus_strength,
        "dissenting_opinion": dissenting_opinion,
        "minority_report": minority_report,
        "predictive_outcome": {
            "success_probability": round(success_probability, 4),
            "risk_factors": risk_factors,
            "recommended_mitigations": recommended_mitigations,
        },
        "historical_context": {
            "similar_decisions": total_votes,
            "average_approval": round(historical_approval, 4),
            "trend": trend,
        },
    }
    
    return enhanced


@router.get("/members")
async def get_council_members(_auth=Depends(_require_auth)):
    """
    Get all council members with full metadata for the UI.
    Returns real data from the Python council.
    """
    from plugins.council.personas import get_council
    
    council = get_council()
    stats = council.get_statistics()
    
    members = []
    for m in council.members:
        members.append({
            "id": m.name,
            "name": m.name,
            "role": m.role,
            "weight": m.weight,
            "veto_power": m.veto_power,
            "emoji": MEMBER_EMOJIS.get(m.name, "🤖"),
            "personality": PERSONALITY_MAP.get(m.name, "analytical"),
            "bias_orientation": BIAS_MAP.get(m.name, "data_driven"),
            "historical_alignment": round(stats.get("approval_rate", 0.5), 3),
            "influence": round(min(1.0, m.weight / 5.0), 3),
            "expertise": [m.role],
        })
    
    return {
        "members": members,
        "total_members": len(members),
        "council_version": getattr(council, "council_version", "3.0.0"),
        "statistics": stats,
    }


@router.get("/statistics")
async def get_council_statistics(_auth=Depends(_require_auth)):
    """Get council voting statistics."""
    from plugins.council.personas import get_council
    
    council = get_council()
    return council.get_statistics()


@router.get("/history")
async def get_council_history(
    limit: int = Query(default=20, ge=1, le=100),
    _auth=Depends(_require_auth),
):
    """Get recent council voting history."""
    from plugins.council.personas import get_council
    
    council = get_council()
    history = council.get_history(limit=limit)
    
    return {
        "history": history,
        "count": len(history),
    }
