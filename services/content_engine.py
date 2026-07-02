"""
Content Engine v10.0 — Structured Single-Call Pipeline
- One Gemini call returns full structured JSON → parsed into TechPost model
- 10 topic categories, 70+ topics
- Jinja2 card renderer with graceful image fallback
- Thread-safe topic rotation
"""

import random
import logging
import threading
from typing import Tuple, Optional, List

from models.content import TechPost, PostTheme
from services.gemini import gemini

logger = logging.getLogger("kanyoza.services.content_engine")

# ── Topic bank ─────────────────────────────────────────────────────────────────

TOPICS = {
    "system_architecture": [
        "Monolithic vs Microservices architecture",
        "REST API vs GraphQL for modern apps",
        "Serverless vs Containers deployment",
        "Redis vs RabbitMQ for message queuing",
        "Load Balancer vs API Gateway patterns",
        "Event-driven vs Request-driven architecture",
        "gRPC vs REST for internal microservices",
        "Service Mesh vs API Gateway: when to use each",
    ],
    "database_mastery": [
        "PostgreSQL vs MySQL for production",
        "B-Tree vs Hash indexes performance",
        "Connection pooling vs direct connections",
        "ORM vs Raw SQL queries",
        "Vertical vs Horizontal database scaling",
        "Replication vs Sharding strategies",
        "Redis vs Memcached for caching",
        "JSONB vs separate tables in PostgreSQL",
        "Optimistic vs Pessimistic locking",
    ],
    "software_craftsmanship": [
        "Git Rebase vs Merge for clean history",
        "Monorepo vs Multi-repo project structure",
        "TypeScript vs JavaScript for large apps",
        "TDD vs BDD testing approaches",
        "OOP vs Functional programming patterns",
        "Clean Architecture vs MVC",
        "Code review: async vs synchronous",
        "Feature flags vs blue-green deployments",
    ],
    "ai_machine_learning": [
        "RAG vs Fine-tuning for custom AI",
        "Vector databases vs Traditional search",
        "Open source vs Proprietary AI models",
        "Prompt engineering vs Model training",
        "Local AI vs Cloud AI for businesses",
        "ChatGPT vs Gemini for developers",
        "LangChain vs direct API calls",
        "Embeddings vs keyword search for retrieval",
    ],
    "backend_engineering": [
        "FastAPI vs Flask for Python APIs",
        "Node.js vs Python for API performance",
        "JWT vs Session-based authentication",
        "Synchronous vs Asynchronous API design",
        "Rate limiting: Redis vs in-memory approaches",
        "Webhook vs Polling for real-time updates",
        "Background jobs: Celery vs RQ vs threading",
        "OAuth 2.0 vs API keys for auth",
    ],
    "devops_cloud": [
        "Docker vs Podman: container runtimes",
        "Kubernetes vs Docker Compose for orchestration",
        "GitHub Actions vs CircleCI CI/CD",
        "AWS vs Google Cloud for startups",
        "Infrastructure as Code: Terraform vs Pulumi",
        "Blue-Green vs Canary deployment strategies",
        "Centralized vs Distributed logging",
        "Prometheus vs Datadog for monitoring",
    ],
    "security_engineering": [
        "Password hashing: bcrypt vs Argon2",
        "SQL injection prevention: parameterized queries vs ORM",
        "HTTPS vs HTTP: what developers must know",
        "Symmetric vs Asymmetric encryption",
        "RBAC vs ABAC authorization patterns",
        "Secrets management: env vars vs vault",
        "Input validation: server-side vs client-side",
        "CORS: what it is and how to configure it",
    ],
    "system_design_patterns": [
        "CQRS vs traditional CRUD architecture",
        "Event sourcing vs state-based storage",
        "Circuit breaker vs retry patterns",
        "Saga vs two-phase commit for distributed transactions",
        "Cache-aside vs write-through caching",
        "Bulkhead vs timeout patterns for resilience",
        "Message queue vs direct API calls for scale",
        "Read replicas vs write-through cache",
    ],
    "malawi_tech": [
        "Mobile Money vs Bank transfers integration",
        "Cloud hosting vs Local servers in Malawi",
        "USSD vs Mobile Apps for African users",
        "Offline-first vs Online-only applications",
        "SMS notifications vs Push notifications",
        "Low-bandwidth API design for African markets",
        "Airtel Money vs TNM Mpamba API integration",
        "Building for feature phones vs smartphones",
    ],
    "career_growth": [
        "Self-taught vs CS degree developers",
        "Freelance vs full-time software engineering",
        "Senior dev habits vs junior dev habits",
        "Side projects vs open source contributions",
        "Specialization vs generalization in tech",
        "Remote work vs on-site for developers",
        "Code bootcamp vs university for tech careers",
        "Reading docs vs watching tutorials",
    ],
}

# ── Category → PostTheme mapping ───────────────────────────────────────────────

CATEGORY_THEMES: dict = {
    "system_architecture":    PostTheme.ARCHITECTURE,
    "database_mastery":       PostTheme.DATABASE,
    "software_craftsmanship": PostTheme.CODE_TIPS,
    "ai_machine_learning":    PostTheme.AI,
    "backend_engineering":    PostTheme.BACKEND,
    "devops_cloud":           PostTheme.CLOUD,
    "security_engineering":   PostTheme.SECURITY,
    "system_design_patterns": PostTheme.SYSTEM_DESIGN,
    "malawi_tech":            PostTheme.COMPARISON,
    "career_growth":          PostTheme.TUTORIAL,
}

# ── Category → hashtags ────────────────────────────────────────────────────────

HASHTAGS: dict = {
    "system_architecture":    "#SystemDesign #SoftwareArchitecture #CloudComputing #TechTips",
    "database_mastery":       "#Database #SQL #NoSQL #Scaling #PostgreSQL",
    "software_craftsmanship": "#CleanCode #DevCommunity #SoftwareEngineering #Programming",
    "ai_machine_learning":    "#AI #MachineLearning #LLM #TechTrends #GenerativeAI",
    "backend_engineering":    "#BackendDev #APIs #Python #NodeJS #WebDev",
    "devops_cloud":           "#DevOps #Cloud #Docker #Kubernetes #CICD",
    "security_engineering":   "#CyberSecurity #AppSecurity #SecureCode #InfoSec",
    "system_design_patterns": "#SystemDesign #DistributedSystems #Architecture #ScalableSystems",
    "malawi_tech":            "#MalawiTech #AfricaTech #DigitalAfrica #TechAfrica",
    "career_growth":          "#SoftwareEngineering #CareerAdvice #DevLife #CodingCareer",
}

# ── Caption assembly helpers ───────────────────────────────────────────────────

VIRAL_HOOKS: list = [
    "Most developers get this wrong. Here's the truth:",
    "I tested both for 30 days. One clear winner:",
    "Stop guessing. Here's what the data shows:",
    "The docs won't tell you this difference:",
    "I asked senior engineers which they prefer:",
    "One costs 10x more. Here's if it's worth it:",
    "The results surprised even experienced devs:",
    "After using both in production, here's my pick:",
    "This decision will save or cost you hours:",
    "Nobody talks about this trade-off openly:",
]

ENGAGEMENT_HOOKS: list = [
    "Which do you use? Drop your answer below 👇",
    "Agree or disagree? Let me know in the comments",
    "What's your experience with these two?",
    "Tag a dev who needs to see this",
    "Save this for your next tech decision 🔖",
    "Share this with your team before your next sprint",
    "What did I miss? Tell me in the comments",
    "Which would you pick for your next project?",
]

FALLBACK_POSTS: list = [
    (
        "PostgreSQL vs MySQL: What to Choose\n\n"
        "PostgreSQL wins for complex queries and data integrity. "
        "MySQL wins for simple read-heavy apps.\n\n"
        "My rule: If you need JSON, full-text search, or complex joins — pick PostgreSQL. "
        "If you're building a basic CRUD app — MySQL is faster to set up.\n\n"
        "PostgreSQL is the winner for 2025 production systems.\n\n"
        "Which do you use? Drop your answer below 👇\n\n"
        "#Database #PostgreSQL #MySQL #BackendDev"
    ),
    (
        "REST vs GraphQL: The Real Difference\n\n"
        "REST is simpler, cached easily, and every developer knows it. "
        "GraphQL gives clients exactly what they ask for — no over-fetching.\n\n"
        "Winner for most projects: REST. Use GraphQL only when you have "
        "complex nested data and multiple client types.\n\n"
        "What's your preference? 👇\n\n"
        "#SystemDesign #REST #GraphQL #APIs"
    ),
    (
        "Monolith vs Microservices: When To Switch\n\n"
        "Start with a monolith. Always. Microservices solve scaling problems "
        "you don't have yet.\n\n"
        "Split when: your team hits 10+, deploy times hurt, "
        "or different parts need different scaling.\n\n"
        "Did you start mono or micro? 👇\n\n"
        "#SystemDesign #SoftwareArchitecture #Microservices"
    ),
]


# ── Engine ─────────────────────────────────────────────────────────────────────

class ContentEngine:
    """
    Generates structured tech posts using a single Gemini call.
    Pipeline: topic → generate_structured_post() → TechPost model → caption + image.
    Falls back to legacy text generation if structured call fails.
    """

    def __init__(self):
        self.posted_topics: List[str] = []
        self.post_counter: int = 0
        self.last_category: str = "system_architecture"
        self.last_topic: str = ""
        self._lock = threading.Lock()

    # ── Topic rotation ─────────────────────────────────────────────────────────

    def get_next_topic(self) -> Tuple[str, str]:
        with self._lock:
            category = random.choice(list(TOPICS.keys()))
            self.last_category = category
            available = [t for t in TOPICS[category] if t not in self.posted_topics[-20:]]
            if not available:
                available = TOPICS[category]
            topic = random.choice(available)
            self.last_topic = topic
            self.posted_topics.append(topic)
            if len(self.posted_topics) > 60:
                self.posted_topics = self.posted_topics[-40:]
            return category, topic

    def get_theme(self, category: str) -> PostTheme:
        return CATEGORY_THEMES.get(category, PostTheme.COMPARISON)

    def get_hashtags(self, category: str) -> str:
        return HASHTAGS.get(category, "#TechTips #Coding #SoftwareEngineering")

    def list_categories(self) -> List[str]:
        return list(TOPICS.keys())

    def get_fallback_post(self) -> Tuple[str, Optional[bytes]]:
        post = random.choice(FALLBACK_POSTS)
        return post, None

    # ── Caption builder ────────────────────────────────────────────────────────

    def _build_caption_from_tech_post(self, post: TechPost, category: str) -> str:
        """
        Assemble a full Facebook caption from a structured TechPost.
        No second Gemini call needed — all data comes from the model.
        """
        viral_hook = random.choice(VIRAL_HOOKS)
        engagement = random.choice(ENGAGEMENT_HOOKS)
        tags = post.formatted_hashtags or self.get_hashtags(category)

        lines = [viral_hook, ""]

        if post.title:
            lines.append(f"🔥 {post.title}")
            lines.append("")

        if post.caption:
            lines.append(post.caption)
            lines.append("")

        if post.is_debate and post.option_a and post.option_b:
            lines.append(f"⚡ {post.option_a} vs {post.option_b}:")
            for pt in post.points_a[:3]:
                lines.append(f"  • {post.option_a}: {pt}")
            for pt in post.points_b[:3]:
                lines.append(f"  • {post.option_b}: {pt}")
            lines.append("")

        if post.winner:
            lines.append(f"🏆 Winner: {post.winner}")
            lines.append("")

        lines.append(engagement)
        lines.append("")
        lines.append(tags)

        return "\n".join(lines)

    # ── Image generation ───────────────────────────────────────────────────────

    def _generate_image(self, post: TechPost, topic: str) -> Optional[bytes]:
        """
        Try card renderer (Jinja2 + HCTI/Playwright), then diagram fallback.
        The TechPost model has all data — no second Gemini call needed.
        """
        image_bytes = None

        try:
            from services.card_renderer import card_renderer
            image_bytes = card_renderer.render(post)
            if image_bytes:
                logger.info("✅ Card renderer produced image")
                return image_bytes
        except Exception as exc:
            logger.warning("Card renderer failed: %s", exc)

        try:
            from services.image_engine import generate_best_image
            caption_preview = f"{post.title} — {post.caption[:100]}"
            image_bytes = generate_best_image(topic, caption_preview, post.theme.value)
            if image_bytes:
                logger.info("✅ Image engine fallback used")
        except Exception as exc:
            logger.warning("Image engine fallback failed: %s", exc)

        return image_bytes

    # ── Main entry point ───────────────────────────────────────────────────────

    def generate_post(self) -> Tuple[str, Optional[bytes]]:
        """
        Structured pipeline: one Gemini call → TechPost → caption + image.
        Falls back to legacy text pipeline if structured call fails.
        """
        try:
            category, topic = self.get_next_topic()
            theme = self.get_theme(category)

            logger.info("Generating post: category='%s' topic='%s' theme='%s'",
                        category, topic[:60], theme.value)

            # ── Structured path (primary) ──────────────────────────────────────
            structured = gemini.generate_structured_post(topic, theme.value)

            if structured:
                try:
                    post = TechPost.from_gemini_response(structured, theme=theme)
                    caption = self._build_caption_from_tech_post(post, category)
                    image_bytes = self._generate_image(post, topic)
                    logger.info("Structured post assembled: %d words", len(caption.split()))
                    self.post_counter += 1
                    return caption, image_bytes
                except Exception as exc:
                    logger.warning("TechPost model parse failed, falling back: %s", exc)

            # ── Legacy path (fallback) ─────────────────────────────────────────
            logger.info("Using legacy text pipeline for topic '%s'", topic[:60])
            post_body = gemini.generate_content_post(topic=topic, template_type="deep_dive")

            if not post_body:
                logger.warning("Both pipelines failed — using static fallback")
                return self.get_fallback_post()

            post_body = post_body.replace("Madalitso", "").replace("madalitso", "")
            viral_hook = random.choice(VIRAL_HOOKS)
            engagement = random.choice(ENGAGEMENT_HOOKS)
            tags = self.get_hashtags(category)

            caption = f"{viral_hook}\n\n{post_body}\n\n{engagement}\n\n{tags}"

            try:
                from services.image_engine import generate_best_image
                image_bytes = generate_best_image(topic, caption[:200], "comparison")
            except Exception:
                image_bytes = None

            self.post_counter += 1
            logger.info("Legacy post assembled: %d words", len(caption.split()))
            return caption, image_bytes

        except Exception as exc:
            logger.error("Post generation crashed: %s", exc)
            return self.get_fallback_post()


content_engine = ContentEngine()
