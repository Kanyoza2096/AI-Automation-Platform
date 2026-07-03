from __future__ import annotations

import random
from typing import Optional


CATEGORIES: dict[str, list[str]] = {
    # ── Tech / Software ───────────────────────────────────────────────────────
    "AI & Machine Learning": [
        "ChatGPT vs Gemini", "Claude vs ChatGPT", "GPT-4 vs Gemini 2.0",
        "LLM fine-tuning vs RAG", "Open-source AI vs Proprietary AI",
        "AI agents vs traditional automation", "Diffusion models vs GANs",
        "On-device AI vs Cloud AI", "Prompt engineering vs model training",
        "Copilot vs Cursor vs Continue for coding",
    ],
    "Backend Development": [
        "FastAPI vs Flask", "REST vs GraphQL", "Microservices vs Monolith",
        "Event-driven vs Request-driven architecture", "CQRS vs traditional CRUD",
        "gRPC vs REST for internal services", "Node.js vs Python for APIs",
        "Celery vs RQ vs Threading for background jobs",
        "Synchronous vs Asynchronous API design",
        "Webhook vs Polling for real-time updates",
    ],
    "Cloud & DevOps": [
        "AWS vs Google Cloud vs Azure", "Kubernetes vs Docker Compose",
        "GitHub Actions vs Jenkins", "Terraform vs Pulumi",
        "Serverless vs Containers", "Blue-Green vs Canary deployments",
        "Prometheus vs Datadog for monitoring", "Centralized vs Distributed logging",
        "GitOps vs traditional CI/CD", "Multi-cloud vs Single-cloud strategy",
    ],
    "Databases": [
        "PostgreSQL vs MySQL", "MongoDB vs PostgreSQL",
        "Redis vs Memcached", "SQL vs NoSQL for startups",
        "Supabase vs Firebase", "PlanetScale vs Neon vs Railway",
        "ORM vs Raw SQL", "Vertical vs Horizontal database scaling",
        "ACID vs BASE consistency models", "Graph databases vs Relational databases",
    ],
    "Frontend": [
        "React vs Vue", "Next.js vs Nuxt", "React vs Svelte",
        "Tailwind CSS vs Bootstrap", "PWA vs Native App",
        "TypeScript vs JavaScript", "Vite vs Webpack",
        "Server-side rendering vs Client-side rendering",
        "React Native vs Flutter for cross-platform",
        "CSS-in-JS vs traditional CSS",
    ],
    "Security": [
        "JWT vs Session-based auth", "OAuth 2.0 vs API keys",
        "Password hashing: bcrypt vs Argon2", "SAST vs DAST testing",
        "Symmetric vs Asymmetric encryption", "Zero-trust vs perimeter security",
        "RBAC vs ABAC authorization", "Secrets in env vars vs vault",
        "mTLS vs TLS", "Container security vs VM isolation",
    ],
    "Programming Languages": [
        "Python vs Go", "Rust vs C++", "TypeScript vs JavaScript",
        "Python vs JavaScript for backends", "Go vs Node.js for performance",
        "Java vs Kotlin for Android", "Swift vs Kotlin for mobile",
        "Python vs R for data science", "PHP vs Python for web", "C vs C++ vs Rust",
    ],
    "System Design": [
        "CAP theorem: Consistency vs Availability", "Kafka vs RabbitMQ",
        "Load balancer: Round-robin vs Least connections",
        "Read replica vs Write-through cache", "Circuit breaker vs Retry with backoff",
        "Saga pattern vs Two-phase commit", "Message queue vs Direct API call",
        "Rate limiting: Redis vs in-memory", "Idempotency: GET vs POST vs PUT",
        "CDN vs Origin server trade-offs",
    ],

    # ── Smartphones & Mobile Devices ──────────────────────────────────────────
    "Smartphones": [
        "iPhone vs Android", "Samsung Galaxy vs Google Pixel",
        "iPhone 16 vs Samsung Galaxy S25", "Google Pixel vs OnePlus",
        "Flagship vs Mid-range phones in 2025", "iPhone camera vs Android camera",
        "iOS updates vs Android updates", "Face ID vs Fingerprint scanner",
        "Apple Silicon vs Snapdragon for mobile", "Foldable phones vs traditional slabs",
    ],

    # ── Computers & Laptops ───────────────────────────────────────────────────
    "Computers & Laptops": [
        "MacBook vs Windows laptop for developers", "MacBook Air vs MacBook Pro",
        "Apple M4 vs Intel Core Ultra", "AMD Ryzen vs Intel Core",
        "Laptop vs Desktop for programming", "MacBook vs Dell XPS vs Lenovo ThinkPad",
        "Gaming PC vs Workstation", "SSD vs HDD in 2025",
        "16GB vs 32GB RAM for developers", "ARM Mac vs x86 Windows performance",
    ],

    # ── Operating Systems ─────────────────────────────────────────────────────
    "Operating Systems": [
        "Windows vs macOS for developers", "Linux vs Windows for coding",
        "macOS vs Linux vs Windows: which is best for devs",
        "Ubuntu vs Fedora vs Arch Linux", "Windows 11 vs Windows 10",
        "macOS Sequoia vs Windows 11", "Android vs iOS",
        "ChromeOS vs Windows for students", "Linux desktop vs Windows desktop",
        "Dual-booting vs Virtual Machine vs WSL",
    ],

    # ── Gaming ────────────────────────────────────────────────────────────────
    "Gaming": [
        "PC gaming vs Console gaming", "PlayStation 5 vs Xbox Series X",
        "Nintendo Switch vs Steam Deck", "PC vs PS5 vs Xbox: which wins in 2025",
        "Cloud gaming vs local gaming", "SSD vs HDD for gaming load times",
        "4K gaming vs 1440p at high FPS", "RTX 4080 vs RTX 4090 vs RX 7900 XTX",
        "Wired vs Wireless gaming peripherals", "AAA games vs Indie games",
    ],

    # ── Apps & Productivity ───────────────────────────────────────────────────
    "Apps & Productivity": [
        "Notion vs Obsidian for notes", "VS Code vs JetBrains IDEs",
        "Slack vs Microsoft Teams", "Figma vs Adobe XD",
        "Google Workspace vs Microsoft 365", "Zoom vs Google Meet vs Teams",
        "Trello vs Jira vs Asana for project management",
        "GitHub vs GitLab vs Bitbucket", "Linear vs Jira for dev teams",
        "1Password vs Bitwarden vs LastPass",
    ],

    # ── Social Media & Platforms ──────────────────────────────────────────────
    "Social Media & Platforms": [
        "Twitter/X vs Bluesky vs Mastodon", "YouTube vs TikTok for creators",
        "Instagram vs TikTok for marketing", "LinkedIn vs Twitter for professional networking",
        "Facebook vs Instagram for businesses", "YouTube Shorts vs TikTok vs Reels",
        "Discord vs Slack for communities", "Reddit vs Quora for Q&A",
        "WhatsApp vs Telegram vs Signal", "Twitch vs YouTube Gaming",
    ],

    # ── Gadgets & Consumer Tech ───────────────────────────────────────────────
    "Gadgets & Tech": [
        "AirPods Pro vs Sony WH-1000XM5", "Apple Watch vs Samsung Galaxy Watch",
        "Kindle vs iPad for reading", "Smart TV vs Projector",
        "Wi-Fi 6 vs Wi-Fi 7", "4K monitor vs Ultrawide for productivity",
        "Mechanical keyboard vs membrane keyboard", "USB-C hub vs Thunderbolt dock",
        "NAS vs Cloud storage for home backups", "Smart home: Alexa vs Google Home vs HomeKit",
    ],

    # ── Mobile Development ────────────────────────────────────────────────────
    "Mobile Development": [
        "React Native vs Flutter", "Native iOS vs Native Android development",
        "Swift vs Kotlin", "Expo vs bare React Native",
        "Flutter vs Xamarin", "SwiftUI vs UIKit",
        "Jetpack Compose vs XML layouts", "App Store vs Google Play policies",
        "Mobile CI/CD: Fastlane vs Bitrise", "Offline-first vs Online-only mobile apps",
    ],

    # ── Open Source & Tools ───────────────────────────────────────────────────
    "Open Source & Tools": [
        "Vim vs VS Code vs Neovim", "GitHub Actions vs GitLab CI",
        "Terraform vs Ansible", "Prometheus vs Grafana vs Datadog",
        "Git rebase vs merge", "Monorepo vs Multi-repo",
        "Open-source vs commercial software", "Self-hosted vs SaaS tools",
        "npm vs yarn vs pnpm", "Docker vs Podman",
    ],
}


class TopicSelector:
    """Selects comparison topics for content generation.

    Cycles through all categories evenly before repeating, so a 10-post day
    covers a wide variety of subjects (phones, OS, coding, laptops, gaming…).
    This is a pure domain object — no I/O, thread-safe via threading.Lock.
    """

    def __init__(self, categories: Optional[dict[str, list[str]]] = None) -> None:
        import threading
        self._categories = categories or CATEGORIES
        # Rotation state — tracks which categories have been used this cycle
        self._unused: list[str] = []
        self._lock = threading.Lock()

    def pick(self, category: Optional[str] = None) -> tuple[str, str]:
        """Return (topic, category). If *category* is None, use even rotation."""
        with self._lock:
            if category and category in self._categories:
                cat = category
            else:
                cat = self._next_category_locked()

            topic = random.choice(self._categories[cat])
            return topic, cat

    def _next_category_locked(self) -> str:
        """
        Rotate evenly through all categories before repeating.
        Must be called with self._lock held.

        Guarantees that over any N-category window, every category appears
        once, which produces maximally varied daily posts.
        """
        if not self._unused:
            self._unused = list(self._categories.keys())
            random.shuffle(self._unused)
        return self._unused.pop()

    def all_categories(self) -> list[str]:
        return list(self._categories.keys())

    def topics_for(self, category: str) -> list[str]:
        return list(self._categories.get(category, []))

    def total_topics(self) -> int:
        return sum(len(v) for v in self._categories.values())


# Module-level singleton — shared across workflow runs so category rotation
# state persists across all 10 daily posts instead of resetting each time.
topic_selector = TopicSelector()
