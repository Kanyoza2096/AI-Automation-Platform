from __future__ import annotations

import logging
import os
from typing import List
from urllib.parse import quote

from services.web_intelligence.base import WebKnowledgeProvider
from services.web_intelligence.models import WebResult

logger = logging.getLogger("kanyoza.web_intelligence")


class TavilyProvider(WebKnowledgeProvider):
    name = "tavily"

    def is_configured(self) -> bool:
        return bool(os.environ.get("TAVILY_API_KEY", "").strip())

    def search(self, query: str, max_results: int = 5) -> List[WebResult]:
        import requests
        key = os.environ.get("TAVILY_API_KEY", "")
        if not key:
            return []
        try:
            r = requests.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": key,
                    "query": query,
                    "max_results": max_results,
                    "include_answer": False,
                },
                timeout=20,
            )
            if r.status_code != 200:
                logger.warning("Tavily HTTP %s", r.status_code)
                return []
            out = []
            for item in (r.json().get("results") or [])[:max_results]:
                out.append(WebResult(
                    title=item.get("title") or "",
                    url=item.get("url") or "",
                    snippet=item.get("content") or item.get("snippet") or "",
                    source=self.name,
                    score=float(item.get("score") or 0),
                    raw=item,
                ))
            return out
        except Exception as exc:
            logger.warning("Tavily search failed: %s", exc)
            return []


class BraveProvider(WebKnowledgeProvider):
    name = "brave"

    def is_configured(self) -> bool:
        return bool(os.environ.get("BRAVE_API_KEY", "").strip())

    def search(self, query: str, max_results: int = 5) -> List[WebResult]:
        import requests
        key = os.environ.get("BRAVE_API_KEY", "")
        if not key:
            return []
        try:
            r = requests.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={"X-Subscription-Token": key, "Accept": "application/json"},
                params={"q": query, "count": max_results},
                timeout=20,
            )
            if r.status_code != 200:
                logger.warning("Brave HTTP %s", r.status_code)
                return []
            out = []
            for item in ((r.json().get("web") or {}).get("results") or [])[:max_results]:
                out.append(WebResult(
                    title=item.get("title") or "",
                    url=item.get("url") or "",
                    snippet=item.get("description") or "",
                    source=self.name,
                    raw=item,
                ))
            return out
        except Exception as exc:
            logger.warning("Brave search failed: %s", exc)
            return []


class NewsAPIProvider(WebKnowledgeProvider):
    name = "newsapi"

    def is_configured(self) -> bool:
        return bool(os.environ.get("NEWSAPI_KEY", "").strip())

    def search(self, query: str, max_results: int = 5) -> List[WebResult]:
        import requests
        key = os.environ.get("NEWSAPI_KEY", "")
        if not key:
            return []
        try:
            r = requests.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q": query,
                    "language": "en",
                    "sortBy": "publishedAt",
                    "pageSize": max_results,
                    "apiKey": key,
                },
                timeout=20,
            )
            if r.status_code != 200:
                return []
            out = []
            for a in (r.json().get("articles") or [])[:max_results]:
                out.append(WebResult(
                    title=a.get("title") or "",
                    url=a.get("url") or "",
                    snippet=a.get("description") or "",
                    source=self.name,
                    published_at=a.get("publishedAt"),
                    raw=a,
                ))
            return out
        except Exception as exc:
            logger.warning("NewsAPI search failed: %s", exc)
            return []


class DuckDuckGoProvider(WebKnowledgeProvider):
    """No API key — HTML/instant answer lite fallback."""

    name = "duckduckgo"

    def search(self, query: str, max_results: int = 5) -> List[WebResult]:
        import requests
        try:
            r = requests.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
                timeout=15,
                headers={"User-Agent": "KanyozaWebIntelligence/1.0"},
            )
            if r.status_code != 200:
                return []
            data = r.json()
            out: List[WebResult] = []
            abstract = data.get("AbstractText") or ""
            if abstract:
                out.append(WebResult(
                    title=data.get("Heading") or query,
                    url=data.get("AbstractURL") or "",
                    snippet=abstract,
                    source=self.name,
                    score=1.0,
                ))
            for topic in (data.get("RelatedTopics") or [])[: max_results - len(out)]:
                if not isinstance(topic, dict):
                    continue
                text = topic.get("Text") or ""
                url = topic.get("FirstURL") or ""
                if text:
                    out.append(WebResult(
                        title=text[:80],
                        url=url,
                        snippet=text,
                        source=self.name,
                    ))
            return out[:max_results]
        except Exception as exc:
            logger.debug("DuckDuckGo search failed: %s", exc)
            return []


class RedditProvider(WebKnowledgeProvider):
    name = "reddit"

    def search(self, query: str, max_results: int = 5) -> List[WebResult]:
        import requests
        sub = (query or "technology").replace(" ", "").strip() or "technology"
        # If query looks like a search phrase, use search endpoint
        try:
            if " " in (query or "") or len(query or "") > 24:
                url = f"https://www.reddit.com/search.json?q={quote(query)}&limit={max_results}"
            else:
                url = f"https://www.reddit.com/r/{sub}/hot.json?limit={max_results}"
            r = requests.get(url, headers={"User-Agent": "KanyozaWebIntelligence/1.0"}, timeout=20)
            if r.status_code != 200:
                return []
            out = []
            for child in (r.json().get("data") or {}).get("children") or []:
                d = child.get("data") or {}
                out.append(WebResult(
                    title=d.get("title") or "",
                    url=f"https://reddit.com{d.get('permalink', '')}",
                    snippet=(d.get("selftext") or "")[:300],
                    source=self.name,
                    score=float(d.get("score") or 0),
                    raw=d,
                ))
            return out[:max_results]
        except Exception as exc:
            logger.debug("Reddit search failed: %s", exc)
            return []


PROVIDERS = {
    "tavily": TavilyProvider,
    "brave": BraveProvider,
    "newsapi": NewsAPIProvider,
    "duckduckgo": DuckDuckGoProvider,
    "ddg": DuckDuckGoProvider,
    "reddit": RedditProvider,
}


def build_default_providers() -> List[WebKnowledgeProvider]:
    """Prefer configured paid/quality providers, always keep free fallbacks."""
    order = ["tavily", "brave", "newsapi", "duckduckgo", "reddit"]
    out: List[WebKnowledgeProvider] = []
    for name in order:
        cls = PROVIDERS[name]
        inst = cls()
        if inst.is_configured() or name in ("duckduckgo", "reddit"):
            out.append(inst)
    return out
