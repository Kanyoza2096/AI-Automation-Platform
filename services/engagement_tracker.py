"""
KANYOZA ENGAGEMENT TRACKER — v3.0 (Async-Native)
=================================================

Fetches, stores, and analyzes social media engagement metrics.
Feeds data into the Condition Engine for autonomous decision-making.

v3.0 CHANGES:
  - FULLY ASYNC-NATIVE (asyncio.Task, asyncio.Lock)
  - UnifiedEventBus integration
  - HealthProvider protocol support
  - All v2.0 features preserved (Condition Engine observe() fix,
    EventBus publishing, configurable interval, batch token resolution)

v2.0 UPGRADES (preserved):
  - Fixed Condition Engine integration (observe, not evaluate)
  - EventBus publishing for Meta Engine / Director observability
  - Configurable tracking interval
  - Batch access token resolution
  - Health check & statistics
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger("kanyoza.services.engagement_tracker")


# ═══════════════════════════════════════════════════════════════════════════
# MODELS (Preserved)
# ═══════════════════════════════════════════════════════════════════════════

class PostEngagement(BaseModel):
    """Engagement metrics for a single published post."""

    id: str
    post_id: str
    brand_id: str
    platform: str = "facebook"
    platform_post_id: Optional[str] = None

    likes: int = 0
    comments: int = 0
    shares: int = 0
    impressions: int = 0
    reach: int = 0
    clicks: int = 0

    total_engagement: int = 0
    engagement_rate: float = 0.0

    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    post_created_at: Optional[datetime] = None

    def compute_totals(self):
        """Compute derived engagement metrics."""
        self.total_engagement = self.likes + self.comments + self.shares + self.clicks
        if self.impressions > 0:
            self.engagement_rate = round((self.total_engagement / self.impressions) * 100, 2)


class BrandEngagementProfile(BaseModel):
    """Rolling engagement profile for a brand."""

    brand_id: str
    avg_likes: float = 0.0
    avg_comments: float = 0.0
    avg_shares: float = 0.0
    avg_total_engagement: float = 0.0
    avg_engagement_rate: float = 0.0
    post_count: int = 0
    last_updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ═══════════════════════════════════════════════════════════════════════════
# ENGAGEMENT TRACKER v3.0 — FULLY ASYNC
# ═══════════════════════════════════════════════════════════════════════════

class EngagementTracker:
    """Tracks social media engagement and feeds data to the Condition Engine — ASYNC-NATIVE."""

    def __init__(
        self,
        supabase_client=None,
        condition_engine=None,
        event_bus=None,
        container=None,
    ):
        self.supabase = supabase_client
        self.condition_engine = condition_engine
        self.event_bus = event_bus
        self.container = container
        self._profiles: Dict[str, BrandEngagementProfile] = {}
        self._access_token_cache: Dict[str, str] = {}
        self._cycles_completed = 0
        self._posts_analyzed = 0
        self._running = False
        self._tracker_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._lock = asyncio.Lock()

    # ═══════════════════════════════════════════════════════════════
    # HEALTH PROVIDER PROTOCOL (v3.0)
    # ═══════════════════════════════════════════════════════════════

    @property
    def service_name(self) -> str:
        return "engagement_tracker"

    async def check_health_async(self) -> dict:
        stats = self.get_stats()
        return {
            "status": "healthy" if self._running else "stopped",
            "cycles_completed": stats["cycles_completed"],
            "posts_analyzed": stats["posts_analyzed"],
            "cached_brands": stats["cached_brands"],
        }

    # ═══════════════════════════════════════════════════════════════
    # EVENT BUS (async)
    # ═══════════════════════════════════════════════════════════════

    async def _emit(self, event_name: str, payload: dict) -> None:
        """Publish an event for the Meta Engine and Director."""
        if not self.event_bus:
            return
        try:
            result = self.event_bus.publish(event_name, payload, source="engagement_tracker")
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            logger.debug("Engagement tracker event failed: %s", exc)

    # ═══════════════════════════════════════════════════════════════
    # DATA FETCHING (async — offloaded to executor)
    # ═══════════════════════════════════════════════════════════════

    async def fetch_post_insights(self, platform_post_id: str, access_token: str) -> Optional[dict]:
        """Fetch engagement metrics from Facebook Graph API."""
        try:
            import requests
            loop = asyncio.get_event_loop()

            url = f"https://graph.facebook.com/v19.0/{platform_post_id}/insights"
            params = {
                "metric": "post_impressions,post_engaged_users,post_reactions_like_total,post_reactions_love_total,post_reactions_wow_total,post_clicks",
                "access_token": access_token,
            }

            response = await loop.run_in_executor(
                None, lambda: requests.get(url, params=params, timeout=15)
            )
            if response.status_code != 200:
                logger.warning("Facebook insights fetch failed: %s", response.status_code)
                return None

            data = response.json().get("data", [])
            metrics = {}
            for item in data:
                name = item.get("name", "")
                values = item.get("values", [])
                if values:
                    metrics[name] = values[0].get("value", 0)

            shares_url = f"https://graph.facebook.com/v19.0/{platform_post_id}"
            shares_params = {"fields": "shares", "access_token": access_token}
            shares_resp = await loop.run_in_executor(
                None, lambda: requests.get(shares_url, params=shares_params, timeout=10)
            )
            if shares_resp.status_code == 200:
                shares_data = shares_resp.json()
                metrics["shares"] = shares_data.get("shares", {}).get("count", 0)

            return {
                "likes": metrics.get("post_reactions_like_total", 0),
                "comments": metrics.get("post_engaged_users", 0),
                "shares": metrics.get("shares", 0),
                "impressions": metrics.get("post_impressions", 0),
                "clicks": metrics.get("post_clicks", 0),
            }

        except Exception as exc:
            logger.error("Facebook insights fetch error: %s", exc)
            return None

    async def fetch_recent_engagement(self, hours_back: int = 24) -> List[PostEngagement]:
        """Fetch engagement for posts published in last N hours."""
        engagements = []

        if not self.supabase:
            logger.debug("No Supabase client — skipping engagement fetch")
            return engagements

        try:
            loop = asyncio.get_event_loop()
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).isoformat()
            response = await loop.run_in_executor(
                None,
                lambda: self.supabase.table("posts").select("*").gte("created_at", cutoff).execute(),
            )

            posts = response.data if response.data else []

            brand_ids = set(post.get("brand_id", "") for post in posts)
            for bid in brand_ids:
                if bid:
                    await self._get_brand_access_token(bid)

            for post in posts:
                engagement = await self._get_or_fetch_engagement(post)
                if engagement:
                    engagement.compute_totals()
                    engagements.append(engagement)
                    await self._store_engagement(engagement)

            logger.info("Fetched engagement for %d posts (last %dh)", len(engagements), hours_back)

        except Exception as exc:
            logger.error("Engagement fetch failed: %s", exc)

        return engagements

    async def _get_or_fetch_engagement(self, post: dict) -> Optional[PostEngagement]:
        """Get cached engagement or fetch from Facebook."""
        post_id = post.get("id", "")
        platform_post_id = post.get("platform_post_id", "")
        brand_id = post.get("brand_id", "")

        if not platform_post_id:
            return None

        # Check cache
        if self.supabase:
            try:
                loop = asyncio.get_event_loop()
                cached = await loop.run_in_executor(
                    None,
                    lambda: self.supabase.table("post_engagement").select("*").eq("post_id", post_id).order("fetched_at", desc=True).limit(1).execute(),
                )
                if cached.data and len(cached.data) > 0:
                    latest = cached.data[0]
                    fetched_at = latest.get("fetched_at", "")
                    if fetched_at:
                        fetched_time = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
                        if datetime.now(timezone.utc) - fetched_time < timedelta(hours=1):
                            return PostEngagement(**latest)
            except Exception:
                pass

        access_token = await self._get_brand_access_token(brand_id)
        if not access_token:
            return None

        insights = await self.fetch_post_insights(platform_post_id, access_token)
        if not insights:
            return None

        return PostEngagement(
            id=f"eng_{post_id}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}",
            post_id=post_id,
            brand_id=brand_id,
            platform=post.get("platform", "facebook"),
            platform_post_id=platform_post_id,
            likes=insights.get("likes", 0),
            comments=insights.get("comments", 0),
            shares=insights.get("shares", 0),
            impressions=insights.get("impressions", 0),
            clicks=insights.get("clicks", 0),
            post_created_at=post.get("created_at"),
        )

    async def _get_brand_access_token(self, brand_id: str) -> Optional[str]:
        """Get Facebook access token for a brand (with async cache)."""
        async with self._lock:
            if brand_id in self._access_token_cache:
                return self._access_token_cache[brand_id]

        if not self.supabase:
            return None

        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self.supabase.table("social_accounts").select("access_token").eq("brand_id", brand_id).eq("platform", "facebook").limit(1).execute(),
            )
            if response.data and len(response.data) > 0:
                token = response.data[0].get("access_token")
                async with self._lock:
                    self._access_token_cache[brand_id] = token
                return token
        except Exception as exc:
            logger.warning("Failed to get brand access token: %s", exc)
        return None

    async def _store_engagement(self, engagement: PostEngagement):
        """Store engagement data in Supabase."""
        if not self.supabase:
            return
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.supabase.table("post_engagement").upsert(engagement.model_dump(mode="json")).execute(),
            )
        except Exception as exc:
            logger.debug("Failed to store engagement: %s", exc)

    # ═══════════════════════════════════════════════════════════════
    # ANALYTICS (async)
    # ═══════════════════════════════════════════════════════════════

    async def get_brand_profile(self, brand_id: str) -> BrandEngagementProfile:
        """Calculate average engagement metrics for a brand."""
        if brand_id in self._profiles:
            return self._profiles[brand_id]

        profile = BrandEngagementProfile(brand_id=brand_id)

        if not self.supabase:
            return profile

        try:
            loop = asyncio.get_event_loop()
            cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
            response = await loop.run_in_executor(
                None,
                lambda: self.supabase.table("post_engagement").select("*").eq("brand_id", brand_id).gte("fetched_at", cutoff).execute(),
            )

            if response.data and len(response.data) > 0:
                engagements = [PostEngagement(**e) for e in response.data]
                for e in engagements:
                    e.compute_totals()

                count = len(engagements)
                profile.post_count = count
                profile.avg_likes = round(sum(e.likes for e in engagements) / count, 1)
                profile.avg_comments = round(sum(e.comments for e in engagements) / count, 1)
                profile.avg_shares = round(sum(e.shares for e in engagements) / count, 1)
                profile.avg_total_engagement = round(sum(e.total_engagement for e in engagements) / count, 1)
                profile.avg_engagement_rate = round(sum(e.engagement_rate for e in engagements) / count, 2)

        except Exception as exc:
            logger.warning("Failed to compute brand profile: %s", exc)

        self._profiles[brand_id] = profile
        return profile

    async def calculate_engagement_ratio(self, engagement: PostEngagement) -> float:
        """Calculate engagement vs brand average."""
        profile = await self.get_brand_profile(engagement.brand_id)
        if profile.avg_total_engagement <= 0:
            return 1.0
        return round(engagement.total_engagement / profile.avg_total_engagement, 2)

    # ═══════════════════════════════════════════════════════════════
    # CONDITION ENGINE INTEGRATION (async — FIXED from v2.0)
    # ═══════════════════════════════════════════════════════════════

    async def evaluate_rules(self):
        """Analyze recent engagement and feed into Condition Engine (async)."""
        if not self.condition_engine:
            logger.debug("No Condition Engine — skipping rule evaluation")
            return

        engagements = await self.fetch_recent_engagement(hours_back=24)

        for engagement in engagements:
            engagement.compute_totals()
            ratio = await self.calculate_engagement_ratio(engagement)

            from services.condition_engine import Observation

            observation = Observation(
                post_id=engagement.post_id,
                brand_id=engagement.brand_id,
                likes=engagement.likes,
                comments=engagement.comments,
                shares=engagement.shares,
                clicks=engagement.clicks,
                impressions=engagement.impressions,
                reach=engagement.reach,
                engagement=engagement.total_engagement,
                engagement_rate=engagement.engagement_rate,
                metadata={
                    "ratio": ratio,
                    "platform": engagement.platform,
                    "post_created_at": engagement.post_created_at.isoformat() if engagement.post_created_at else None,
                },
            )

            # Condition Engine is async-native — await observe()
            result = self.condition_engine.observe(observation)
            if asyncio.iscoroutine(result):
                result = await result

            if result:
                async with self._lock:
                    self._posts_analyzed += 1

                decision = result.get("decision", {})
                if isinstance(decision, dict):
                    state = decision.get("state", "observe")
                    if state != "observe":
                        logger.info("Engagement triggered action for post %s (ratio=%.1fx, state=%s)", engagement.post_id[:12], ratio, state)

                await self._emit("engagement.analyzed", {
                    "post_id": engagement.post_id,
                    "brand_id": engagement.brand_id,
                    "ratio": ratio,
                    "total_engagement": engagement.total_engagement,
                    "state": decision.get("state", "observe") if isinstance(decision, dict) else "observe",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

    async def check_dead_brands(self):
        """Check for brands that haven't posted recently (async)."""
        if not self.condition_engine or not self.supabase:
            return

        try:
            loop = asyncio.get_event_loop()
            brands_response = await loop.run_in_executor(
                None,
                lambda: self.supabase.table("brands").select("id,name").eq("status", "active").execute(),
            )

            if not brands_response.data:
                return

            for brand in brands_response.data:
                brand_id = brand.get("id")

                try:
                    posts_response = await loop.run_in_executor(
                        None,
                        lambda: self.supabase.table("posts").select("created_at").eq("brand_id", brand_id).order("created_at", desc=True).limit(1).execute(),
                    )
                except Exception as e:
                    logger.warning(f"Failed to fetch posts for brand {brand_id}: {e}")
                    continue

                days_since = 0
                if posts_response.data and len(posts_response.data) > 0:
                    last_post = posts_response.data[0].get("created_at", "")
                    if last_post:
                        try:
                            last_date = datetime.fromisoformat(last_post.replace("Z", "+00:00"))
                            days_since = (datetime.now(timezone.utc) - last_date).days
                        except Exception:
                            pass

                from services.condition_engine import Observation

                observation = Observation(
                    post_id=f"brand_check_{brand_id}",
                    brand_id=brand_id,
                    metadata={
                        "event": "scheduled_tick",
                        "brand_name": brand.get("name", ""),
                        "days_since_last_post": days_since,
                    },
                )

                result = self.condition_engine.observe(observation)
                if asyncio.iscoroutine(result):
                    result = await result

                if days_since > 7:
                    logger.warning("Brand %s inactive for %d days", brand_id, days_since)
                    await self._emit("brand.inactive", {
                        "brand_id": brand_id,
                        "brand_name": brand.get("name", ""),
                        "days_since_last_post": days_since,
                    })

        except Exception as exc:
            logger.error("Dead brand check failed: %s", exc)

    # ═══════════════════════════════════════════════════════════════
    # LIFECYCLE (async)
    # ═══════════════════════════════════════════════════════════════

    async def start(self, interval_seconds: int = 1800) -> None:
        """Start periodic engagement tracking (async)."""
        if self._running:
            return
        self._running = True
        self._stop_event = asyncio.Event()
        self._tracker_task = asyncio.create_task(
            self._tracking_loop(interval_seconds),
            name="engagement-tracker",
        )
        logger.info("Engagement tracker started (every %dmin)", interval_seconds // 60)

    async def stop(self) -> None:
        """Stop tracking."""
        self._running = False
        self._stop_event.set()
        if self._tracker_task and not self._tracker_task.done():
            self._tracker_task.cancel()
            try:
                await self._tracker_task
            except asyncio.CancelledError:
                pass

    async def _tracking_loop(self, interval_seconds: int) -> None:
        """Run engagement checks on configured interval (async)."""
        while self._running:
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval_seconds)
            except asyncio.TimeoutError:
                pass

            if not self._running:
                break

            try:
                logger.info("Engagement tracker: starting cycle")
                await self.evaluate_rules()
                await self.check_dead_brands()
                async with self._lock:
                    self._cycles_completed += 1
                logger.info("Engagement tracker: cycle complete (%d posts analyzed)", self._posts_analyzed)
            except Exception as exc:
                logger.error("Engagement tracker error: %s", exc)

    # ═══════════════════════════════════════════════════════════════
    # STATISTICS (preserved)
    # ═══════════════════════════════════════════════════════════════

    def get_stats(self) -> dict:
        """Return tracker statistics."""
        return {
            "cycles_completed": self._cycles_completed,
            "posts_analyzed": self._posts_analyzed,
            "cached_brands": len(self._access_token_cache),
            "profiles_computed": len(self._profiles),
        }


# ═══════════════════════════════════════════════════════════════════════════
# MODULE SINGLETON + STARTUP (async)
# ═══════════════════════════════════════════════════════════════════════════

engagement_tracker: Optional[EngagementTracker] = None


async def start_engagement_tracking(container=None, interval_minutes: int = 30) -> EngagementTracker:
    """Start periodic engagement tracking (async). Call from lifecycle boot."""
    global engagement_tracker

    supabase = None
    condition_engine = None
    event_bus = None

    if container:
        try:
            from services.supabase import get_service_client_from_settings
            supabase = get_service_client_from_settings()
        except Exception:
            pass

        try:
            from services.condition_engine import autonomous_engine
            condition_engine = autonomous_engine
        except Exception:
            pass

        try:
            from core.unified_bus import UnifiedEventBus
            event_bus = container.try_resolve(UnifiedEventBus)
        except Exception:
            pass

    engagement_tracker = EngagementTracker(
        supabase_client=supabase,
        condition_engine=condition_engine,
        event_bus=event_bus,
        container=container,
    )

    await engagement_tracker.start(interval_seconds=max(60, interval_minutes * 60))
    logger.info("Engagement tracker started (every %dmin)", interval_minutes)

    return engagement_tracker


async def shutdown_engagement_tracking() -> None:
    """Stop engagement tracking."""
    global engagement_tracker
    if engagement_tracker is not None:
        await engagement_tracker.stop()
        engagement_tracker = None


__all__ = [
    "EngagementTracker",
    "PostEngagement",
    "BrandEngagementProfile",
    "engagement_tracker",
    "start_engagement_tracking",
    "shutdown_engagement_tracking",
]