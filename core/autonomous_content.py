"""
Autonomous Content Engine — Self-driving content with adaptive scheduling.

v1.3 — Adaptive posting time:
  - Learns best posting hours from engagement data
  - Starts with default hours
  - Improves over time based on actual engagement
  - Never stops posting

Key principles:
  1. POSTING IS ALWAYS ON
  2. Topics from brand-specific sources
  3. Time adapts to audience engagement
  4. Passive learning — never waits for engagement to post
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
    """Self-driving content generation with adaptive scheduling."""

    # ═══════════════════════════════════════════════════════════════
    # CONFIGURATION
    # ═══════════════════════════════════════════════════════════════

    MAX_POSTS_PER_DAY = 10
    MIN_ENGAGEMENT_THRESHOLD = 5
    MIN_POST_INTERVAL_MINUTES = 30  # Don't post more often than every 30 min
    LEARNING_MIN_SAMPLES = 5        # Need at least 5 data points before learning

    # ═══════════════════════════════════════════════════════════════
    # BRAND-SPECIFIC TOPIC SOURCES
    # ═══════════════════════════════════════════════════════════════

    BRAND_TOPIC_SOURCES = {
        "Spiritual Teacher": ["knowledge_base", "custom", "fallback"],
        "Dev Comparisons": ["trends", "knowledge_base", "custom", "fallback"],
        "default": ["trends", "fallback"],
    }

    # ═══════════════════════════════════════════════════════════════
    # DEFAULT POSTING HOURS (Before learning)
    # ═══════════════════════════════════════════════════════════════

    DEFAULT_POSTING_HOURS = {
        "Spiritual Teacher": [6, 8, 10, 12, 16, 18, 20, 22],  # Spiritual: morning + evening
        "Dev Comparisons": [9, 11, 13, 15, 17, 19, 21],        # Tech: work hours
        "default": [9, 12, 15, 18, 21],
    }

    # ═══════════════════════════════════════════════════════════════
    # BRAND KEYWORDS
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
    # BRAND TOPIC TEMPLATES
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
    # FALLBACK TOPICS
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
        self._hour_engagement: Dict[str, Dict[int, List[float]]] = {}
        self._learned_hours: Dict[str, List[int]] = {}

    # ═══════════════════════════════════════════════════════════════
    # LIFECYCLE
    # ═══════════════════════════════════════════════════════════════

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._loop,
            name="autonomous-content",
            daemon=True,
        )
        self._thread.start()
        logger.info("🚀 Autonomous Content Engine started — adaptive scheduling active")

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        self._thread = None

    # ═══════════════════════════════════════════════════════════════
    # MAIN LOOP
    # ═══════════════════════════════════════════════════════════════

    def _loop(self) -> None:
        while self._running:
            try:
                self._tick()
            except Exception:
                logger.exception("Autonomous Content Loop error")
            time.sleep(60)  # Check every minute

    def _tick(self) -> None:
        brands = self._get_active_brands()

        for brand in brands:
            brand_id = brand.get("id", "")
            brand_name = brand.get("name", "default")

            if self._should_post(brand_id, brand_name):
                self._generate_and_publish(brand_id, brand_name)

    # ═══════════════════════════════════════════════════════════════
    # ADAPTIVE SCHEDULING
    # ═══════════════════════════════════════════════════════════════

    def _should_post(self, brand_id: str, brand_name: str) -> bool:
        """Check if it's the RIGHT TIME to post for this brand."""
        now = datetime.now(timezone.utc)
        current_hour = now.hour

        # Check if this hour is in the brand's posting hours
        posting_hours = self._get_posting_hours(brand_id, brand_name)

        if current_hour not in posting_hours:
            return False

        # Check minimum interval
        last_post = self._last_post_time.get(brand_id)
        if last_post:
            minutes_since = (now - last_post).total_seconds() / 60
            if minutes_since < self.MIN_POST_INTERVAL_MINUTES:
                return False

        # Check daily limit
        today = now.strftime("%Y-%m-%d")
        posts_today = self._posts_today.get(f"{brand_id}:{today}", 0)
        if posts_today >= self.MAX_POSTS_PER_DAY:
            return False

        return True

    def _get_posting_hours(self, brand_id: str, brand_name: str) -> List[int]:
        """Get posting hours for brand — learned or default."""
        # Check if we've learned better hours
        learned = self._learned_hours.get(brand_id)
        if learned:
            return learned

        # Check if we have enough engagement data to learn
        hour_engagement = self._hour_engagement.get(brand_id, {})
        total_samples = sum(len(scores) for scores in hour_engagement.values())

        if total_samples >= self.LEARNING_MIN_SAMPLES:
            learned_hours = self._calculate_best_hours(hour_engagement)
            if learned_hours:
                self._learned_hours[brand_id] = learned_hours
                logger.info(
                    f"🎯 Learned best posting hours for {brand_name}: "
                    f"{[f'{h}:00' for h in learned_hours]}"
                )
                return learned_hours

        # Use default hours
        return self.DEFAULT_POSTING_HOURS.get(
            brand_name,
            self.DEFAULT_POSTING_HOURS["default"],
        )

    def _calculate_best_hours(self, hour_engagement: Dict[int, List[float]]) -> List[int]:
        """Calculate best posting hours from engagement data."""
        hour_averages = {}

        for hour, scores in hour_engagement.items():
            if scores:
                hour_averages[hour] = sum(scores) / len(scores)

        if not hour_averages:
            return []

        # Sort hours by average engagement (highest first)
        sorted_hours = sorted(
            hour_averages,
            key=hour_averages.get,
            reverse=True,
        )

        # Take top 4-6 hours
        best_hours = sorted_hours[:5]

        # Sort back chronologically for posting
        return sorted(best_hours)

    # ═══════════════════════════════════════════════════════════════
    # BRAND DATA
    # ═══════════════════════════════════════════════════════════════

    def _get_active_brands(self) -> List[Dict[str, Any]]:
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
    # TOPIC SELECTION
    # ═══════════════════════════════════════════════════════════════

    def _select_topic(self, brand_id: str, brand_name: str) -> str:
        sources = self.BRAND_TOPIC_SOURCES.get(brand_name, self.BRAND_TOPIC_SOURCES["default"])

        for source in sources:
            topic = None

            if source == "trends":
                topic = self._get_trend_topic(brand_id, brand_name)
            elif source == "knowledge_base":
                topic = self._get_knowledge_base_topic(brand_id)
            elif source == "custom":
                topic = self._get_custom_topic(brand_id)
            elif source == "fallback":
                fallback = self.FALLBACK_TOPICS.get(brand_name, self.FALLBACK_TOPICS["default"])
                topic = random.choice(fallback)
                logger.info(f"🛟 Fallback topic for {brand_name}: {topic}")
                return topic

            if topic and self._is_unused(brand_id, topic):
                return topic

        fallback = self.FALLBACK_TOPICS.get(brand_name, self.FALLBACK_TOPICS["default"])
        return random.choice(fallback)

    def _get_trend_topic(self, brand_id: str, brand_name: str) -> Optional[str]:
        trending = self._get_trending_topics()

        if not trending:
            return None

        relevant = self._filter_by_brand_keywords(trending, brand_name)
        if relevant:
            topic = random.choice(relevant)
            logger.info(f"📡 Trend topic for {brand_name}: {topic}")
            return topic

        trend = random.choice(trending)
        topic = self._generate_brand_topic(trend, brand_name)
        if topic:
            logger.info(f"🔄 Generated trend topic for {brand_name}: {topic}")
            return topic

        return None

    def _get_knowledge_base_topic(self, brand_id: str) -> Optional[str]:
        if not self.supabase:
            return None

        try:
            result = self.supabase.table("knowledge_entries") \
                .select("title, content") \
                .eq("brand_id", brand_id) \
                .order("created_at", desc=True) \
                .limit(20) \
                .execute()

            if result.data:
                entry = random.choice(result.data)

                title = entry.get("title", "")
                if title and len(title) > 5:
                    logger.info(f"📚 Knowledge base topic: {title}")
                    return title

                content = entry.get("content", "")
                if content:
                    first_sentence = content.split(".")[0].strip()
                    if len(first_sentence) > 10:
                        logger.info(f"📚 Knowledge base topic (from content): {first_sentence[:100]}")
                        return first_sentence[:100]

            return None
        except Exception:
            return None

    def _get_custom_topic(self, brand_id: str) -> Optional[str]:
        if not self.supabase:
            return None

        try:
            result = self.supabase.table("brands") \
                .select("custom_topics") \
                .eq("id", brand_id) \
                .execute()

            if result.data:
                topics = result.data[0].get("custom_topics", [])

                if isinstance(topics, list) and topics:
                    topic = random.choice(topics)
                    logger.info(f"📋 Custom topic: {topic}")
                    return topic

                if isinstance(topics, str):
                    try:
                        parsed = json.loads(topics)
                        if isinstance(parsed, list) and parsed:
                            topic = random.choice(parsed)
                            logger.info(f"📋 Custom topic (parsed): {topic}")
                            return topic
                    except json.JSONDecodeError:
                        pass

            return None
        except Exception:
            return None

    def _get_trending_topics(self) -> List[str]:
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
        templates = self.BRAND_TOPIC_TEMPLATES.get(brand_name, self.BRAND_TOPIC_TEMPLATES["default"])
        keyword = self._extract_keyword(trend_topic)

        if not keyword:
            return None

        template = random.choice(templates)
        return template.replace("{keyword}", keyword)

    def _extract_keyword(self, topic: str) -> str:
        clean = topic.lower().replace("'s", "").replace("'", "")
        words = clean.split()

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

        return " ".join(meaningful[:3]).title()

    def _is_unused(self, brand_id: str, topic: str) -> bool:
        used = self._used_topics.get(brand_id, [])
        if len(used) > 50:
            used = used[-50:]
            self._used_topics[brand_id] = used
        return topic not in used

    # ═══════════════════════════════════════════════════════════════
    # CONTENT GENERATION & PUBLISHING
    # ═══════════════════════════════════════════════════════════════

    def _generate_and_publish(self, brand_id: str, brand_name: str) -> None:
        topic = self._select_topic(brand_id, brand_name)

        logger.info(f"📝 Generating content for '{brand_name}' — topic: {topic}")

        self._used_topics.setdefault(brand_id, []).append(topic)

        if self.shared_bus:
            self.shared_bus.publish("publish_request", {
                "brand_id": brand_id,
                "brand_name": brand_name,
                "topic": topic,
                "source": "autonomous_content_engine",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

        now = datetime.now(timezone.utc)
        self._last_post_time[brand_id] = now
        today = now.strftime("%Y-%m-%d")
        key = f"{brand_id}:{today}"
        self._posts_today[key] = self._posts_today.get(key, 0) + 1

        logger.info(f"✅ Published request sent for '{brand_name}' — {topic}")

    # ═══════════════════════════════════════════════════════════════
    # LEARNING (PASSIVE ENGAGEMENT TRACKING)
    # ═══════════════════════════════════════════════════════════════

    def record_engagement(
        self,
        brand_id: str,
        topic: str,
        engagement: int,
    ) -> None:
        """Record engagement — used for BOTH topic selection AND time learning."""
        timestamp = datetime.now(timezone.utc)

        # Store engagement for topic learning
        self._engagement_data.setdefault(brand_id, []).append({
            "topic": topic,
            "engagement": engagement,
            "timestamp": timestamp.isoformat(),
        })

        if len(self._engagement_data.get(brand_id, [])) > 100:
            self._engagement_data[brand_id] = self._engagement_data[brand_id][-100:]

        # Store engagement by HOUR for time learning
        hour = timestamp.hour
        self._hour_engagement.setdefault(brand_id, {}).setdefault(hour, []).append(
            float(engagement)
        )

        # Check if we should re-learn best hours
        total = sum(len(scores) for scores in self._hour_engagement.get(brand_id, {}).values())
        if total >= self.LEARNING_MIN_SAMPLES:
            # Invalidate learned hours so it recalculates
            self._learned_hours.pop(brand_id, None)

    def get_status(self) -> Dict[str, Any]:
        return {
            "running": self._running,
            "posts_today": sum(self._posts_today.values()),
            "topics_used": sum(len(v) for v in self._used_topics.values()),
            "engagement_data_points": sum(len(v) for v in self._engagement_data.values()),
            "brands_tracked": list(self._last_post_time.keys()),
            "learned_hours": {
                brand: [f"{h}:00" for h in hours]
                for brand, hours in self._learned_hours.items()
            },
            "brand_topic_sources": {
                brand: self.BRAND_TOPIC_SOURCES.get(brand, ["default"])
                for brand in self.FALLBACK_TOPICS
            },
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
    global _autonomous_content

    _autonomous_content = AutonomousContentEngine(
        supabase_client=supabase_client,
        shared_bus=shared_bus,
        container=container,
    )
    _autonomous_content.start()
    return _autonomous_content


def get_autonomous_content() -> Optional[AutonomousContentEngine]:
    return _autonomous_content


__all__ = [
    "AutonomousContentEngine",
    "init_autonomous_content",
    "get_autonomous_content",
]
