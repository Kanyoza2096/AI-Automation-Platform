from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from services.web_intelligence.base import WebKnowledgeProvider
from services.web_intelligence.models import WebPage, WebResult
from services.web_intelligence.providers import build_default_providers

logger = logging.getLogger("kanyoza.web_intelligence")

_gateway: Optional["WebIntelligenceGateway"] = None


class WebIntelligenceGateway:
    """
    Unified tool surface for Director / Orchestrator / Trend.

    search(query) → List[WebResult]
    fetch(url) → WebPage
    """

    VERSION = "1.0.0"

    def __init__(self, providers: Optional[List[WebKnowledgeProvider]] = None):
        self._providers = providers or build_default_providers()
        logger.info(
            "WebIntelligence v%s providers: %s",
            self.VERSION,
            [p.name for p in self._providers],
        )

    def list_providers(self) -> List[Dict[str, Any]]:
        return [
            {"name": p.name, "configured": p.is_configured()}
            for p in self._providers
        ]

    def search(
        self,
        query: str,
        max_results: int = 5,
        provider: Optional[str] = None,
    ) -> List[WebResult]:
        q = (query or "").strip()
        if not q:
            return []
        providers = self._providers
        if provider:
            providers = [p for p in self._providers if p.name == provider] or providers
        for p in providers:
            try:
                if provider is None and not p.is_configured() and p.name not in ("duckduckgo", "reddit"):
                    continue
                results = p.search(q, max_results=max_results)
                if results:
                    logger.info(
                        "WebIntelligence search via %s → %d results for %r",
                        p.name,
                        len(results),
                        q[:60],
                    )
                    return results
            except Exception as exc:
                logger.warning("Provider %s failed: %s", p.name, exc)
        return []

    async def search_async(
        self,
        query: str,
        max_results: int = 5,
        provider: Optional[str] = None,
    ) -> List[WebResult]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, lambda: self.search(query, max_results=max_results, provider=provider)
        )

    def fetch(self, url: str, provider: Optional[str] = None) -> WebPage:
        providers = self._providers
        if provider:
            providers = [p for p in self._providers if p.name == provider] or providers
        p = providers[0] if providers else None
        if p is None:
            return WebPage(url=url, error="no_provider")
        return p.fetch(url)

    async def fetch_async(self, url: str, provider: Optional[str] = None) -> WebPage:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self.fetch(url, provider=provider))

    def search_dict(self, query: str, max_results: int = 5, **kwargs) -> Dict[str, Any]:
        results = self.search(query, max_results=max_results, provider=kwargs.get("provider"))
        return {
            "ok": bool(results),
            "query": query,
            "count": len(results),
            "results": [r.to_dict() for r in results],
            "providers": self.list_providers(),
        }


def get_web_intelligence() -> Optional[WebIntelligenceGateway]:
    return _gateway


def init_web_intelligence(providers: Optional[List[WebKnowledgeProvider]] = None) -> WebIntelligenceGateway:
    global _gateway
    _gateway = WebIntelligenceGateway(providers=providers)
    return _gateway
