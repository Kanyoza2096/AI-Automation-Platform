from __future__ import annotations

import logging
import os
from typing import List

from plugins.trend_intelligence.adapters.base import NormalizedTrend, TrendSourceAdapter, TrendSourceFetchError

logger = logging.getLogger("kanyoza.plugins.trends.twitter")


class TwitterAdapter(TrendSourceAdapter):
    platform = "twitter"

    def is_configured(self) -> bool:
        return bool(
            (os.environ.get("TWITTER_BEARER_TOKEN") or os.environ.get("TWITTER_TOKEN") or "").strip()
        )

    def fetch(self, query: str, **kwargs) -> List[NormalizedTrend]:
        import requests
        token = (
            os.environ.get("TWITTER_BEARER_TOKEN")
            or os.environ.get("TWITTER_TOKEN")
            or ""
        )
        if not token:
            return []
        try:
            r = requests.get(
                "https://api.twitter.com/2/tweets/search/recent",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "query": query or "technology",
                    "max_results": 10,
                    "tweet.fields": "public_metrics,author_id,created_at",
                },
                timeout=20,
            )
            if r.status_code != 200:
                raise TrendSourceFetchError(f"Twitter HTTP {r.status_code}")
            out: List[NormalizedTrend] = []
            for t in r.json().get("data") or []:
                metrics = t.get("public_metrics") or {}
                eng = int(metrics.get("like_count") or 0) + int(metrics.get("retweet_count") or 0)
                out.append(NormalizedTrend(
                    title=(t.get("text") or "")[:120],
                    content=t.get("text") or "",
                    url=f"https://twitter.com/i/web/status/{t.get('id')}",
                    author=t.get("author_id") or "",
                    platform=self.platform,
                    engagement_count=eng,
                    published_at=t.get("created_at"),
                    raw=t,
                ))
            return out
        except Exception as e:
            raise TrendSourceFetchError(str(e)) from e
