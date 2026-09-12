from __future__ import annotations

import logging
from typing import List

from plugins.trend_intelligence.adapters.base import NormalizedTrend, TrendSourceAdapter

logger = logging.getLogger("kanyoza.plugins.trends.reddit")


class RedditAdapter(TrendSourceAdapter):
    platform = "reddit"

    def fetch(self, query: str, **kwargs) -> List[NormalizedTrend]:
        import requests
        sub = (query or "technology").replace("r/", "").strip() or "technology"
        url = f"https://www.reddit.com/r/{sub}/hot.json?limit=10"
        try:
            r = requests.get(url, headers={"User-Agent": "KanyozaTrendBot/2.1"}, timeout=20)
            if r.status_code != 200:
                logger.warning("Reddit HTTP %s", r.status_code)
                return []
            out: List[NormalizedTrend] = []
            for child in (r.json().get("data") or {}).get("children") or []:
                d = child.get("data") or {}
                out.append(NormalizedTrend(
                    title=d.get("title") or "",
                    content=(d.get("selftext") or "")[:500],
                    url=f"https://reddit.com{d.get('permalink', '')}",
                    author=d.get("author") or "",
                    platform=self.platform,
                    engagement_count=int(d.get("score") or 0),
                    published_at=None,
                    raw=d,
                ))
            return out
        except Exception as e:
            logger.error("Reddit fetch failed: %s", e)
            return []
