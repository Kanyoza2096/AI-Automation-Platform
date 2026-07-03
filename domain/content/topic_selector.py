from __future__ import annotations

import random
from typing import Optional


CATEGORIES: dict[str, list[str]] = {
    "AI & Machine Learning": [
        "GPT-4 vs Gemini", "LLM fine-tuning", "RAG architecture", "Vector databases",
        "AI agents", "Prompt engineering", "Diffusion models", "AI safety",
        "On-device inference", "Multimodal AI",
    ],
    "Backend Development": [
        "FastAPI vs Flask", "REST vs GraphQL", "Microservices patterns",
        "Event-driven architecture", "CQRS and Event Sourcing", "API versioning",
        "gRPC vs REST", "Service mesh", "12-factor apps", "Backend for Frontend",
    ],
    "Cloud & DevOps": [
        "Kubernetes vs Docker Swarm", "GitOps workflows", "Infrastructure as Code",
        "CI/CD pipelines", "Serverless vs containers", "FinOps", "Multi-cloud strategy",
        "Chaos engineering", "Platform engineering", "SRE practices",
    ],
    "Databases": [
        "PostgreSQL vs MySQL", "Redis caching", "Time-series databases",
        "NewSQL vs NoSQL", "Database sharding", "ACID vs BASE", "Graph databases",
        "Column-oriented storage", "Change data capture", "Polyglot persistence",
    ],
    "Frontend": [
        "React vs Vue", "Next.js vs Nuxt", "Web Components", "PWA vs native",
        "CSS-in-JS", "Micro-frontends", "WebAssembly", "Edge rendering",
        "Islands architecture", "Accessibility-first design",
    ],
    "Security": [
        "Zero-trust architecture", "OAuth 2.0 vs API keys", "JWT best practices",
        "Supply chain attacks", "Secrets management", "OWASP Top 10",
        "mTLS", "Container security", "SAST vs DAST", "Threat modeling",
    ],
    "Programming Languages": [
        "Python vs Go", "Rust memory safety", "TypeScript adoption",
        "Functional programming", "Concurrency models", "Type systems",
        "Compiled vs interpreted", "Garbage collection", "WASM languages", "DSL design",
    ],
    "System Design": [
        "CAP theorem", "Rate limiting strategies", "Distributed caching",
        "Message queues", "Load balancing algorithms", "Circuit breakers",
        "Idempotency keys", "Saga pattern", "Blue-green deployments", "Bulkhead pattern",
    ],
    "Mobile": [
        "React Native vs Flutter", "Native vs cross-platform", "Mobile CI/CD",
        "App performance profiling", "Offline-first apps", "Push notification strategy",
        "App security", "Deep linking", "State management", "Background tasks",
    ],
    "Open Source & Tools": [
        "Vim vs VS Code", "GitHub Actions vs Jenkins", "Terraform vs Pulumi",
        "Prometheus vs Datadog", "Grafana dashboards", "OpenTelemetry",
        "Dependency management", "Monorepo tools", "Code review automation",
        "Developer experience tooling",
    ],
}


class TopicSelector:
    """Selects topics for content generation using weighted random sampling.

    This is a pure domain object — no I/O, no imports from infrastructure.
    """

    def __init__(self, categories: Optional[dict[str, list[str]]] = None) -> None:
        self._categories = categories or CATEGORIES

    def pick(self, category: Optional[str] = None) -> tuple[str, str]:
        """Return (topic, category). If *category* is None a random one is chosen."""
        if category and category in self._categories:
            cat = category
        else:
            cat = random.choice(list(self._categories.keys()))

        topic = random.choice(self._categories[cat])
        return topic, cat

    def all_categories(self) -> list[str]:
        return list(self._categories.keys())

    def topics_for(self, category: str) -> list[str]:
        return list(self._categories.get(category, []))

    def total_topics(self) -> int:
        return sum(len(v) for v in self._categories.values())
