"""Trend source adapters — normalize external feeds into a common shape."""

from plugins.trend_intelligence.adapters.base import NormalizedTrend, TrendSourceAdapter
from plugins.trend_intelligence.adapters.newsapi import NewsAPIAdapter
from plugins.trend_intelligence.adapters.reddit import RedditAdapter
from plugins.trend_intelligence.adapters.twitter import TwitterAdapter
from plugins.trend_intelligence.adapters.rss import RSSAdapter

ADAPTERS = {
    "newsapi": NewsAPIAdapter,
    "reddit": RedditAdapter,
    "twitter": TwitterAdapter,
    "x": TwitterAdapter,
    "rss": RSSAdapter,
    "feed": RSSAdapter,
    "atom": RSSAdapter,
}


def get_adapter(platform: str) -> TrendSourceAdapter | None:
    key = (platform or "").lower().strip()
    cls = ADAPTERS.get(key)
    return cls() if cls else None


__all__ = [
    "NormalizedTrend",
    "TrendSourceAdapter",
    "NewsAPIAdapter",
    "RedditAdapter",
    "TwitterAdapter",
    "RSSAdapter",
    "ADAPTERS",
    "get_adapter",
]
