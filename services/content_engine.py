"""
Content Engine v11.0 — Brand-Aware Image Post Pipeline

Pipeline:
    Scheduler
        │
        ▼
    ContentEngine.generate_post(brand, ai_profile)
        │
        ▼
    Gemini (ONE API CALL with brand-aware prompt)
        │
        ▼
    TechPost (dataclass)
    ┌───────────────┼────────────────┐
    ▼               ▼                ▼
Caption Builder  HTML Renderer   SVG Renderer
    │               │                │
    └───────────────┴────────────────┘
                    ▼
         Playwright Screenshot
                    ▼
             PNG (1080×1080)
                    ▼
         Facebook Publisher

- Brand-aware: each brand has its own tone, hashtags, and AI personality
- generate_post_legacy() provides v10 backwards compatibility
"""

import random
import logging
import time
from typing import Optional, List

from models.content import TechPost, PostTheme
from services.gemini import gemini
from utils.metrics import metrics

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
    "smartphones": [
        "iPhone 16 vs Samsung Galaxy S25",
        "Google Pixel vs OnePlus in 2025",
        "iOS vs Android: which is better for you",
        "Flagship phones vs mid-range phones: worth the price?",
        "iPhone camera system vs Samsung camera system",
        "Apple Face ID vs Android fingerprint scanners",
        "iPhone battery vs Android battery in 2025",
        "Foldable phones vs traditional slabs",
    ],
    "computers_laptops": [
        "MacBook vs Windows laptop for developers",
        "MacBook Air vs MacBook Pro: which to buy",
        "Apple M4 vs Intel Core Ultra performance",
        "AMD Ryzen vs Intel Core for workstations",
        "16GB vs 32GB RAM: do devs need more?",
        "SSD vs NVMe SSD speed difference",
        "Laptop vs Desktop for software development",
        "MacBook vs Dell XPS vs Lenovo ThinkPad",
    ],
    "operating_systems": [
        "Windows vs macOS for developers",
        "Linux vs Windows for software engineering",
        "macOS vs Linux: which is better for devs",
        "Ubuntu vs Fedora vs Arch Linux",
        "Windows 11 vs macOS Sequoia",
        "Android vs iOS: which ecosystem wins",
        "WSL2 vs dual-boot Linux on Windows",
        "ChromeOS vs Windows for students",
    ],
    "gaming": [
        "PC gaming vs Console gaming in 2025",
        "PlayStation 5 vs Xbox Series X",
        "Nintendo Switch 2 vs Steam Deck",
        "Cloud gaming vs local gaming",
        "RTX 4080 vs RTX 4090: is the price worth it",
        "4K gaming vs 1440p at high FPS",
        "Wired vs Wireless gaming peripherals",
        "Gaming laptop vs Gaming desktop",
    ],
    "gadgets_tech": [
        "AirPods Pro vs Sony WH-1000XM5",
        "Apple Watch vs Samsung Galaxy Watch",
        "Kindle vs iPad for reading",
        "Smart TV vs Projector for home theatre",
        "Wi-Fi 6 vs Wi-Fi 7: is the upgrade worth it",
        "4K monitor vs Ultrawide for productivity",
        "Mechanical keyboard vs membrane keyboard",
        "NAS vs Cloud storage for home backups",
    ],
    "apps_productivity": [
        "Notion vs Obsidian for developers",
        "VS Code vs JetBrains IDEs",
        "Slack vs Microsoft Teams",
        "Google Workspace vs Microsoft 365",
        "Zoom vs Google Meet for remote teams",
        "Trello vs Jira vs Linear for project management",
        "GitHub vs GitLab vs Bitbucket",
        "1Password vs Bitwarden",
    ],
    "cars_motorbikes": [
    "Tesla Model 3 vs BMW i4 electric comparison",
    "Toyota Hilux vs Ford Ranger: best African pickup",
    "Electric cars vs Petrol cars in 2025",
    "Toyota Vitz vs Honda Fit: best used car",
    "Hybrid vs Full Electric: which saves more money",
    "SUV vs Sedan: what to buy for African roads",
    "Tesla Cybertruck vs Rivian R1T",
    "Automatic vs Manual transmission: pros and cons",
    "Toyota RAV4 vs Honda CR-V comparison",
    "Mercedes-Benz vs BMW: which brand wins",
    "Best motorcycles for African roads",
    "Honda CB vs Yamaha YZF for beginners",
    "Electric motorcycles vs Petrol motorcycles",
    "Bajaj vs TVS: best affordable bike",
    "Sports bikes vs Cruisers: riding experience",
    "Kawasaki Ninja vs Suzuki GSX comparison",
    "Best commuter bikes under $5000",
    "Dirt bikes vs Street bikes for adventure",
    "Harley-Davidson vs Indian Motorcycle",
    "Motorcycle safety gear: what's essential",
    "Tesla Autopilot vs Mercedes Drive Pilot",
    "Car maintenance tips every driver should know",
    "Best fuel-efficient cars for city driving",
    "Luxury cars vs Economy cars: total cost of ownership",
    "Toyota vs Honda reliability comparison",
    "Best 7-seater SUVs for families",
    "Electric scooter vs Motorcycle for city commute",
    "Turbocharged vs Naturally aspirated engines",
],
"tech_startups": [
    "Bootstrapping vs VC funding: which path",
    "SaaS vs Marketplace business model",
    "How to validate your startup idea before building",
    "MVP vs Full product launch strategy",
    "Solo founder vs Co-founder: pros and cons",
    "B2B vs B2C SaaS: which is easier",
    "How to get your first 100 customers",
    "Pricing strategies for SaaS products",
    "When to pivot vs when to persevere",
    "Building in public vs stealth mode",
    "Remote team vs In-office for startups",
    "How to pitch to investors effectively",
    "Equity split between co-founders guide",
    "Customer acquisition vs Product development priority",
    "African tech startup ecosystem overview",
],
"blockchain_web3": [
    "Bitcoin vs Ethereum: store of value vs smart contracts",
    "Proof of Work vs Proof of Stake explained",
    "DeFi vs Traditional banking",
    "NFTs beyond art: real utility cases",
    "Solana vs Ethereum for dApp development",
    "Centralized exchange vs DEX comparison",
    "Web3 vs Web2: what actually changes",
    "Stablecoins vs Traditional currencies",
    "Smart contract security best practices",
    "Layer 1 vs Layer 2 blockchain scaling",
    "Crypto wallet comparison: hardware vs software",
    "DAO vs Traditional organization structure",
],
"internet_of_things": [
    "Smart home: Google Home vs Amazon Alexa",
    "IoT security risks every user should know",
    "Raspberry Pi vs Arduino for IoT projects",
    "MQTT vs HTTP for IoT communication",
    "Edge computing vs Cloud for IoT data",
    "Smart agriculture sensors for African farmers",
    "Wearables: Apple Watch vs Garmin for health",
    "Zigbee vs Z-Wave vs Wi-Fi for smart homes",
    "IoT in manufacturing vs Traditional automation",
    "Smart city technology: what's actually working",
],

}

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
    "smartphones":            PostTheme.COMPARISON,
    "computers_laptops":      PostTheme.COMPARISON,
    "operating_systems":      PostTheme.COMPARISON,
    "gaming":                 PostTheme.COMPARISON,
    "gadgets_tech":           PostTheme.COMPARISON,
    "apps_productivity":      PostTheme.COMPARISON,
    "cars_motorbikes":        PostTheme.COMPARISON,
    "tech_startups":          PostTheme.TUTORIAL,
    "blockchain_web3":        PostTheme.TUTORIAL,
    "internet_of_things":     PostTheme.TUTORIAL,
}

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
    "smartphones":            "#Smartphones #iPhone #Android #Samsung #TechReview",
    "computers_laptops":      "#Laptops #MacBook #Windows #Apple #PCSetup",
    "operating_systems":      "#Windows #macOS #Linux #Android #iOS #TechTips",
    "gaming":                 "#Gaming #PlayStation #Xbox #PCGaming #Nintendo",
    "gadgets_tech":           "#Gadgets #TechReview #Unboxing #ConsumerTech #TechTrends",
    "apps_productivity":      "#Productivity #Apps #SoftwareTools #TechTips #DevTools",
    "cars_motorbikes":        "#Cars #Motorcycles #AutoReview #CarComparison #EVs #Tesla #Toyota",
    "tech_startups":          "#Startups #Entrepreneurship #SaaS #Business #TechStartups",
    "blockchain_web3":        "#Blockchain #Web3 #Crypto #DeFi #NFTs #Ethereum",
    "internet_of_things":     "#IoT #SmartHome #EdgeComputing #RaspberryPi #Automation",
}

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


# ── Caption Builder ─────────────────────────────────────────────────────────────

class CaptionBuilder:
    """Builds a Facebook-ready caption from a TechPost. Zero additional AI calls."""

    @staticmethod
    def build(post: TechPost, category: str, brand=None) -> str:
        viral_hook = random.choice(VIRAL_HOOKS)
        engagement = random.choice(ENGAGEMENT_HOOKS)

        if brand and getattr(brand, 'hashtags', None):
            tags = brand.formatted_hashtags if hasattr(brand, 'formatted_hashtags') else " ".join(brand.hashtags)
        else:
            tags = post.formatted_hashtags or HASHTAGS.get(
                category, "#TechTips #Coding #SoftwareEngineering"
            )

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


# ── Content Engine ──────────────────────────────────────────────────────────────

class ContentEngine:
    """Brand-aware Content Engine. Orchestrates the full post pipeline."""

    def __init__(self):
        self._caption_builder = CaptionBuilder()
        self._prompt_cache = {}
        self._redis = None
        try:
            from flask import current_app
            container = current_app.extensions.get("service_container")
            if container:
                from core.redis_client import UpstashRedisClient
                self._redis = container.try_resolve(UpstashRedisClient)
        except Exception:
            pass

    def _cached_prompt(self, brand, ai_profile, topic: str) -> Optional[str]:
        """Check Redis for a cached prompt. Falls back to in-memory."""
        cache_key = f"prompt:{getattr(brand, 'id', 'default')}:{topic[:50]}"
        if self._redis and self._redis.is_available:
            try:
                cached = self._redis.get(cache_key)
                if cached:
                    logger.info("Redis prompt cache HIT for %s", cache_key)
                    return cached
            except Exception:
                pass
        return self._prompt_cache.get(cache_key)

    def _cache_prompt(self, brand, ai_profile, topic: str, prompt: str) -> None:
        """Store prompt in Redis + in-memory cache with 30-min TTL."""
        cache_key = f"prompt:{getattr(brand, 'id', 'default')}:{topic[:50]}"
        self._prompt_cache[cache_key] = prompt
        if self._redis and self._redis.is_available:
            try:
                self._redis.set(cache_key, prompt, ex=1800)
            except Exception:
                pass

    def get_theme(self, category: str) -> PostTheme:
        return CATEGORY_THEMES.get(category, PostTheme.COMPARISON)

    def get_hashtags(self, category: str) -> str:
        return HASHTAGS.get(category, "#TechTips #Coding #SoftwareEngineering")

    def list_categories(self) -> List[str]:
        return list(TOPICS.keys())

    def get_fallback_post(self) -> dict:
        caption = random.choice(FALLBACK_POSTS)
        return {"caption": caption, "image_bytes": None, "topic": "", "category": ""}

    # ── v11: Brand-aware generation ─────────────────────────────────────────

    def generate_post(self, brand=None, ai_profile=None, topic: Optional[str] = None) -> dict:
        """
        v11 Brand-aware post generation.
        Falls back to defaults if brand/ai_profile not provided.
        """
        _t0 = time.monotonic()
        category = "system_architecture"

        try:
            from domain.content.topic_selector import TopicSelector

            if not topic:
                selector = TopicSelector(brand=brand)
                topic, category = selector.pick()
            else:
                for cat, topics in TOPICS.items():
                    if topic in topics:
                        category = cat
                        break

            theme = self.get_theme(category)
            brand_name = getattr(brand, 'name', 'Default') if brand else 'Default'
            logger.info(
                "Pipeline start: brand='%s' category='%s' topic='%s' theme='%s'",
                brand_name, category, topic[:60], theme.value,
            )

            # Check Redis cache before calling prompt builder
            cached_prompt = self._cached_prompt(brand, ai_profile, topic)
            if cached_prompt:
                base_prompt = cached_prompt
                logger.info("Using cached prompt for %s", topic[:50])
            else:
                from services.prompt_builder import prompt_builder
                base_prompt = prompt_builder.build_post_prompt(brand, ai_profile, topic)
                self._cache_prompt(brand, ai_profile, topic, base_prompt)

            structured = gemini.generate_structured_post(topic, theme.value, base_prompt=base_prompt)
            if structured:
                try:
                    post = TechPost.from_gemini_response(structured, theme=theme)
                    caption = CaptionBuilder.build(post, category, brand=brand)
                    image_bytes = self._render_image(post, topic, brand)
                    logger.info(
                        "Pipeline complete: caption=%d words image=%s",
                        len(caption.split()),
                        f"{len(image_bytes)} bytes" if image_bytes else "None",
                    )
                    metrics.record_ai("content_engine", "generate_post", (time.monotonic() - _t0) * 1000, True)
                    return {"caption": caption, "image_bytes": image_bytes, "topic": topic, "category": category}
                except Exception as exc:
                    logger.warning("TechPost parse/render failed — falling back: %s", exc)
            # Fallback to legacy text
            post_body = gemini.generate_content_post(topic=topic, template_type="deep_dive")
            if not post_body:
                logger.warning("Both pipelines failed — using static fallback")
                return self.get_fallback_post()

            viral_hook = random.choice(VIRAL_HOOKS)
            engagement = random.choice(ENGAGEMENT_HOOKS)
            tags = self.get_hashtags(category)
            caption = f"{viral_hook}\n\n{post_body}\n\n{engagement}\n\n{tags}"
            return {"caption": caption, "image_bytes": None, "topic": topic, "category": category}

        except Exception as exc:
            logger.error("Post generation crashed: %s", exc)
            metrics.record_ai("content_engine", "generate_post", (time.monotonic() - _t0) * 1000, False)
            return self.get_fallback_post()

    # ── v10 Legacy: Backwards-compatible method ────────────────────────────

    def generate_post_legacy(self, topic: Optional[str] = None) -> dict:
        """
        Backwards-compatible method for v10 scheduler.
        Uses default workspace brand and AI profile.
        """
        try:
            from models.brand import Brand
            from models.ai_profile import AIProfile

            default_brand = Brand.default()
            default_profile = AIProfile.default()

            return self.generate_post(
                brand=default_brand,
                ai_profile=default_profile,
                topic=topic,
            )
        except Exception as e:
            logger.warning("Default brand/profile failed: %s — using fallback", e)
            return self.get_fallback_post()

    # ── Image rendering ────────────────────────────────────────────────────

    def _render_image(self, post: TechPost, topic: str, brand=None) -> Optional[bytes]:
        """Card rendering: SVG → HTML → Playwright → PNG."""
        try:
            from services.card_renderer import card_renderer
            png = card_renderer.render(post)
            if png:
                logger.info("Card renderer produced PNG (%d bytes)", len(png))
                return png
        except Exception as exc:
            logger.warning("Card renderer failed: %s", exc)

        try:
            from services.image_engine import generate_best_image
            caption_preview = f"{post.title} — {post.caption[:100]}"
            png = generate_best_image(topic, caption_preview, post.theme.value)
            if png:
                logger.info("image_engine last-resort fallback used")
            return png
        except Exception as exc:
            logger.warning("image_engine fallback also failed: %s", exc)
        return None


# Singleton
content_engine = ContentEngine()
