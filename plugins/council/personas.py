"""Deprecated Council compatibility facade.

The canonical Council implementation lives exclusively in :mod:`council`.
This module intentionally contains no independent personas, voting logic, or
Council singleton. Legacy imports are delegated to the canonical root Council.
"""
from __future__ import annotations

import warnings
from typing import Any

from council.integration import get_council as _get_canonical_council
from council.personas import (
    ALL_PERSONAS,
    AffiliateMarketer,
    BusinessOwner,
    CommunityElder,
    CustomerVoice,
    DataAnalyst,
    GutFeeling,
    MoodRing,
    Rippler,
    RiskManager,
    Theologian,
    TrendSpotter,
    YouthAdvocate,
)

warnings.warn(
    "plugins.council is deprecated; use council.*",
    DeprecationWarning,
    stacklevel=2,
)

COUNCIL_VERSION = "1.0.0-canonical-compat"

class CommunityCouncil:
    """Backward-compatible facade; all operations delegate to root Council."""

    def __new__(cls, *args: Any, **kwargs: Any):
        return _get_canonical_council()


def get_council():
    """Return the canonical root Council singleton."""
    return _get_canonical_council()


council = _get_canonical_council()

__all__ = [
    "COUNCIL_VERSION", "ALL_PERSONAS", "CommunityCouncil", "get_council", "council",
    "YouthAdvocate", "BusinessOwner", "CommunityElder", "DataAnalyst",
    "RiskManager", "TrendSpotter", "CustomerVoice", "GutFeeling", "MoodRing",
    "Rippler", "AffiliateMarketer", "Theologian",
]
