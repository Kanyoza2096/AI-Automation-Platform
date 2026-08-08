"""
Trend Intelligence Plugin v1.1 — Social media trend monitoring & smart posting.
Fetches trends, scores by brand relevance, generates AI images, routes to Facebook.
"""

import logging
import threading
import time
import os
import uuid
import base64
import requests
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

logger = logging.getLogger("kanyoza.plugins.trends")


class TrendIntelligencePlugin:
    """Fetches trending content, generates AI images, routes to brand pages."""

    def __init__(self, supabase_client=None, redis_client=None):
        self.supabase = supabase_client
        self.redis = redis_client
        self.name = "trend_intelligence"
        self._running = False
        self._thread = None
        self._scan_interval = 300

    def get_commands(self) -> List[Dict[str, Any]]:
        return [
            {"name": "trend_scan", "description": "Scan for trending topics now", "handler": self._handle_scan},
            {"name": "trend_status", "description": "Get trend intelligence status", "handler": self._handle_status},
        ]

    def get_webhooks(self) -> List[Dict[str, Any]]:
        return []

    def start(self):
        if self._running: return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="trend-engine")
        self._thread.start()
        logger.info("TrendIntelligence: started (scan every %ds)", self._scan_interval)

    def stop(self):
        self._running = False
        if self._thread: self._thread.join(timeout=5)
        logger.info("TrendIntelligence: stopped")

    def _loop(self):
        while self._running:
            try:
                self.scan_all_sources()
                self.process_trends()
            except Exception as e:
                logger.error("TrendIntelligence cycle error: %s", e)
            time.sleep(self._scan_interval)

    # ═══════════════════════════════════════════════════════════════════
    # TREND SCANNING
    # ═══════════════════════════════════════════════════════════════════

    def scan_all_sources(self):
        if not self.supabase: return
        sources = self.supabase.table("trend_sources").select("*").eq("enabled", True).execute()
        for source in (sources.data or []):
            try:
                posts = self._fetch_from_source(source)
                self._store_posts(source["id"], posts)
                self.supabase.table("trend_sources").update(
                    {"last_fetched": datetime.now(timezone.utc).isoformat()}
                ).eq("id", source["id"]).execute()
                logger.info("TrendIntelligence: %s — %d posts fetched", source["name"], len(posts))
            except Exception as e:
                logger.error("TrendIntelligence: %s failed: %s", source["name"], e)

    def _fetch_from_source(self, source: dict) -> List[dict]:
        platform = source["platform"]
        query = source["query"]
        if platform == "newsapi": return self._fetch_newsapi(query)
        elif platform == "reddit": return self._fetch_reddit(query)
        elif platform == "twitter": return self._fetch_twitter(query)
        return []

    def _fetch_newsapi(self, query: str) -> List[dict]:
        try:
            api_key = os.environ.get("NEWSAPI_KEY", "")
            if not api_key: return []
            url = f"https://newsapi.org/v2/everything?q={query}&sortBy=popularity&pageSize=5&apiKey={api_key}"
            resp = requests.get(url, timeout=10)
            if resp.status_code != 200: return []
            data = resp.json()
            posts = []
            for article in (data.get("articles", []) or [])[:5]:
                posts.append({
                    "title": article.get("title", ""),
                    "content": article.get("description", ""),
                    "url": article.get("url", ""),
                    "author": article.get("source", {}).get("name", ""),
                    "platform": "newsapi",
                    "engagement_count": 0,
                })
            return posts
        except Exception as e:
            logger.error("NewsAPI fetch failed: %s", e)
            return []

    def _fetch_reddit(self, query: str) -> List[dict]:
        try:
            client_id = os.environ.get("REDDIT_CLIENT_ID", "")
            client_secret = os.environ.get("REDDIT_CLIENT_SECRET", "")
            if not client_id: return []
            auth = requests.auth.HTTPBasicAuth(client_id, client_secret)
            headers = {"User-Agent": "KanyozaTrendBot/1.0"}
            token_resp = requests.post(
                "https://www.reddit.com/api/v1/access_token",
                data={"grant_type": "client_credentials"}, headers=headers, auth=auth, timeout=10
            )
            if token_resp.status_code != 200: return []
            token = token_resp.json().get("access_token", "")
            headers["Authorization"] = f"Bearer {token}"
            resp = requests.get(f"https://oauth.reddit.com/r/{query}/hot?limit=5", headers=headers, timeout=10)
            if resp.status_code != 200: return []
            posts = []
            for post in (resp.json().get("data", {}).get("children", []) or [])[:5]:
                data = post.get("data", {})
                posts.append({
                    "title": data.get("title", ""),
                    "content": data.get("selftext", "")[:300],
                    "url": f"https://reddit.com{data.get('permalink', '')}",
                    "author": f"u/{data.get('author', 'unknown')}",
                    "platform": "reddit",
                    "engagement_count": data.get("score", 0),
                })
            return posts
        except Exception as e:
            logger.error("Reddit fetch failed: %s", e)
            return []

    def _fetch_twitter(self, query: str) -> List[dict]:
        try:
            token = os.environ.get("TWITTER_BEARER_TOKEN", "")
            if not token: return []
            resp = requests.get(
                f"https://api.twitter.com/2/tweets/search/recent?query={query}&max_results=5&tweet.fields=public_metrics,author_id",
                headers={"Authorization": f"Bearer {token}"}, timeout=10
            )
            if resp.status_code != 200: return []
            posts = []
            for tweet in (resp.json().get("data", []) or [])[:5]:
                metrics = tweet.get("public_metrics", {})
                posts.append({
                    "title": tweet.get("text", "")[:200],
                    "content": tweet.get("text", ""),
                    "url": f"https://twitter.com/i/web/status/{tweet['id']}",
                    "author": tweet.get("author_id", ""),
                    "platform": "twitter",
                    "engagement_count": metrics.get("like_count", 0) + metrics.get("retweet_count", 0),
                })
            return posts
        except Exception as e:
            logger.error("Twitter fetch failed: %s", e)
            return []

    def _store_posts(self, source_id: str, posts: List[dict]):
        for post in posts:
            try:
                existing = self.supabase.table("trend_posts").select("id").eq("url", post.get("url", "")).limit(1).execute()
                if existing.data: continue
                self.supabase.table("trend_posts").insert({
                    "source_id": source_id, "title": post["title"], "content": post.get("content", ""),
                    "url": post.get("url", ""), "author": post.get("author", ""),
                    "platform": post.get("platform", "unknown"), "engagement_count": post.get("engagement_count", 0),
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                }).execute()
            except Exception as e:
                logger.debug("TrendIntelligence: duplicate or insert error: %s", e)

    # ═══════════════════════════════════════════════════════════════════
    # AI IMAGE GENERATION — Cloudflare Workers AI
    # ═══════════════════════════════════════════════════════════════════

    def _generate_trend_image(self, trend: dict) -> Optional[str]:
        """Generate AI image for a trending topic using Cloudflare Workers AI."""
        try:
            account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
            api_token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
            if not account_id or not api_token: return None

            prompt = f"Professional social media graphic about: {trend['title']}. Modern, clean, viral style, tech aesthetic."

            response = requests.post(
                f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/@cf/stabilityai/stable-diffusion-xl-base-1.0",
                headers={"Authorization": f"Bearer {api_token}"},
                json={"prompt": prompt},
                timeout=30,
            )

            if response.status_code == 200:
                data = response.json()
                if data.get("success") and data.get("result", {}).get("image"):
                    image_bytes = base64.b64decode(data["result"]["image"])
                    filename = f"trend_{uuid.uuid4().hex[:8]}.png"
                    self.supabase.storage.from_("trend-images").upload(
                        filename, image_bytes, {"content-type": "image/png"}
                    )
                    url = self.supabase.storage.from_("trend-images").get_public_url(filename)
                    logger.info("TrendIntelligence: Image generated — %s", url)
                    return url
            else:
                logger.warning("Cloudflare image gen failed: %s", response.text[:200])
        except Exception as e:
            logger.error("TrendIntelligence: Image generation error: %s", e)
        return None

    # ═══════════════════════════════════════════════════════════════════
    # BRAND ROUTING — The Smart Filter
    # ═══════════════════════════════════════════════════════════════════

    def process_trends(self):
        if not self.supabase: return
        trends = self.supabase.table("trend_posts").select("*").order("fetched_at", desc=True).limit(20).execute()
        rules = self.supabase.table("trend_brand_rules").select("*").eq("enabled", True).execute()
        for trend in (trends.data or []):
            existing = self.supabase.table("trend_decisions").select("id").eq("trend_post_id", trend["id"]).limit(1).execute()
            if existing.data: continue
            for rule in (rules.data or []):
                relevance = self._calculate_relevance(trend, rule)
                if relevance >= rule.get("min_relevance_score", 60):
                    decision = self._make_decision(trend, rule, relevance)
                    self._execute_decision(trend, rule, decision, relevance)

    def _calculate_relevance(self, trend: dict, rule: dict) -> float:
        text = f"{trend.get('title', '')} {trend.get('content', '')}".lower()
        keywords = rule.get("keywords", [])
        exclude = rule.get("exclude_keywords", [])
        if not keywords: return 0.0
        for kw in exclude:
            if kw.lower() in text: return 0.0
        matches = sum(1 for kw in keywords if kw.lower() in text)
        score = (matches / len(keywords)) * 100
        engagement = trend.get("engagement_count", 0)
        if engagement > 10000: score += 15
        elif engagement > 5000: score += 10
        elif engagement > 1000: score += 5
        return min(100, score)

    def _make_decision(self, trend: dict, rule: dict, relevance: float) -> str:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        count_result = self.supabase.table("trend_decisions").select("id").eq("brand_id", rule["brand_id"]).eq("decision", "posted").gte("decided_at", today).execute()
        if len(count_result.data or []) >= rule.get("max_posts_per_day", 3): return "skipped"
        if relevance >= 85: return "posted"
        elif relevance >= 70: return "scheduled"
        return "skipped"

    def _execute_decision(self, trend: dict, rule: dict, decision: str, relevance: float):
        reason = f"Relevance: {relevance:.0f}% — Keywords matched"
        self.supabase.table("trend_decisions").insert({
            "trend_post_id": trend["id"], "brand_id": rule["brand_id"],
            "decision": decision, "relevance_score": relevance,
            "reason": reason, "decided_at": datetime.now(timezone.utc).isoformat(),
        }).execute()

        if decision == "posted":
            image_url = self._generate_trend_image(trend)
            if image_url: trend["generated_image"] = image_url
            self._post_to_brand(trend, rule["brand_id"])
        elif decision == "scheduled":
            logger.info("TrendIntelligence: Scheduled for %s — %s", rule["brand_id"], trend["title"][:60])

        try:
            from services.director import director
            if director and hasattr(director, '_event'):
                director._event("trend_decision", {
                    "trend": trend["title"], "brand_id": rule["brand_id"],
                    "decision": decision, "relevance": relevance,
                })
        except Exception: pass

    def _post_to_brand(self, trend: dict, brand_id: str):
        try:
            from scheduler.brand_scheduler import BrandSchedulerManager
            from service_container import get_container
            container = get_container()
            if not container: return
            manager = container.try_resolve(BrandSchedulerManager)
            if not manager: return
            scheduler = manager._schedulers.get(brand_id)
            if not scheduler:
                manager._sync_brands()
                scheduler = manager._schedulers.get(brand_id)
            if scheduler:
                result = scheduler.trigger_manual()
                logger.info("TrendIntelligence: Posted to %s — %s", brand_id, result.get("topic", trend["title"][:60]))
                self.supabase.table("trend_page_posts").insert({
                    "trend_post_id": trend["id"], "content": trend["title"],
                    "posted_at": datetime.now(timezone.utc).isoformat(),
                }).execute()
            else:
                logger.warning("TrendIntelligence: No scheduler for brand %s", brand_id)
        except Exception as e:
            logger.error("TrendIntelligence: Post to brand failed: %s", e)

    # ═══════════════════════════════════════════════════════════════════
    # COMMAND HANDLERS
    # ═══════════════════════════════════════════════════════════════════

    def _handle_scan(self, params: dict) -> dict:
        self.scan_all_sources()
        self.process_trends()
        return {"ok": True, "message": "Trend scan triggered"}

    def _handle_status(self, params: dict) -> dict:
        if not self.supabase: return {"ok": False, "error": "Database unavailable"}
        sources = self.supabase.table("trend_sources").select("*").execute()
        recent = self.supabase.table("trend_posts").select("*").order("fetched_at", desc=True).limit(10).execute()
        decisions = self.supabase.table("trend_decisions").select("*").order("decided_at", desc=True).limit(20).execute()
        return {"ok": True, "sources": len(sources.data or []), "recent_trends": recent.data or [], "recent_decisions": decisions.data or []}


trend_plugin: Optional[TrendIntelligencePlugin] = None


def init_trend_plugin(supabase_client=None, redis_client=None) -> TrendIntelligencePlugin:
    global trend_plugin
    if trend_plugin is None:
        trend_plugin = TrendIntelligencePlugin(supabase_client, redis_client)
        trend_plugin.start()
    return trend_plugin
