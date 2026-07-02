"""
Tech news scraper v8.0
Sources: RSS feeds → BeautifulSoup parsing → Google Trends.
Memory-efficient with bounded history.
"""

import logging
import random
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("kanyoza.services.news_scraper")

_MAX_POSTED_HISTORY = 500
_TRIM_TO = 200
_REQUEST_TIMEOUT = 12

_RSS_FEEDS = [
    {"url": "https://feeds.feedburner.com/TechCrunch", "name": "TechCrunch"},
    {"url": "https://www.theverge.com/rss/index.xml", "name": "The Verge"},
    {"url": "https://hnrss.org/frontpage", "name": "Hacker News"},
    {"url": "https://feeds.arstechnica.com/arstechnica/technology-lab", "name": "Ars Technica"},
    {"url": "https://www.wired.com/feed/rss", "name": "Wired"},
    {"url": "https://feeds.bbci.co.uk/news/technology/rss.xml", "name": "BBC Tech"},
    {"url": "https://techcrunch.com/feed/", "name": "TechCrunch RSS"},
    {"url": "https://dev.to/feed", "name": "Dev.to"},
    {"url": "https://feeds.bbci.co.uk/news/world/africa/rss.xml", "name": "BBC Africa"},
]

_TECH_SCRAPE_URLS = [
    "https://techcrunch.com",
    "https://dev.to",
    "https://news.ycombinator.com",
]


class NewsScraper:
    """Scrapes news from multiple sources."""

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "Mozilla/5.0 (compatible; KanyozaBot/1.0)"
        })
        self.posted_articles: List[str] = []
        self.last_scraped: Dict[str, datetime] = {}
        self._source_article_counts: Dict[str, List[int]] = {}

    def get_fresh_article(self) -> Optional[Dict]:
        """Return one unposted article dict."""
        articles = self._fetch_from_rss()
        if not articles:
            articles = self._scrape_pages()

        random.shuffle(articles)
        for article in articles:
            url = article.get("url", "")
            if url and url not in self.posted_articles:
                self._mark_posted(url)
                return article
        return None

    def scrape_all(self, category: Optional[str] = None) -> List[Dict]:
        """Scrape all sources, optionally filtered by category."""
        all_articles = []
        feeds = _RSS_FEEDS
        if category:
            feeds = [f for f in feeds if category in f.get("category", "")]

        for feed in feeds:
            last = self.last_scraped.get(feed["name"])
            if last and (datetime.now(timezone.utc) - last).total_seconds() < 1800:
                continue
            try:
                resp = self._session.get(feed["url"], timeout=_REQUEST_TIMEOUT)
                if resp.status_code == 200:
                    articles = self._parse_rss(resp.text, feed["name"])
                    all_articles.extend(articles)
                    self.last_scraped[feed["name"]] = datetime.now(timezone.utc)
            except Exception as e:
                logger.debug(f"RSS fetch failed for {feed['name']}: {e}")

        # Deduplicate
        seen = set()
        unique = []
        for a in all_articles:
            key = a.get("title", "").lower()
            if key not in seen and a.get("url") not in self.posted_articles:
                unique.append(a)
                seen.add(key)

        random.shuffle(unique)
        return unique

    def _fetch_from_rss(self) -> List[Dict]:
        feeds = list(_RSS_FEEDS)
        random.shuffle(feeds)
        for feed in feeds:
            try:
                resp = self._session.get(feed["url"], timeout=_REQUEST_TIMEOUT)
                if resp.status_code == 200:
                    articles = self._parse_rss(resp.text, feed["name"])
                    if articles:
                        return articles
            except Exception as e:
                logger.debug(f"RSS fetch failed for {feed['name']}: {e}")
        return []

    def _scrape_pages(self) -> List[Dict]:
        urls = list(_TECH_SCRAPE_URLS)
        random.shuffle(urls)
        for url in urls:
            try:
                resp = self._session.get(url, timeout=_REQUEST_TIMEOUT)
                if resp.status_code == 200:
                    articles = self._parse_html(resp.text, url)
                    if articles:
                        return articles
            except Exception as e:
                logger.debug(f"Page scrape failed for {url}: {e}")
        return []

    def _parse_rss(self, xml_text: str, source: str) -> List[Dict]:
        articles = []
        try:
            soup = BeautifulSoup(xml_text, "xml")
            items = soup.find_all("item") or soup.find_all("entry")
            for item in items[:10]:
                title = _text(item, ["title"])
                url = _text(item, ["link", "id"])
                summary = _clean(_text(item, ["description", "summary", "content"]))
                if title and url and len(url) > 10:
                    articles.append({
                        "title": title, "url": url,
                        "summary": summary[:400] if summary else "",
                        "source": source, "category": "technology",
                    })
        except Exception as e:
            logger.debug(f"RSS parse error: {e}")
        return articles

    def _parse_html(self, html: str, base_url: str) -> List[Dict]:
        articles = []
        try:
            soup = BeautifulSoup(html, "lxml")
            for tag in soup.find_all("a", href=True)[:50]:
                href = tag.get("href", "")
                if not href.startswith("http"):
                    href = urljoin(base_url, href)
                text = tag.get_text(strip=True)
                if len(text) > 30 and any(w in href.lower() for w in ["/article", "/post", "/story", "/news"]):
                    articles.append({
                        "title": text[:200], "url": href,
                        "summary": "", "source": _source_name(base_url),
                        "category": "technology",
                    })
                    if len(articles) >= 8:
                        break
        except Exception as e:
            logger.debug(f"HTML parse error: {e}")
        return articles

    def _mark_posted(self, url: str) -> None:
        self.posted_articles.append(url)
        if len(self.posted_articles) > _MAX_POSTED_HISTORY:
            self.posted_articles = self.posted_articles[-_TRIM_TO:]

    def post_news(self, category: str = None) -> bool:
        """Scrape and return one article for posting."""
        articles = self.scrape_all(category)
        for article in articles[:3]:
            url = article.get("url", "")
            if url and url not in self.posted_articles:
                self._mark_posted(url)
                # In the full flow, this calls Gemini + Facebook
                return True
        return False

    def close(self):
        self._session.close()


def _text(tag, names: list) -> str:
    for name in names:
        child = tag.find(name)
        if child:
            return child.get_text(strip=True)
    return ""


def _clean(text: str) -> str:
    if not text:
        return ""
    text = BeautifulSoup(text, "lxml").get_text(separator=" ")
    return re.sub(r"\s+", " ", text).strip()


def _source_name(url: str) -> str:
    for name in ["techcrunch", "theverge", "hackernews", "hnrss", "arstechnica",
                 "wired", "businessinsider", "dev.to", "hackernoon"]:
        if name in url.lower():
            return name.title()
    return url.split("//")[-1].split("/")[0]


# Singleton
news_scraper = NewsScraper()
