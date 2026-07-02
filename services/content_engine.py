"""
Content Engine v9.0 — Comparison Posts + HTML Cards
- Thread-safe topic rotation
- Comparison-focused templates
- HTML card generation (primary) + Diagram fallback
- Returns (caption, image_bytes)
"""

import random
import logging
import threading
from typing import Tuple, Optional, List

from services.gemini import gemini

logger = logging.getLogger("kanyoza.services.content_engine")

TOPICS = {
    "system_architecture": [
        "Monolithic vs Microservices architecture",
        "REST API vs GraphQL for modern apps",
        "SQL vs NoSQL databases for scaling",
        "Serverless vs Containers deployment",
        "Redis vs RabbitMQ for message queuing",
        "Load Balancer vs API Gateway patterns",
    ],
    "database_mastery": [
        "PostgreSQL vs MySQL for production",
        "B-Tree vs Hash indexes performance",
        "Connection pooling vs direct connections",
        "ORM vs Raw SQL queries",
        "Vertical vs Horizontal database scaling",
        "Replication vs Sharding strategies",
    ],
    "software_craftsmanship": [
        "Git Rebase vs Merge for clean history",
        "Monorepo vs Multi-repo project structure",
        "TypeScript vs JavaScript for large apps",
        "TDD vs BDD testing approaches",
        "OOP vs Functional programming patterns",
        "Agile vs Waterfall development",
    ],
    "ai_explained": [
        "RAG vs Fine-tuning for custom AI",
        "Vector databases vs Traditional search",
        "Open source vs Proprietary AI models",
        "Prompt engineering vs Model training",
        "Local AI vs Cloud AI for businesses",
        "ChatGPT vs Gemini for developers",
    ],
    "malawi_tech": [
        "Mobile Money vs Bank transfers integration",
        "Cloud hosting vs Local servers in Malawi",
        "USSD vs Mobile Apps for African users",
        "Offline-first vs Online-only applications",
        "Solar-powered vs Grid-dependent servers",
        "SMS notifications vs Push notifications",
    ],
}

VIRAL_HOOKS = [
    "Most developers get this wrong. Here's the truth:",
    "I tested both for 30 days. One clear winner:",
    "Stop guessing. Here's what the data shows:",
    "The docs won't tell you this difference:",
    "I asked 100 developers which they prefer:",
    "One costs 10x more. Is it worth it?",
    "The results surprised me:",
    "After using both for a year, here's my pick:",
]

ENGAGEMENT_HOOKS = [
    "Which do you use? Drop your answer below 👇",
    "Agree or disagree? Let me know in the comments",
    "What's your experience with these two?",
    "Tag a dev who needs to see this comparison",
    "Save this for your next tech decision",
    "Share this with your team before your next sprint",
]

HASHTAGS = {
    "system_architecture": "#SystemDesign #SoftwareArchitecture #CloudComputing",
    "database_mastery": "#Database #SQL #NoSQL #Scaling",
    "software_craftsmanship": "#CleanCode #DevCommunity #SoftwareEngineering",
    "ai_explained": "#AI #MachineLearning #TechTrends",
    "malawi_tech": "#MalawiTech #AfricaTech #DigitalAfrica",
}

FALLBACK_POSTS = [
    (
        "PostgreSQL vs MySQL: What to Choose\n\n"
        "PostgreSQL wins for complex queries and data integrity. "
        "MySQL wins for simple read-heavy apps.\n\n"
        "My rule: If you need JSON, full-text search, or complex joins — pick PostgreSQL. "
        "If you're building a basic CRUD app — MySQL is faster to set up.\n\n"
        "Which do you use?"
    ),
    (
        "REST vs GraphQL: The Real Difference\n\n"
        "REST is simpler, cached easily, and every developer knows it. "
        "GraphQL gives clients exactly what they ask for — no over-fetching.\n\n"
        "Winner for most projects: REST. Use GraphQL only when you have "
        "complex nested data and multiple client types.\n\n"
        "What's your preference?"
    ),
    (
        "Monolith vs Microservices: When To Switch\n\n"
        "Start with a monolith. Always. Microservices solve scaling problems "
        "you don't have yet.\n\n"
        "Split when: your team is 10+, your deploy times hurt, "
        "or different parts need different scaling.\n\n"
        "Did you start mono or micro?"
    ),
]

POST_TEMPLATES = ["deep_dive", "comparison", "refactoring", "tutorial", "quick_tip"]


class ContentEngine:
    """Generates professional comparison posts with HTML cards."""

    def __init__(self):
        self.posted_topics: List[str] = []
        self.post_counter: int = 0
        self.last_category: str = "system_architecture"
        self.last_topic: str = ""
        self.last_template: str = ""
        self._lock = threading.Lock()

    def get_next_topic(self) -> Tuple[str, str]:
        with self._lock:
            category = random.choice(list(TOPICS.keys()))
            self.last_category = category
            available = [t for t in TOPICS[category] if t not in self.posted_topics[-15:]]
            if not available:
                available = TOPICS[category]
            topic = random.choice(available)
            self.last_topic = topic
            self.posted_topics.append(topic)
            if len(self.posted_topics) > 50:
                self.posted_topics = self.posted_topics[-30:]
            return category, topic

    def get_next_template(self) -> str:
        template = random.choice(POST_TEMPLATES)
        self.last_template = template
        return template

    def get_hashtags(self, category: str) -> str:
        return HASHTAGS.get(category, "#TechTips #Coding")

    def get_fallback_post(self) -> Tuple[str, Optional[bytes]]:
        post = random.choice(FALLBACK_POSTS)
        post += f"\n\n{self.get_hashtags(self.last_category)}"
        return post, None

    def generate_post(self) -> Tuple[str, Optional[bytes]]:
        """Generate a comparison-style post with card image."""
        try:
            category, topic = self.get_next_topic()
            template = self.get_next_template()

            logger.info(f"Generating: topic='{topic[:60]}', template='{template}'")

            post_body = gemini.generate_content_post(topic=topic, template_type=template)

            if not post_body:
                logger.warning("Gemini failed, using fallback")
                return self.get_fallback_post()

            post_body = post_body.replace("Madalitso", "").replace("Kanyoza", "").replace("madalitso", "")

            viral_hook = random.choice(VIRAL_HOOKS)
            engagement_question = random.choice(ENGAGEMENT_HOOKS)
            tags = self.get_hashtags(category)

            full_caption = (
                f"{viral_hook}\n\n"
                f"{post_body}\n\n"
                f"{engagement_question}\n\n"
                f"{tags}"
            )

            # Generate image
            from services.html_card_engine import generate_card_from_post, is_configured as card_configured
            from services.image_engine import generate_best_image

            image_bytes = None

            # Try HTML card first
            if card_configured():
                try:
                    image_bytes = generate_card_from_post(full_caption)
                    if image_bytes:
                        logger.info("✅ HTML card generated")
                except Exception as e:
                    logger.warning(f"HTML card failed: {e}")

            # Fallback to diagram/Picsum
            if not image_bytes:
                try:
                    image_bytes = generate_best_image(topic, full_caption, template)
                    if image_bytes:
                        logger.info("✅ Diagram/Picsum fallback used")
                except Exception as e:
                    logger.warning(f"Image fallback failed: {e}")

            logger.info(f"Post assembled: {len(full_caption.split())} words")
            return full_caption, image_bytes

        except Exception as e:
            logger.error(f"Post generation failed: {e}")
            return self.get_fallback_post()


content_engine = ContentEngine()
