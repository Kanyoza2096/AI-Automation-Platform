"""Web Intelligence — tool gateway for fresh external knowledge."""

from services.web_intelligence.gateway import (
    WebIntelligenceGateway,
    get_web_intelligence,
    init_web_intelligence,
)
from services.web_intelligence.models import WebPage, WebResult

__all__ = [
    "WebIntelligenceGateway",
    "WebResult",
    "WebPage",
    "get_web_intelligence",
    "init_web_intelligence",
]
