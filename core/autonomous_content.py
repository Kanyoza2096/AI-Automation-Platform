"""
Autonomous Content Engine — Self-driving content generation.

v1.0 — The system NEVER stops posting.
- Fetches trends from internet
- Selects best topics for each brand
- Generates content immediately
- Publishes without waiting for engagement
- Learns passively from results

Key principle: POSTING IS ALWAYS ON.
Engagement is feedback, not a requirement.
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger("kanyoza.autonomous_content")


class AutonomousContentEngine:
    """Self-driving content generation and publishing."""

    # ═══════════════════════════════════════════════════════════════
    # CONFIGURATION
    # ═══════════════════════════════════════════════════════════════

    POST_INTERVAL_MINUTES = 60          # Post at least every 60 minutes
    TREND_CHECK_INTERVAL = 15           # Check trends every 15 minutes
    MAX_POSTS_PER_DAY = 10              # Don't exceed 10 posts/day per brand
    MIN_ENGAGEMENT_THRESHOLD = 5        # Below this = consider changing

    # Fallback topics (NEVER empty — always something to post)
    FALLBACK_TOPICS = {
        "Spiritual Teacher": [
            "Daily Meditation for Inner Peace",
            "Finding Purpose Through Mindfulness",
            "Spiritual Wisdom for Modern Life",
            "Overcoming Anxiety with Spirituality",
            "The Power of Gratitude",
            "Living with Intention",
            "Understanding Karma",
            "Mindful Living Tips",
        ],
        "Dev Comparisons": [
            "Python vs JavaScript: Which to Learn First",
            "React vs Vue: Framework Comparison",
            "SQL vs NoSQL: Database Choices",
            "Docker vs Kubernetes: Container Wars",
            "TypeScript vs JavaScript: Is It Worth It",
            "AWS vs Azure vs GCP: Cloud Comparison",
            "AI Tools for Developers in 2026",
            "Best Code Editors Compared",
        ],
        "default": [
            "Industry Insights for Today",
            "Innovation in Technology",
            "Digital Transformation Trends",
            "Future of Work",
            "Tech Skills for 2026",
        ],
    }

    def __init__(
        self,
        supabase_client=None,
        shared_bus=None,
        container=None,
    ):
        self.supabase = supabase_client
        self.shared_bus = shared_bus
        self.container = container

        self._running = False
        self._thread = None
        self._last_post_time: Dict[str, datetime] = {}
        self._posts_today: Dict[str, int] = {}
        self._used_topics: Dict[str, List[str]] = {}
        self._engagement_data: Dict[str, List[Dict]] = {}

    # ═══════════════════════════════════════════════════════════════
    # LIFECYCLE
    # ═══════════════════════════════════════════════════════════════

    def start(self) -> None:
        """Start the autonomous content loop."""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._loop,
            name="autonomous-content",
            daemon=True,
        )
        self._thread.start()
        logger.info("🚀 Autonomous Content Engine started — will never stop posting")

    def stop(self) -> None:
        """Stop the loop."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        self._thread = None

    # ═══════════════════════════════════════════════════════════════
    # MAIN LOOP
    # ═══════════════════════════════════════════════════════════════

    def _loop(self) -> None:
        """Main loop — continuously checks and posts."""
        while self._running:
            try:
                self._tick()
            except Exception:
                logger.exception("Autonomous Content Loop error")
            time.sleep(60)  # Check every minute

    def _tick(self) -> None:
        """One iteration — check if we should post."""
        brands = self._get_active_brands()

        for brand in brands:
            brand_id = brand.get("id", "")
            brand_name = brand.get("name", "default")

            # Check if it's time to post for this brand
            if self._should_post(brand_id):
                self._generate_and_publish(brand_id, brand_name)

    def _should_post(self, brand_id: str) -> bool:
        """Determine if it's time to post for this brand."""
        now = datetime.now(timezone.utc)

        # Check if we've already posted this hour
        last_post = self._last_post_time.get(brand_id)
        if last_post:
            minutes_since = (now - last_post).total_seconds() / 60
            if minutes_since < self.POST_INTERVAL_MINUTES:
                return False

        # Check daily limit
        today = now.strftime("%Y-%m-%d")
        posts_today = self._posts_today.get(f"{brand_id}:{today}", 0)
        if posts_today >= self.MAX_POSTS_PER_DAY:
            return False

        return True

    # ═══════════════════════════════════════════════════════════════
    # TOPIC SELECTION
    # ═══════════════════════════════════════════════════════════════

    def _get_active_brands(self) -> List[Dict[str, Any]]:
        """Get all active brands from Supabase."""
        if not self.supabase:
            return []

        try:
            result = self.supabase.table("brands") \
                .select("*") \
                .eq("status", "active") \
                .execute()
            return result.data or []
        except Exception as exc:
            logger.error("Failed to fetch brands: %s", exc)
            return []

    def _select_topic(self, brand_id: str, brand_name: str) -> str:
        """Select the best topic for this brand.

        Priority:
        1. Trending topics (from internet)
        2. Brand's custom topics
        3. Previously successful topics
        4. Fallback topics (NEVER empty)
        """
        # 1. Try trending topics
        trending = self._get_trending_topics(brand_name)
        if trending:
            topic = random.choice(trending)
            if self._is_unused(brand_id, topic):
                return topic

        # 2. Try custom topics
        custom = self._get_custom_topics(brand_id)
        if custom:
            topic = random.choice(custom)
            if self._is_unused(brand_id, topic):
                return topic

        # 3. Try previously successful topics
        successful = self._get_successful_topics(brand_id)
        if successful:
            topic = random.choice(successful)
            if self._is_unused(brand_id, topic):
                return topic

        # 4. Fallback topics (guaranteed)
        fallback = self.FALLBACK_TOPICS.get(brand_name, self.FALLBACK_TOPICS["default"])
        topic = random.choice(fallback)
        return topic

    def _get_trending_topics(self, brand_name: str) -> List[str]:
        """Fetch trending topics from Supabase (populated by TrendIntelligence)."""
        if not self.supabase:
            return []

        try:
            result = self.supabase.table("trend_posts") \
                .select("title") \
                .order("fetched_at", desc=True) \
                .limit(10) \
                .execute()

            topics = []
            for row in result.data or []:
                title = row.get("title", "")
                if title:
                    topics.append(title)
            return topics
        except Exception:
            return []

    def _get_custom_topics(self, brand_id: str) -> List[str]:
        """Get brand's custom topics."""
        if not self.supabase:
            return []

        try:
            result = self.supabase.table("brands") \
                .select("custom_topics") \
                .eq("id", brand_id) \
                .execute()

            if result.data:
                topics = result.data[0].get("custom_topics", [])
                if isinstance(topics, list):
                    return topics
                if isinstance(topics, str):
                    return json.loads(topics)
            return []
        except Exception:
            return []

    def _get_successful_topics(self, brand_id: str) -> List[str]:
        """Get topics that previously got engagement."""
        engagement = self._engagement_data.get(brand_id, [])
        successful = [
            e.get("topic", "") for e in engagement
            if e.get("engagement", 0) >= self.MIN_ENGAGEMENT_THRESHOLD
        ]
        return successful

    def _is_unused(self, brand_id: str, topic: str) -> bool:
        """Check if topic was already used recently."""
        used = self._used_topics.get(brand_id, [])
        return topic not in used

    # ═══════════════════════════════════════════════════════════════
    # CONTENT GENERATION & PUBLISHING
    # ═══════════════════════════════════════════════════════════════

    def _generate_and_publish(self, brand_id: str, brand_name: str) -> None:
        """Generate content for topic and publish."""
        topic = self._select_topic(brand_id, brand_name)

        logger.info(f"📝 Generating content for '{brand_name}' — topic: {topic}")

        # Record that we're using this topic
        self._used_topics.setdefault(brand_id, []).append(topic)

        # Publish to SharedEventBus for Workers to handle
        if self.shared_bus:
            self.shared_bus.publish("publish_request", {
                "brand_id": brand_id,
                "brand_name": brand_name,
                "topic": topic,
                "source": "autonomous_content_engine",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

        # Update tracking
        now = datetime.now(timezone.utc)
        self._last_post_time[brand_id] = now
        today = now.strftime("%Y-%m-%d")
        key = f"{brand_id}:{today}"
        self._posts_today[key] = self._posts_today.get(key, 0) + 1

        logger.info(f"✅ Published request sent for '{brand_name}' — {topic}")

    # ═══════════════════════════════════════════════════════════════
    # LEARNING (PASSIVE)
    # ═══════════════════════════════════════════════════════════════

    def record_engagement(
        self,
        brand_id: str,
        topic: str,
        engagement: int,
    ) -> None:
        """Record engagement passively — used for FUTURE topic selection."""
        self._engagement_data.setdefault(brand_id, []).append({
            "topic": topic,
            "engagement": engagement,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        # Keep only last 100 entries
        if len(self._engagement_data.get(brand_id, [])) > 100:
            self._engagement_data[brand_id] = self._engagement_data[brand_id][-100:]

    def get_status(self) -> Dict[str, Any]:
        """Get autonomous content engine status."""
        return {
            "running": self._running,
            "posts_today": sum(self._posts_today.values()),
            "topics_used": sum(len(v) for v in self._used_topics.values()),
            "engagement_data_points": sum(len(v) for v in self._engagement_data.values()),
        }


# ═══════════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════════

_autonomous_content: Optional[AutonomousContentEngine] = None


def init_autonomous_content(
    supabase_client=None,
    shared_bus=None,
    container=None,
) -> AutonomousContentEngine:
    """Initialize the autonomous content engine."""
    global _autonomous_content

    _autonomous_content = AutonomousContentEngine(
        supabase_client=supabase_client,
        shared_bus=shared_bus,
        container=container,
    )
    _autonomous_content.start()
    return _autonomous_content


def get_autonomous_content() -> Optional[AutonomousContentEngine]:
    """Get the autonomous content engine singleton."""
    return _autonomous_content


__all__ = [
    "AutonomousContentEngine",
    "init_autonomous_content",
    "get_autonomous_content",
]
