"""
Organism plan authority model — single policy matrix.

FREE → SUGGEST
STARTER → AUTO_LOW
BUSINESS → AUTO_MEDIUM
ENTERPRISE → AUTO_HIGH
SPECIES → platform-level
"""

from __future__ import annotations

from enum import Enum
from typing import Optional


class PlanTier(str, Enum):
    FREE = "free"
    STARTER = "starter"
    BUSINESS = "business"
    ENTERPRISE = "enterprise"
    SPECIES = "species"


class AuthorityLevel(str, Enum):
    SUGGEST = "SUGGEST"
    AUTO_LOW = "AUTO_LOW"
    AUTO_MEDIUM = "AUTO_MEDIUM"
    AUTO_HIGH = "AUTO_HIGH"
    SPECIES = "SPECIES"
    BLOCK = "BLOCK"


_PLAN_AUTHORITY = {
    PlanTier.FREE: AuthorityLevel.SUGGEST,
    PlanTier.STARTER: AuthorityLevel.AUTO_LOW,
    PlanTier.BUSINESS: AuthorityLevel.AUTO_MEDIUM,
    PlanTier.ENTERPRISE: AuthorityLevel.AUTO_HIGH,
    PlanTier.SPECIES: AuthorityLevel.SPECIES,
}


def normalize_plan(plan: Optional[str]) -> PlanTier:
    if not plan:
        return PlanTier.FREE
    key = plan.strip().lower()
    for tier in PlanTier:
        if tier.value == key:
            return tier
    return PlanTier.FREE


def authority_for_plan(plan: Optional[str]) -> AuthorityLevel:
    return _PLAN_AUTHORITY[normalize_plan(plan)]


def can_auto_execute(plan: Optional[str], *, species: bool = False) -> bool:
    if species:
        return True
    level = authority_for_plan(plan)
    return level in (
        AuthorityLevel.AUTO_LOW,
        AuthorityLevel.AUTO_MEDIUM,
        AuthorityLevel.AUTO_HIGH,
        AuthorityLevel.SPECIES,
    )


def director_decision_for_plan(plan: Optional[str], *, species: bool = False) -> str:
    """Map plan to pipeline Decision value."""
    if species:
        return "ALLOW"
    level = authority_for_plan(plan)
    if level == AuthorityLevel.SUGGEST:
        return "SUGGEST"
    if level == AuthorityLevel.AUTO_LOW:
        return "AUTO_LOW"
    if level == AuthorityLevel.AUTO_MEDIUM:
        return "AUTO_MEDIUM"
    if level == AuthorityLevel.AUTO_HIGH:
        return "AUTO_HIGH"
    return "SUGGEST"


def authority_from_workspace(workspace_id: str) -> AuthorityLevel:
    """Resolve authority from organism plan via Metabolism catalog."""
    try:
        from core.metabolism import metabolism
        limits = metabolism.limits_for(workspace_id)
        auth = (limits.director_authority or "suggest").lower()
        mapping = {
            "suggest": AuthorityLevel.SUGGEST,
            "notify": AuthorityLevel.SUGGEST,
            "auto_low": AuthorityLevel.AUTO_LOW,
            "auto_medium": AuthorityLevel.AUTO_MEDIUM,
            "auto_high": AuthorityLevel.AUTO_HIGH,
        }
        return mapping.get(auth, AuthorityLevel.SUGGEST)
    except Exception:
        return AuthorityLevel.SUGGEST
