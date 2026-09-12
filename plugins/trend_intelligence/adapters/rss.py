from __future__ import annotations

import logging
from typing import List
from xml.etree import ElementTree as ET

from plugins.trend_intelligence.adapters.base import NormalizedTrend, TrendSourceAdapter

logger = logging.getLogger("kanyoza.plugins.trends.rss")


class RSSAdapter(TrendSourceAdapter):
    platform = "rss"

    def fetch(self, query: str, **kwargs) -> List[NormalizedTrend]:
        """`query` is expected to be a full feed URL."""
        import requests
        feed_url = (query or "").strip()
        if not feed_url.startswith("http"):
            logger.warning("RSS adapter expects a feed URL, got: %s", feed_url[:80])
            return []
        try:
            r = requests.get(feed_url, timeout=20, headers={"User-Agent": "KanyozaTrendBot/2.1"})
            if r.status_code != 200:
                logger.warning("RSS HTTP %s for %s", r.status_code, feed_url[:80])
                return []
            root = ET.fromstring(r.content)
            # RSS 2.0 channel/item or Atom entry
            items = root.findall(".//item")
            if not items:
                # Atom
                ns = {"a": "http://www.w3.org/2005/Atom"}
                entries = root.findall(".//{http://www.w3.org/2005/Atom}entry") or root.findall(".//entry")
                out: List[NormalizedTrend] = []
                for e in entries[:10]:
                    title = (e.findtext("{http://www.w3.org/2005/Atom}title") or e.findtext("title") or "").strip()
                    link_el = e.find("{http://www.w3.org/2005/Atom}link") or e.find("link")
                    href = ""
                    if link_el is not None:
                        href = link_el.get("href") or (link_el.text or "")
                    summary = (
                        e.findtext("{http://www.w3.org/2005/Atom}summary")
                        or e.findtext("summary")
                        or ""
                    )
                    out.append(NormalizedTrend(
                        title=title,
                        content=summary[:500],
                        url=href,
                        author="",
                        platform=self.platform,
                    ))
                return out

            out = []
            for item in items[:10]:
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                desc = (item.findtext("description") or "")[:500]
                author = (item.findtext("author") or item.findtext("dc:creator") or "")
                out.append(NormalizedTrend(
                    title=title,
                    content=desc,
                    url=link,
                    author=author,
                    platform=self.platform,
                    published_at=item.findtext("pubDate"),
                ))
            return out
        except Exception as e:
            logger.error("RSS fetch failed: %s", e)
            return []
