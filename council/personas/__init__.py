"""All Council personas — now with Affiliator and Theologian."""

from council.personas.base import Persona
from council.personas.youth import YouthAdvocate
from council.personas.business import BusinessOwner
from council.personas.elder import CommunityElder
from council.personas.analyst import DataAnalyst
from council.personas.risk import RiskManager
from council.personas.trend import TrendSpotter
from council.personas.customer import CustomerVoice
from council.personas.intuition import GutFeeling
from council.personas.mood import MoodRing
from council.personas.momentum import Rippler
from council.personas.affiliator import AffiliateMarketer  # ✅ NEW
from council.personas.theologian import Theologian  # ✅ NEW

ALL_PERSONAS = [
    YouthAdvocate,
    BusinessOwner,
    CommunityElder,
    DataAnalyst,
    RiskManager,
    TrendSpotter,
    CustomerVoice,
    GutFeeling,
    MoodRing,
    Rippler,
    AffiliateMarketer,  # ✅ NEW — Revenue Voice
    Theologian,        # ✅ NEW — Spiritual Advisor
]

__all__ = [
    "Persona",
    "YouthAdvocate", "BusinessOwner", "CommunityElder",
    "DataAnalyst", "RiskManager", "TrendSpotter",
    "CustomerVoice", "GutFeeling", "MoodRing", "Rippler",
    "AffiliateMarketer", "Theologian",  # ✅ NEW
    "ALL_PERSONAS",
]

def get_council():
    """Compatibility export for routes that import get_council from personas."""
    from council.integration import get_council as _gc
    return _gc()

