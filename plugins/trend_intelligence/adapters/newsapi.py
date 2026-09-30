from __future__ import annotations

import logging
import os
from typing import List

from plugins.trend_intelligence.adapters.base import NormalizedTrend, TrendSourceAdapter, TrendSourceFetchError

logger = logging.getLogger("kanyoza.plugins.trends.newsapi")


class NewsAPIAdapter(TrendSourceAdapter):
    platform = "newsapi"

    def is_configured(self) -> bool:
        return bool(os.environ.get("NEWSAPI_KEY", "").strip())

    def fetch(self, query: str, **kwargs) -> List[NormalizedTrend]:
        import requests
        api_key = os.environ.get("NEWSAPI_KEY", "")
        if not api_key:
            return []
        url = (
            "https://newsapi.org/v2/everything"
            f"?q={requests.utils.quote(query or 'technology')}"
            "&language=en&sortBy=publishedAt&pageSize=10"
            f"&apiKey={api_key}"
        )
        try:
            r = requests.get(url, timeout=20)
            if r.status_code != 200:
                raise TrendSourceFetchError(f"NewsAPI HTTP {r.status_code}")
            out: List[NormalizedTrend] = []
            for a in (r.json().get("articles") or [])[:10]:
                out.append(NormalizedTrend(
                    title=a.get("title") or "",
                    content=a.get("description") or "",
                    url=a.get("url") or "",
                    author=(a.get("source") or {}).get("name") or "",
                    platform=self.platform,
                    engagement_count=0,
                    published_at=a.get("publishedAt"),
                    raw=a,
                ))
            return out
        except Exception as e:
            raise TrendSourceFetchError(str(e)) from e
