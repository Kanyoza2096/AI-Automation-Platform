# routes/council_routes.py
# ═══════════════════════════════════════════════════════════════════════════
# KANYOZA COMMUNITY COUNCIL API ROUTES v2.3
# Wired to the Democracy Engine (council/ root package, 12 personas)
# ═══════════════════════════════════════════════════════════════════════════

from fastapi import APIRouter, HTTPException, Depends, Request, Query
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
import asyncio
import hmac
import inspect
import logging
from datetime import datetime, timezone

logger = logging.getLogger("kanyoza.council.api")

router = APIRouter(prefix="/api/v1/council", tags=["council"])


# ═══════════════════════════════════════════════════════════════════════════
# AUTH
# ═══════════════════════════════════════════════════════════════════════════

def _require_auth(request: Request):
    """Authenticate using master token, Python service token, or X-API-Key."""
    from config.settings import settings

    token = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
    if not token:
        token = request.headers.get("X-API-Key", "").strip()

    if not token:
        raise HTTPException(401, "Missing authentication token")

    master_token = getattr(settings, "master_api_token", "") or ""
    python_service_token = getattr(settings, "python_service_token", "") or ""

    if master_token and hmac.compare_digest(token, master_token):
        return True

    if python_service_token and hmac.compare_digest(token, python_service_token):
        return True

    raise HTTPException(401, "Unauthorized")


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
# MEMBER METADATA — covers all 12 personas in the Democracy Engine
# ═══════════════════════════════════════════════════════════════════════════

MEMBER_EMOJIS = {
    # Original 10
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
    # New — v12 council
    "affiliate_marketer": "💸",
    "theologian": "🕊️",
    # Aliases just in case the class exposes a slightly different name
    "affiliate": "💸",
    "theology": "🕊️",
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
    "affiliate_marketer": "analytical",
    "theologian": "empathic",
    "affiliate": "analytical",
    "theology": "empathic",
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
    "affiliate_marketer": "revenue_focused",
    "theologian": "values_focused",
    "affiliate": "revenue_focused",
    "theology": "values_focused",
}

# Fallback emojis by role when name isn't matched
ROLE_EMOJIS = {
    "Revenue Voice": "💸",
    "Spiritual Advisor": "🕊️",
}


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════

async def _maybe_await(value):
    """Return value if sync; await if coroutine/awaitable."""
    if inspect.isawaitable(value):
        return await value
    return value


def _pick_emoji(member) -> str:
    name = getattr(member, "name", "") or ""
    role = getattr(member, "role", "") or ""
    return (
        MEMBER_EMOJIS.get(name)
        or ROLE_EMOJIS.get(role)
        or "🤖"
    )


# ═══════════════════════════════════════════════════════════════════════════
# ROUTES
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/vote")
async def run_council_vote(data: VoteRequest, _auth=Depends(_require_auth)):
    """
    Run a community council vote on a proposal.

    Returns:
        - Real votes from all personas in the Democracy Engine
        - Enhanced analytics (consensus strength, dissenting opinion,
          predictive outcome, historical context)
    """
    from council.personas import get_council

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

    # ── Run the vote (handles sync OR async vote_on) ───────────────────
    result = await _maybe_await(council.vote_on(brand_context, proposal))

    # ── Enhanced Analytics ─────────────────────────────────────────────
    approval = float(result.get("approve_ratio", 0))
    rejection = float(result.get("reject_ratio", 0))

    diff = abs(approval - rejection)
    if diff > 0.6:
        consensus_strength = "STRONG"
    elif diff > 0.3:
        consensus_strength = "MODERATE"
    else:
        consensus_strength = "WEAK"

    # ── Dissenting opinion ─────────────────────────────────────────────
    strongest_reject = None
    for vote in result.get("votes", []):
        if vote.get("vote") == "reject":
            confidence = float(vote.get("confidence", 0))
            if strongest_reject is None or confidence > strongest_reject.get("confidence", 0):
                strongest_reject = vote

    dissenting_opinion = ""
    if strongest_reject:
        dissenting_opinion = (
            f"{strongest_reject.get('role', 'Member')} dissents "
            f"({strongest_reject.get('confidence', 0):.0%} confidence): "
            f"{strongest_reject.get('reason', '')}"
        )

    # ── Historical context ─────────────────────────────────────────────
    stats = council.get_statistics() or {}
    historical_approval = float(stats.get("approval_rate", 0.5) or 0.5)
    total_votes = int(stats.get("total_votes", 0) or 0)

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

    # ── Predictive outcome ─────────────────────────────────────────────
    success_probability = min(0.95, max(0.1, approval * 0.6 + historical_approval * 0.4))

    risk_factors = []
    if approval < 0.6:
        risk_factors.append("Low council consensus")
    if result.get("veto"):
        risk_factors.append(f"Veto by {result.get('veto_member')}")
    if float(result.get("quorum_ratio", 0) or 0) < 0.6:
        risk_factors.append("Low quorum")
    if not risk_factors:
        risk_factors.append("Minimal identified risks")

    recommended_mitigations = []
    if approval < 0.5:
        recommended_mitigations.append("Revise proposal based on council feedback")
    if float(result.get("quorum_ratio", 0) or 0) < 0.7:
        recommended_mitigations.append("Engage more council members")
    if not recommended_mitigations:
        recommended_mitigations.append("Proceed with monitoring")

    # ── Enhance votes with member metadata ─────────────────────────────
    enhanced_votes = []
    for vote in result.get("votes", []):
        member_name = vote.get("member", vote.get("member_name", ""))
        enhanced_votes.append({
            **vote,
            "member_id": member_name,
            "member_name": member_name,
            "emoji": MEMBER_EMOJIS.get(member_name, "🤖"),
            "historical_alignment": round(historical_approval, 3),
            "influence": round(min(1.0, float(vote.get("weight", 1) or 1) / 5.0), 3),
            "argument": vote.get("reason", ""),
        })

    enhanced = {
        **result,
        "id": result.get("correlation_id") or result.get("proposal_id") or "",
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
    Serves the 12-persona Democracy Engine council.
    """
    from council.personas import get_council

    council = get_council()
    stats = council.get_statistics() or {}

    members = []
    for m in council.members:
        approval = float(stats.get("approval_rate", 0.5) or 0.5)
        weight = float(getattr(m, "weight", 1) or 1)
        influence = round(min(1.0, weight / 5.0), 3)
        name = getattr(m, "name", "unknown")
        role = getattr(m, "role", name)

        members.append({
            "id": name,
            "name": name,
            "role": role,
            "seat": role,
            "weight": weight,
            "veto_power": bool(getattr(m, "veto_power", False)),
            "emoji": _pick_emoji(m),
            "personality": PERSONALITY_MAP.get(name, "analytical"),
            "bias_orientation": BIAS_MAP.get(name, "data_driven"),
            "historical_alignment": round(approval, 3),
            "influence": influence,
            "expertise": [role],
            "status": "active",
            "reputationScore": round(influence * 100, 1),
            "reputation": round(influence * 100, 1),
            "confidence": round(approval, 3),
            "latency": "—",
        })

    return {
        "members": members,
        "total_members": len(members),
        "council_version": getattr(council, "council_version", "1.0.0"),
        "statistics": stats,
    }


@router.get("/statistics")
async def get_council_statistics(_auth=Depends(_require_auth)):
    """Get council voting statistics."""
    from council.personas import get_council

    council = get_council()
    return council.get_statistics() or {}


@router.get("/stats")
async def get_council_stats_alias(_auth=Depends(_require_auth)):
    """Alias for /statistics — returns the same data."""
    from council.personas import get_council

    council = get_council()
    return council.get_statistics() or {}


@router.get("/history")
async def get_council_history(
    limit: int = Query(default=20, ge=1, le=100),
    _auth=Depends(_require_auth),
):
    """Get recent council voting history."""
    from council.personas import get_council

    council = get_council()
    history = council.get_history(limit=limit) or []

    return {
        "history": history,
        "count": len(history),
    }
