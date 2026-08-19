"""
Community Council Plugin Package
================================

Rule-based persona plugins that vote on system decisions.
No LLMs, no API costs, instant voting.
"""

from plugins.council.personas import (
    Persona,
    YouthAdvocate,
    BusinessOwner,
    CommunityElder,
    DataAnalyst,
    RiskManager,
    TrendSpotter,
    GutFeeling,
    CustomerVoice,
    MoodRing,
    Rippler,
    CommunityCouncil,
    council,
    get_council,
)

__all__ = [
    "Persona",
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