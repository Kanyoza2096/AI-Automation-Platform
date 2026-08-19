"""
Kanyoza Systems AI Platform v2 — Community Council Personas
=============================================================

Deterministic stakeholder simulation for proposal evaluation.

The council is intentionally LLM-free and API-free. Each persona evaluates a
proposal from a specific operational perspective, and the council combines
those independent opinions into an auditable decision.

Public API compatibility is retained:

    from plugins.council.personas import CommunityCouncil

    council = CommunityCouncil()
    result = council.vote_on(brand_context, proposal)

Design goals
------------
- deterministic by default (safe for tests, retries and production auditing)
- no network calls and no hidden LLM dependency
- weighted votes
- hard vetoes evaluated independently of ordinary consensus
- confidence-aware scoring
- quorum and minimum-participation protection
- bounded in-memory history
- explainable vote reasons
- defensive handling of malformed proposal/context data
- backwards-compatible persona names and council entry points
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger("kanyoza.council")


# ═══════════════════════════════════════════════════════════════════════════
# CONSTANTS / HELPERS
# ═══════════════════════════════════════════════════════════════════════════

COUNCIL_VERSION = "2.0.0"
DEFAULT_HISTORY_LIMIT = 500
DEFAULT_MIN_QUORUM_RATIO = 0.60
DEFAULT_APPROVAL_THRESHOLD = 0.55
DEFAULT_REJECTION_THRESHOLD = 0.35

VALID_VOTES = frozenset({"approve", "reject", "abstain"})

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9'_-]*", re.IGNORECASE)


def _text(value: Any) -> str:
    """Safely normalize arbitrary values into searchable text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _lower(value: Any) -> str:
    return _text(value).lower()


def _contains_any(text: str, terms: Sequence[str]) -> Optional[str]:
    """Return the first matching phrase using word-aware matching."""
    normalized = _lower(text)
    for term in terms:
        term_normalized = _lower(term)
        if not term_normalized:
            continue
        # Phrase boundaries prevent "new" from matching unrelated words.
        pattern = r"(?<![a-z0-9])" + re.escape(term_normalized) + r"(?![a-z0-9])"
        if re.search(pattern, normalized):
            return term
    return None


def _clamp(value: Any, low: float = 0.0, high: float = 1.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return low
    return max(low, min(high, number))


def _safe_number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _canonical_day(value: Any) -> str:
    return _lower(value).replace(" ", "_")


def _stable_bucket(*parts: Any) -> int:
    """
    Stable pseudo-random bucket.

    Unlike random.random(), this produces the same result for the same
    proposal/member combination across retries and process restarts.
    """
    payload = "|".join(_text(part) for part in parts).encode("utf-8")
    digest = hashlib.sha256(payload).digest()
    return int.from_bytes(digest[:8], "big") % 100


# ═══════════════════════════════════════════════════════════════════════════
# VOTE MODEL
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Vote:
    """Immutable internal representation of a persona vote."""

    member: str
    role: str
    vote: str
    weight: int
    reason: str
    confidence: float
    veto_power: bool = False
    evidence: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "member": self.member,
            "role": self.role,
            "vote": self.vote,
            "weight": self.weight,
            "reason": self.reason,
            "confidence": round(self.confidence, 3),
        }
        if self.veto_power:
            result["veto_power"] = True
        if self.evidence:
            result["evidence"] = dict(self.evidence)
        return result


# ═══════════════════════════════════════════════════════════════════════════
# BASE PERSONA
# ═══════════════════════════════════════════════════════════════════════════

class Persona:
    """Base class for all council members."""

    name: str = "persona"
    role: str = "Community Member"
    weight: int = 1
    veto_power: bool = False
    mood: str = "neutral"

    def evaluate(
        self,
        brand_context: Dict[str, Any],
        proposal: Dict[str, Any],
    ) -> Dict[str, Any]:
        raise NotImplementedError

    def _vote(
        self,
        vote: str,
        reason: str,
        confidence: float = 0.7,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        vote = vote if vote in VALID_VOTES else "abstain"
        return Vote(
            member=self.name,
            role=self.role,
            vote=vote,
            weight=max(1, _safe_int(self.weight, 1)),
            reason=_text(reason)[:500] or "No reason supplied",
            confidence=_clamp(confidence),
            veto_power=self.veto_power,
            evidence=evidence,
        ).to_dict()


# ═══════════════════════════════════════════════════════════════════════════
# 1. YOUTH ADVOCATE
# ═══════════════════════════════════════════════════════════════════════════

class YouthAdvocate(Persona):
    name = "youth_advocate"
    role = "Youth Voice (Age 22)"
    weight = 2

    YOUTH_INTERESTS = {
        "church": ("youth", "worship", "fellowship", "bible", "praise"),
        "school": ("study", "career", "scholarship", "success", "exam"),
        "hospital": ("fitness", "nutrition", "mental health", "wellness"),
        "tech": ("coding", "ai", "startup", "digital", "innovation"),
        "retail": ("style", "trending", "new", "sale", "fashion"),
        "general": ("opportunity", "growth", "future", "skills"),
    }

    BORING_WORDS = ("policy", "regulation", "compliance", "formal notice")

    def evaluate(self, brand_context, proposal):
        topic = _lower(proposal.get("topic"))
        industry = _lower(brand_context.get("industry", "general")) or "general"
        interests = self.YOUTH_INTERESTS.get(industry, self.YOUTH_INTERESTS["general"])

        matched_boring = _contains_any(topic, self.BORING_WORDS)
        if matched_boring:
            return self._vote(
                "reject",
                "Too formal for the intended young audience.",
                0.85,
                {"matched_term": matched_boring},
            )

        matched_interest = _contains_any(topic, interests)
        if matched_interest:
            return self._vote(
                "approve",
                f"Relevant to youth in {industry}.",
                0.80,
                {"matched_interest": matched_interest, "industry": industry},
            )

        hour = _safe_int(proposal.get("hour", 12), 12)
        if 18 <= hour <= 23:
            return self._vote(
                "approve",
                "Evening timing is favorable for youth engagement.",
                0.70,
                {"hour": hour},
            )

        return self._vote("abstain", "No strong youth-specific signal.", 0.40)


# ═══════════════════════════════════════════════════════════════════════════
# 2. BUSINESS OWNER
# ═══════════════════════════════════════════════════════════════════════════

class BusinessOwner(Persona):
    name = "business_owner"
    role = "Business Voice (Age 45)"
    weight = 3

    PRACTICAL_WORDS = ("how to", "tips", "guide", "simple", "easy", "steps")
    TECHNICAL_WORDS = ("api", "algorithm", "backend", "database", "code")

    def evaluate(self, brand_context, proposal):
        topic = _lower(proposal.get("topic"))
        content = _lower(proposal.get("content"))

        matched_technical = _contains_any(topic, self.TECHNICAL_WORDS)
        if matched_technical:
            return self._vote(
                "reject",
                "The topic is too technical for a general business audience.",
                0.85,
                {"matched_term": matched_technical},
            )

        matched_practical = _contains_any(
            f"{topic} {content}",
            self.PRACTICAL_WORDS,
        )
        if matched_practical:
            return self._vote(
                "approve",
                "The proposal communicates practical value.",
                0.80,
                {"matched_term": matched_practical},
            )

        if bool(proposal.get("has_cta")):
            return self._vote(
                "approve",
                "Contains a clear call-to-action.",
                0.75,
                {"has_cta": True},
            )

        return self._vote("abstain", "Acceptable, but business value is not explicit.", 0.50)


# ═══════════════════════════════════════════════════════════════════════════
# 3. COMMUNITY ELDER
# ═══════════════════════════════════════════════════════════════════════════

class CommunityElder(Persona):
    name = "community_elder"
    role = "Community Elder (Age 65)"
    weight = 3
    veto_power = True

    DISRESPECTFUL = ("stupid", "lazy", "worthless", "hate", "dumb", "idiot")
    DANGEROUS_HEALTH = (
        "miracle cure",
        "unproven",
        "fake",
        "stop medication",
    )
    COMMUNITY_VALUES = ("community", "family", "together", "support", "unity")

    def evaluate(self, brand_context, proposal):
        content = _lower(proposal.get("content"))
        topic = _lower(proposal.get("topic"))
        industry = _lower(brand_context.get("industry"))

        matched_disrespect = _contains_any(content, self.DISRESPECTFUL)
        if matched_disrespect:
            return self._vote(
                "reject",
                f"VETO: Disrespectful language detected: '{matched_disrespect}'.",
                1.0,
                {"matched_term": matched_disrespect, "veto": True},
            )

        if industry == "hospital":
            matched_health = _contains_any(
                f"{topic} {content}",
                self.DANGEROUS_HEALTH,
            )
            if matched_health:
                return self._vote(
                    "reject",
                    f"VETO: Potentially dangerous health claim: '{matched_health}'.",
                    1.0,
                    {"matched_term": matched_health, "veto": True},
                )

        matched_value = _contains_any(content, self.COMMUNITY_VALUES)
        if matched_value:
            return self._vote(
                "approve",
                "Reflects community values.",
                0.85,
                {"matched_value": matched_value},
            )

        if industry == "church":
            day = _canonical_day(proposal.get("day"))
            hour = _safe_int(proposal.get("hour", 0), 0)
            if day == "sunday" and 8 <= hour <= 12:
                return self._vote(
                    "reject",
                    "Conflicts with the configured Sunday worship window.",
                    0.80,
                    {"day": day, "hour": hour},
                )

        return self._vote("approve", "Content is socially acceptable.", 0.65)


# ═══════════════════════════════════════════════════════════════════════════
# 4. DATA ANALYST
# ═══════════════════════════════════════════════════════════════════════════

class DataAnalyst(Persona):
    name = "data_analyst"
    role = "Data Voice"
    weight = 2

    def evaluate(self, brand_context, proposal):
        engagement_ratio = _safe_number(proposal.get("engagement_ratio"), 1.0)
        sample_count = _safe_int(proposal.get("sample_count"), 0)

        if sample_count < 5:
            return self._vote(
                "abstain",
                f"Insufficient data (n={sample_count}).",
                0.20,
                {"sample_count": sample_count},
            )

        if engagement_ratio >= 1.5:
            return self._vote(
                "approve",
                f"Strong engagement ({engagement_ratio:.2f}x baseline).",
                0.90,
                {"engagement_ratio": round(engagement_ratio, 3)},
            )

        if engagement_ratio <= 0.5:
            return self._vote(
                "reject",
                f"Poor engagement ({engagement_ratio:.2f}x baseline).",
                0.85,
                {"engagement_ratio": round(engagement_ratio, 3)},
            )

        return self._vote(
            "abstain",
            "Historical data is inconclusive.",
            0.50,
            {"engagement_ratio": round(engagement_ratio, 3)},
        )


# ═══════════════════════════════════════════════════════════════════════════
# 5. RISK MANAGER
# ═══════════════════════════════════════════════════════════════════════════

class RiskManager(Persona):
    name = "risk_manager"
    role = "Risk Advisor"
    weight = 1
    veto_power = True

    RISKY_TOPICS = ("politics", "religion", "controversial", "lawsuit")

    def evaluate(self, brand_context, proposal):
        topic = _lower(proposal.get("topic"))

        matched_risk = _contains_any(topic, self.RISKY_TOPICS)
        if matched_risk:
            return self._vote(
                "reject",
                f"VETO: Risk-sensitive topic detected: '{matched_risk}'.",
                0.90,
                {"matched_term": matched_risk, "veto": True},
            )

        system_health = _safe_number(proposal.get("system_health"), 80.0)
        if system_health < 50:
            return self._vote(
                "reject",
                "System health is below the safe operating threshold.",
                0.70,
                {"system_health": system_health},
            )

        return self._vote(
            "abstain",
            "No significant operational risk detected.",
            0.60,
            {"system_health": system_health},
        )


# ═══════════════════════════════════════════════════════════════════════════
# 6. TREND SPOTTER
# ═══════════════════════════════════════════════════════════════════════════

class TrendSpotter(Persona):
    name = "trend_spotter"
    role = "Trend Voice"
    weight = 2

    TRENDING_WORDS = ("viral", "trending", "challenge", "new", "launch")

    def evaluate(self, brand_context, proposal):
        topic = _lower(proposal.get("topic"))

        matched = _contains_any(topic, self.TRENDING_WORDS)
        if matched:
            return self._vote(
                "approve",
                "Proposal contains a strong trend signal.",
                0.85,
                {"matched_term": matched},
            )

        if _lower(proposal.get("topic_source")) == "trend_intelligence":
            return self._vote(
                "approve",
                "Proposal is sourced from Trend Intelligence.",
                0.80,
                {"topic_source": "trend_intelligence"},
            )

        return self._vote("abstain", "No strong trend signal.", 0.40)


# ═══════════════════════════════════════════════════════════════════════════
# 7. GUT FEELING — CONTROLLED, DETERMINISTIC CHAOS
# ═══════════════════════════════════════════════════════════════════════════

class GutFeeling(Persona):
    name = "gut_feeling"
    role = "Intuition"
    weight = 1

    def evaluate(self, brand_context, proposal):
        """
        The original implementation used random.random(), which made identical
        requests produce different decisions. Production automation should be
        reproducible, so the wildcard now uses a stable hash bucket.
        """
        proposal_id = (
            proposal.get("id")
            or proposal.get("proposal_id")
            or proposal.get("content")
            or proposal.get("topic")
            or "anonymous"
        )
        bucket = _stable_bucket(self.name, proposal_id)

        if bucket < 10:
            return self._vote(
                "approve",
                "Gut feeling says YES — trust the instinct.",
                0.60,
                {"deterministic_bucket": bucket},
            )

        if bucket < 15:
            return self._vote(
                "approve",
                "Try something different — controlled experimentation may help.",
                0.55,
                {"deterministic_bucket": bucket},
            )

        return self._vote(
            "abstain",
            "No strong intuition right now.",
            0.30,
            {"deterministic_bucket": bucket},
        )


# ═══════════════════════════════════════════════════════════════════════════
# 8. CUSTOMER VOICE
# ═══════════════════════════════════════════════════════════════════════════

class CustomerVoice(Persona):
    name = "customer_voice"
    role = "Audience Representative"
    weight = 3

    def evaluate(self, brand_context, proposal):
        raw_content = _text(proposal.get("content"))
        content = raw_content.lower()

        if "?" in raw_content:
            return self._vote(
                "approve",
                "Asks a question and invites audience participation.",
                0.80,
                {"question_detected": True},
            )

        if len(content) > 500:
            return self._vote(
                "reject",
                "Content is too long for fast-scrolling audiences.",
                0.75,
                {"character_count": len(content)},
            )

        if 0 < len(content) < 200:
            return self._vote(
                "approve",
                "Short and punchy — easy to consume.",
                0.70,
                {"character_count": len(content)},
            )

        return self._vote("abstain", "Content length is within an average range.", 0.50)


# ═══════════════════════════════════════════════════════════════════════════
# 9. MOOD RING
# ═══════════════════════════════════════════════════════════════════════════

class MoodRing(Persona):
    name = "mood_ring"
    role = "System Mood"
    weight = 1

    def evaluate(self, brand_context, proposal):
        system_health = _safe_number(proposal.get("system_health"), 80.0)
        engagement_trend = _safe_number(proposal.get("engagement_trend"), 0.0)

        if system_health > 90 and engagement_trend > 0.1:
            return self._vote(
                "approve",
                "System is thriving — conditions support bold experimentation.",
                0.85,
                {
                    "system_health": system_health,
                    "engagement_trend": engagement_trend,
                },
            )

        if system_health > 70:
            return self._vote(
                "approve",
                "System is stable — proceed normally.",
                0.70,
                {"system_health": system_health},
            )

        if system_health > 50:
            return self._vote(
                "abstain",
                "System is under pressure — play it safe.",
                0.50,
                {"system_health": system_health},
            )

        return self._vote(
            "reject",
            "System is in crisis — hold non-essential posts.",
            0.90,
            {"system_health": system_health},
        )


# ═══════════════════════════════════════════════════════════════════════════
# 10. RIPPLER
# ═══════════════════════════════════════════════════════════════════════════

class Rippler(Persona):
    name = "rippler"
    role = "Momentum Tracker"
    weight = 2

    def evaluate(self, brand_context, proposal):
        recent_success = proposal.get("recent_success")
        similarity = _safe_number(proposal.get("similarity_to_success"), 0.0)

        if recent_success and similarity > 0.7:
            return self._vote(
                "approve",
                f"Similar to a recent hit ({similarity:.0%} match) — ride the wave.",
                0.85,
                {"similarity_to_success": round(similarity, 3)},
            )

        momentum = _safe_number(proposal.get("engagement_momentum"), 0.0)

        if momentum > 0.2:
            return self._vote(
                "approve",
                f"Positive engagement momentum ({momentum:.2f}).",
                0.75,
                {"engagement_momentum": momentum},
            )

        if momentum < -0.2:
            return self._vote(
                "reject",
                f"Negative engagement momentum ({momentum:.2f}) — change approach.",
                0.70,
                {"engagement_momentum": momentum},
            )

        return self._vote("abstain", "No clear momentum signal.", 0.40)


# ═══════════════════════════════════════════════════════════════════════════
# COMMUNITY COUNCIL
# ═══════════════════════════════════════════════════════════════════════════

class CommunityCouncil:
    """
    Weighted stakeholder council.

    Decision policy
    ---------------
    1. Every member gets an independent vote.
    2. A valid hard veto immediately rejects a proposal.
    3. Ordinary votes are weighted by member weight and confidence.
    4. Quorum prevents a decision when too many members failed to participate.
    5. Approval requires the configured approval threshold.
    6. Rejection requires the configured rejection threshold.
    7. Everything else becomes NEEDS_REVISION.

    The legacy `vote_on(brand_context, proposal)` API remains intact.
    """

    def __init__(
        self,
        members: Optional[Sequence[Persona]] = None,
        history_limit: int = DEFAULT_HISTORY_LIMIT,
        min_quorum_ratio: float = DEFAULT_MIN_QUORUM_RATIO,
        approval_threshold: float = DEFAULT_APPROVAL_THRESHOLD,
        rejection_threshold: float = DEFAULT_REJECTION_THRESHOLD,
    ):
        self.members: List[Persona] = list(
            members
            if members is not None
            else [
                YouthAdvocate(),
                BusinessOwner(),
                CommunityElder(),
                DataAnalyst(),
                RiskManager(),
                TrendSpotter(),
                GutFeeling(),
                CustomerVoice(),
                MoodRing(),
                Rippler(),
            ]
        )

        self.history_limit = max(1, _safe_int(history_limit, DEFAULT_HISTORY_LIMIT))
        self.min_quorum_ratio = _clamp(min_quorum_ratio, 0.0, 1.0)
        self.approval_threshold = _clamp(approval_threshold, 0.0, 1.0)
        self.rejection_threshold = _clamp(rejection_threshold, 0.0, 1.0)

        if self.approval_threshold <= self.rejection_threshold:
            raise ValueError(
                "approval_threshold must be greater than rejection_threshold"
            )

        self.voting_history: List[Dict[str, Any]] = []

    # ────────────────────────────────────────────────────────────────────
    # Voting
    # ────────────────────────────────────────────────────────────────────

    def vote_on(
        self,
        brand_context: Dict[str, Any],
        proposal: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Evaluate a proposal and return an auditable council decision."""
        started = time.perf_counter()

        brand_context = dict(brand_context or {})
        proposal = dict(proposal or {})

        votes: List[Dict[str, Any]] = []
        total_weight = sum(max(1, _safe_int(m.weight, 1)) for m in self.members)
        participating_weight = 0
        approve_score = 0.0
        reject_score = 0.0
        abstain_weight = 0
        errors: List[Dict[str, Any]] = []

        veto_vote: Optional[Dict[str, Any]] = None

        for member in self.members:
            member_weight = max(1, _safe_int(member.weight, 1))

            try:
                vote_result = member.evaluate(brand_context, proposal)

                # Defensive normalization if a custom persona returns malformed data.
                if not isinstance(vote_result, dict):
                    raise TypeError("Persona returned a non-dict vote result")

                vote = vote_result.get("vote")
                if vote not in VALID_VOTES:
                    raise ValueError(f"Invalid vote '{vote}'")

                confidence = _clamp(vote_result.get("confidence", 0.0))
                vote_result["weight"] = member_weight
                vote_result["confidence"] = round(confidence, 3)
                vote_result["member"] = vote_result.get("member", member.name)
                vote_result["role"] = vote_result.get("role", member.role)
                vote_result["reason"] = _text(
                    vote_result.get("reason")
                )[:500] or "No reason supplied"

                if member.veto_power:
                    vote_result["veto_power"] = True

                votes.append(vote_result)

            except Exception as exc:
                logger.exception("Council persona '%s' failed", member.name)
                errors.append(
                    {
                        "member": member.name,
                        "error": str(exc)[:300],
                    }
                )
                vote_result = member._vote(
                    "abstain",
                    "Persona evaluation failed; vote excluded from quorum.",
                    0.0,
                    {"error": str(exc)[:300]},
                )
                votes.append(vote_result)

            vote = vote_result["vote"]
            confidence = _clamp(vote_result.get("confidence", 0.0))

            # A zero-confidence fallback is deliberately non-participating.
            if confidence > 0:
                participating_weight += member_weight

            if vote == "approve":
                approve_score += member_weight * confidence
            elif vote == "reject":
                reject_score += member_weight * confidence
            else:
                abstain_weight += member_weight

            # Hard vetoes are checked independently from normal consensus.
            if (
                vote == "reject"
                and member.veto_power
                and confidence >= 0.90
            ):
                veto_vote = vote_result

        quorum_ratio = (
            participating_weight / total_weight if total_weight else 0.0
        )

        if veto_vote is not None:
            decision = "REJECTED"
            decision_reason = (
                f"VETO by {veto_vote['member']}: {veto_vote['reason']}"
            )
        elif quorum_ratio < self.min_quorum_ratio:
            decision = "NEEDS_REVISION"
            decision_reason = (
                f"Insufficient council quorum "
                f"({quorum_ratio:.0%} < {self.min_quorum_ratio:.0%})."
            )
        else:
            decision_weight = approve_score + reject_score

            if decision_weight <= 0:
                approve_ratio = 0.0
                reject_ratio = 0.0
                decision = "NEEDS_REVISION"
                decision_reason = "No decisive approve/reject evidence."
            else:
                approve_ratio = approve_score / decision_weight
                reject_ratio = reject_score / decision_weight

                if approve_ratio >= self.approval_threshold:
                    decision = "APPROVED"
                    decision_reason = (
                        f"Weighted approval reached {approve_ratio:.0%}."
                    )
                elif reject_ratio >= self.rejection_threshold:
                    decision = "REJECTED"
                    decision_reason = (
                        f"Weighted rejection reached {reject_ratio:.0%}."
                    )
                else:
                    decision = "NEEDS_REVISION"
                    decision_reason = (
                        "Council did not reach a decisive consensus."
                    )

        # Keep ratios meaningful even when a veto/quorum path exits the normal
        # scoring branch.
        decision_weight = approve_score + reject_score
        if decision_weight > 0:
            approve_ratio = approve_score / decision_weight
            reject_ratio = reject_score / decision_weight
        else:
            approve_ratio = 0.0
            reject_ratio = 0.0

        elapsed_ms = (time.perf_counter() - started) * 1000

        result: Dict[str, Any] = {
            "decision": decision,
            "reason": decision_reason,
            "approve_ratio": round(approve_ratio, 4),
            "reject_ratio": round(reject_ratio, 4),
            "quorum_ratio": round(quorum_ratio, 4),
            "votes": votes,
            "total_weight": total_weight,
            "participating_weight": participating_weight,
            "abstain_weight": abstain_weight,
            "approve_score": round(approve_score, 4),
            "reject_score": round(reject_score, 4),
            "veto": veto_vote is not None,
            "veto_member": veto_vote["member"] if veto_vote else None,
            "errors": errors,
            "council_version": COUNCIL_VERSION,
            "voting_time_ms": round(elapsed_ms, 3),
            "voted_at": datetime.now(timezone.utc).isoformat(),
        }

        self._record_history(result)
        return result

    # ────────────────────────────────────────────────────────────────────
    # History / analytics
    # ────────────────────────────────────────────────────────────────────

    def _record_history(self, result: Dict[str, Any]) -> None:
        """Store a bounded copy so long-running workers cannot grow forever."""
        self.voting_history.append(result)

        overflow = len(self.voting_history) - self.history_limit
        if overflow > 0:
            del self.voting_history[:overflow]

    def clear_history(self) -> None:
        self.voting_history.clear()

    def get_history(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Return newest history records first."""
        if limit is None:
            limit = self.history_limit
        limit = max(0, _safe_int(limit, self.history_limit))
        if limit == 0:
            return []
        return list(reversed(self.voting_history[-limit:]))

    def get_statistics(self) -> Dict[str, Any]:
        """Return lightweight council performance statistics."""
        if not self.voting_history:
            return {
                "total_votes": 0,
                "decisions": {},
                "approval_rate": 0.0,
                "rejection_rate": 0.0,
                "revision_rate": 0.0,
                "veto_rate": 0.0,
            }

        decisions = Counter(
            str(item.get("decision", "UNKNOWN"))
            for item in self.voting_history
        )
        total = len(self.voting_history)

        return {
            "total_votes": total,
            "decisions": dict(decisions),
            "approval_rate": round(
                decisions.get("APPROVED", 0) / total, 4
            ),
            "rejection_rate": round(
                decisions.get("REJECTED", 0) / total, 4
            ),
            "revision_rate": round(
                decisions.get("NEEDS_REVISION", 0) / total, 4
            ),
            "veto_rate": round(
                sum(1 for x in self.voting_history if x.get("veto")) / total,
                4,
            ),
        }

    # ────────────────────────────────────────────────────────────────────
    # Explanation
    # ────────────────────────────────────────────────────────────────────

    def get_summary(self, vote_result: Dict[str, Any]) -> str:
        """Generate a concise human-readable explanation."""
        votes = vote_result.get("votes", [])

        approve_count = sum(
            1 for vote in votes if vote.get("vote") == "approve"
        )
        reject_count = sum(
            1 for vote in votes if vote.get("vote") == "reject"
        )
        abstain_count = sum(
            1 for vote in votes if vote.get("vote") == "abstain"
        )

        reasons = [
            f"  • {vote.get('role', vote.get('member', 'Member'))}: "
            f"{vote.get('reason', '')}"
            for vote in votes
            if vote.get("vote") != "abstain"
        ]

        return (
            f"Council Decision: {vote_result.get('decision', 'UNKNOWN')}\n"
            f"  Reason: {vote_result.get('reason', 'N/A')}\n"
            f"  ✅ {approve_count} approve | "
            f"❌ {reject_count} reject | "
            f"🤷 {abstain_count} abstain\n"
            f"  Approval score ratio: "
            f"{float(vote_result.get('approve_ratio', 0)):.0%}\n"
            f"  Rejection score ratio: "
            f"{float(vote_result.get('reject_ratio', 0)):.0%}\n"
            f"  Quorum: "
            f"{float(vote_result.get('quorum_ratio', 0)):.0%}\n"
            f"Reasons:\n"
            + ("\n".join(reasons[:5]) if reasons else "  • None")
        )


# ═══════════════════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════════════════

council = CommunityCouncil()


def get_council() -> CommunityCouncil:
    """Return the process-local council singleton."""
    return council


__all__ = [
    "COUNCIL_VERSION",
    "Persona",
    "Vote",
    "YouthAdvocate",
    "BusinessOwner",
    "CommunityElder",
    "DataAnalyst",
    "RiskManager",
    "TrendSpotter",
    "GutFeeling",
    "CustomerVoice",
    "MoodRing",
    "Rippler",
    "CommunityCouncil",
    "council",
    "get_council",
]
