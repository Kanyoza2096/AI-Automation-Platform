"""
Autonomous Content Engine — Self-driving content generation with brand relevance.

v1.1 — Brand-aware topic selection:
  - Fetches trends from internet
  - Filters by brand-specific keywords
  - Generates brand-relevant topics inspired by trends
  - Falls back to brand-appropriate topics when no trends match
  - Never stops posting

Key principle: POSTING IS ALWAYS ON.
Engagement is feedback, not a requirement.
Topics are ALWAYS relevant to the brand.
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
    """Self-driving content generation with brand relevance."""

    # ═══════════════════════════════════════════════════════════════
    # CONFIGURATION
    # ═══════════════════════════════════════════════════════════════

    POST_INTERVAL_MINUTES = 60          # Post at least every 60 minutes
    MAX_POSTS_PER_DAY = 10              # Don't exceed 10 posts/day per brand
    MIN_ENGAGEMENT_THRESHOLD = 5        # Below this = consider changing

    # ═══════════════════════════════════════════════════════════════
    # BRAND-SPECIFIC KEYWORDS
    # ═══════════════════════════════════════════════════════════════

    BRAND_KEYWORDS = {
        "Spiritual Teacher": [
            "meditation", "mindfulness", "spiritual", "peace", "wisdom",
            "gratitude", "inner", "soul", "karma", "consciousness",
            "healing", "prayer", "divine", "faith", "enlightenment",
            "love", "compassion", "breath", "stillness", "awareness",
            "present", "energy", "universe", "spirit", "sacred",
            "teacher", "guidance", "purpose", "meaning", "growth",
        ],
        "Dev Comparisons": [
            "python", "javascript", "react", "vue", "typescript",
            "docker", "kubernetes", "aws", "azure", "gcp",
            "comparison", "vs", "versus", "best", "top",
            "programming", "coding", "developer", "framework", "database",
            "api", "frontend", "backend", "cloud", "devops",
            "node", "golang", "rust", "java", "csharp",
            "ai", "machine learning", "software", "web", "mobile",
        ],
        "default": [
            "technology", "innovation", "digital", "future", "ai",
            "business", "growth", "success", "trends", "skills",
        ],
    }

    # ═══════════════════════════════════════════════════════════════
    # BRAND-SPECIFIC TOPIC TEMPLATES
    # ═══════════════════════════════════════════════════════════════

    BRAND_TOPIC_TEMPLATES = {
        "Spiritual Teacher": [
            "Finding Inner Peace Through {keyword}",
            "The Spiritual Side of {keyword}",
            "Mindfulness in the Age of {keyword}",
            "Ancient Wisdom for Modern {keyword}",
            "How {keyword} Connects to Your Spiritual Journey",
            "Practicing Presence Amidst {keyword}",
            "The Sacred in {keyword}",
            "Spiritual Lessons from {keyword}",
            "Awakening Through {keyword}",
            "The Path of {keyword} and Inner Growth",
        ],
        "Dev Comparisons": [
            "{keyword}: Which Tool is Best in 2026?",
            "{keyword} vs Traditional Methods",
            "Top 5 {keyword} Tools Compared",
            "{keyword}: A Developer's Complete Guide",
            "What's New in {keyword}?",
            "{keyword} Comparison: Pros and Cons",
            "Choosing the Right {keyword} for Your Project",
            "{keyword} Showdown: Which One Wins?",
            "The Ultimate {keyword} Guide",
            "{keyword} in 2026: What Changed?",
        ],
        "default": [
            "The Future of {keyword}",
            "Why {keyword} Matters",
            "Understanding {keyword}",
            "{keyword} Explained Simply",
            "The Impact of {keyword}",
        ],
    }

    # ═══════════════════════════════════════════════════════════════
    # FALLBACK TOPICS (NEVER EMPTY)
    # ═══════════════════════════════════════════════════════════════

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
            "The Art of Letting Go",
            "Connecting with Your Higher Self",
            "Breath Work for Beginners",
            "Finding Peace in Chaos",
            "The Journey of Self-Discovery",
            "Practicing Compassion Daily",
            "The Power of Positive Thinking",
        ],
        "Dev Comparisons": [
            "Python vs JavaScript: Which to Learn First",
            "React vs Vue: Framework Comparison",
            "SQL vs NoSQL: Database Choices",
            "Docker vs Kubernetes: Container Wars",
            "TypeScript vs JavaScript: Is It Worth It",
            "AWS vs Azure vs GCP: Cloud Comparison",
            "Best Code Editors Compared",
            "REST vs GraphQL: API Design",
            "GitHub vs GitLab: Which is Better?",
            "Frontend vs Backend: Career Path Guide",
            "Monolith vs Microservices: Architecture",
            "Flutter vs React Native: Mobile Dev",
            "PostgreSQL vs MySQL: Database Showdown",
            "Vim vs VS Code: Editor Wars",
            "Tailwind vs Bootstrap: CSS Frameworks",
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
        logger.info("🚀 Autonomous Content Engine started — brand-aware self-driving posting")

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
            time.sleep(60)

    def _tick(self) -> None:
        """One iteration — check if we should post."""
        brands = self._get_active_brands()

        for brand in brands:
            brand_id = brand.get("id", "")
            brand_name = brand.get("name", "default")

            if self._should_post(brand_id):
                self._generate_and_publish(brand_id, brand_name)

    def _should_post(self, brand_id: str) -> bool:
        """Determine if it's time to post for this brand."""
        now = datetime.now(timezone.utc)

        last_post = self._last_post_time.get(brand_id)
        if last_post:
            minutes_since = (now - last_post).total_seconds() / 60
            if minutes_since < self.POST_INTERVAL_MINUTES:
                return False

        today = now.strftime("%Y-%m-%d")
        posts_today = self._posts_today.get(f"{brand_id}:{today}", 0)
        if posts_today >= self.MAX_POSTS_PER_DAY:
            return False

        return True

    # ═══════════════════════════════════════════════════════════════
    # BRAND DATA
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

    # ═══════════════════════════════════════════════════════════════
    # TOPIC SELECTION (Brand-Aware)
    # ═══════════════════════════════════════════════════════════════

    def _select_topic(self, brand_id: str, brand_name: str) -> str:
        """Select the best topic for this brand.

        Priority:
        1. Brand-relevant trending topics (filtered by keywords)
        2. Brand-inspired topics from trends (using templates)
        3. Custom topics from database
        4. Previously successful topics
        5. Fallback topics (guaranteed relevant)
        """
        # 1. Try trending topics filtered by brand keywords
        trending = self._get_trending_topics()
        if trending:
            relevant = self._filter_by_brand_keywords(trending, brand_name)
            if relevant:
                topic = random.choice(relevant)
                if self._is_unused(brand_id, topic):
                    logger.info(f"✅ Found relevant trend for {brand_name}: {topic}")
                    return topic

            # 2. Generate brand-inspired topic from trend
            if trending:
                trend = random.choice(trending)
                topic = self._generate_brand_topic(trend, brand_name)
                if topic and self._is_unused(brand_id, topic):
                    logger.info(f"🔄 Generated brand topic from trend for {brand_name}: {topic}")
                    return topic

        # 3. Try custom topics from database
        custom = self._get_custom_topics(brand_id)
        if custom:
            topic = random.choice(custom)
            if self._is_unused(brand_id, topic):
                logger.info(f"📋 Using custom topic for {brand_name}: {topic}")
                return topic

        # 4. Try previously successful topics
        successful = self._get_successful_topics(brand_id)
        if successful:
            topic = random.choice(successful)
            if self._is_unused(brand_id, topic):
                logger.info(f"📈 Reusing successful topic for {brand_name}: {topic}")
                return topic

        # 5. Fallback topics (guaranteed relevant)
        fallback = self.FALLBACK_TOPICS.get(brand_name, self.FALLBACK_TOPICS["default"])
        topic = random.choice(fallback)
        logger.info(f"🛟 Using fallback topic for {brand_name}: {topic}")
        return topic

    def _get_trending_topics(self) -> List[str]:
        """Fetch trending topics from Supabase (populated by TrendIntelligence)."""
        if not self.supabase:
            return []

        try:
            result = self.supabase.table("trend_posts") \
                .select("title") \
                .order("fetched_at", desc=True) \
                .limit(20) \
                .execute()

            topics = []
            for row in result.data or []:
                title = row.get("title", "")
                if title:
                    topics.append(title)
            return topics
        except Exception:
            return []

    def _filter_by_brand_keywords(self, topics: List[str], brand_name: str) -> List[str]:
        """Filter trending topics by brand-specific keywords."""
        keywords = self.BRAND_KEYWORDS.get(brand_name, self.BRAND_KEYWORDS["default"])
        
        relevant = []
        for topic in topics:
            topic_lower = topic.lower()
            for keyword in keywords:
                if keyword in topic_lower:
                    relevant.append(topic)
                    break
        
        return relevant

    def _generate_brand_topic(self, trend_topic: str, brand_name: str) -> Optional[str]:
        """Generate a brand-relevant topic inspired by a trend.

        Example:
          Trend: "Thinking Machines Lab Drops Its First Model"
          Spiritual Teacher → "Mindfulness in the Age of Thinking Machines Lab"
          Dev Comparisons → "Thinking Machines Lab: Which Tool is Best in 2026?"
        """
        templates = self.BRAND_TOPIC_TEMPLATES.get(brand_name, self.BRAND_TOPIC_TEMPLATES["default"])
        
        # Extract meaningful keyword from trend
        keyword = self._extract_keyword(trend_topic)
        
        if not keyword:
            return None
        
        template = random.choice(templates)
        topic = template.replace("{keyword}", keyword)
        return topic

    def _extract_keyword(self, topic: str) -> str:
        """Extract the main keyword from a trending topic title."""
        # Remove apostrophes and lowercase
        clean = topic.lower().replace("'s", "").replace("'", "")
        
        # Split into words
        words = clean.split()
        
        # Remove stop words
        stop_words = {
            "the", "a", "an", "is", "are", "was", "were", "to", "for",
            "of", "in", "on", "at", "and", "or", "but", "with", "without",
            "from", "by", "about", "into", "through", "during", "before",
            "after", "above", "below", "up", "down", "out", "off", "over",
            "under", "again", "further", "then", "once", "here", "there",
        }
        
        meaningful = [w for w in words if w not in stop_words and len(w) > 2]
        
        if not meaningful:
            return None
        
        # Take up to 3 words as the keyword
        keyword = " ".join(meaningful[:3])
        
        # Capitalize first letter of each word
        keyword = keyword.title()
        
        return keyword

    def _get_custom_topics(self, brand_id: str) -> List[str]:
        """Get brand's custom topics from database."""
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
                    try:
                        return json.loads(topics)
                    except json.JSONDecodeError:
                        return []
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
        # Keep only last 50 topics
        if len(used) > 50:
            used = used[-50:]
            self._used_topics[brand_id] = used
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

        if len(self._engagement_data.get(brand_id, [])) > 100:
            self._engagement_data[brand_id] = self._engagement_data[brand_id][-100:]

    def get_status(self) -> Dict[str, Any]:
        """Get autonomous content engine status."""
        return {
            "running": self._running,
            "posts_today": sum(self._posts_today.values()),
            "topics_used": sum(len(v) for v in self._used_topics.values()),
            "engagement_data_points": sum(len(v) for v in self._engagement_data.values()),
            "brands_tracked": list(self._last_post_time.keys()),
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
