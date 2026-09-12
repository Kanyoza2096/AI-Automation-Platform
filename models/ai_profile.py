"""
AI Profile domain model — configurable AI personality.

Instead of one hardcoded Gemini personality, each brand can own a
custom AI Profile that controls how content is generated:

  - Senior Backend Engineer
  - Cybersecurity Expert
  - Cloud Architect
  - Python Teacher
  - Startup Mentor

Each profile defines tone, expertise, writing style, CTA approach,
emoji usage, and an optional custom prompt template.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import Field

from models.base import KanyozaModel


class EmojiLevel(str, Enum):
    """Controls emoji density in generated content."""
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ContentComplexity(str, Enum):
    """Target audience complexity level."""
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    EXPERT = "expert"


class AIProfile(KanyozaModel):
    """
    A reusable AI personality configuration.

    Linked to brands via Brand.ai_profile_id. Multiple brands can
    share the same profile, or each can have its own.
    """

    id: str = Field(..., min_length=1, description="Unique profile identifier")
    workspace_id: str = Field(..., min_length=1, description="Owning workspace")
    name: str = Field(..., min_length=1, max_length=200, description="Profile display name")

    # Core identity
    expertise: str = Field(
        default="Software engineering, system design, and modern technology",
        description="Areas of expertise for content generation",
    )
    tone: str = Field(
        default="professional",
        description="Communication tone: professional, casual, authoritative, friendly, witty, academic",
    )
    style: str = Field(
        default="clear and structured",
        description="Writing style: concise, detailed, conversational, academic, storytelling",
    )

    # Content behaviour
    cta_style: str = Field(
        default="question",
        description="Call-to-action approach: question, challenge, share, save, none",
    )
    emoji_level: EmojiLevel = Field(
        default=EmojiLevel.MEDIUM,
        description="Emoji density in content",
    )
    complexity: ContentComplexity = Field(
        default=ContentComplexity.INTERMEDIATE,
        description="Target audience technical level",
    )
    writing_style: str = Field(
        default="structured with bullet points and clear sections",
        description="Specific writing instructions",
    )

    # Custom prompt template (optional — overrides the default prompt builder)
    prompt_template: str = Field(
        default="",
        description="Custom Jinja2-compatible prompt template. Empty = use default builder.",
    )
    system_prompt_override: str = Field(
        default="",
        description="Full system prompt override. Empty = auto-generate from fields above.",
    )

    # Metadata
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)
    updated_at: Optional[datetime] = Field(default=None)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def build_system_prompt(self, brand_name: str = "", audience: str = "") -> str:
        """
        Generate a system prompt from this profile's fields.

        If system_prompt_override is set, return it directly.
        Otherwise, assemble a prompt from the individual fields.
        """
        if self.system_prompt_override:
            return self.system_prompt_override

        emoji_instruction = {
            EmojiLevel.NONE: "Do not use any emojis.",
            EmojiLevel.LOW: "Use emojis sparingly — at most 1-2 per post.",
            EmojiLevel.MEDIUM: "Use emojis moderately to add visual interest.",
            EmojiLevel.HIGH: "Use emojis liberally to make content engaging and lively.",
        }[self.emoji_level]

        complexity_instruction = {
            ContentComplexity.BEGINNER: "Explain concepts simply, assuming no prior knowledge.",
            ContentComplexity.INTERMEDIATE: "Assume basic technical knowledge. Use some jargon but explain complex concepts.",
            ContentComplexity.ADVANCED: "Write for experienced practitioners. Use technical terminology freely.",
            ContentComplexity.EXPERT: "Write for domain experts. Deep technical analysis with nuanced trade-offs.",
        }[self.complexity]

        brand_context = f"You represent {brand_name}." if brand_name else ""
        audience_context = f"Your target audience: {audience}." if audience else ""

        return f"""You are a {self.name} — an expert in {self.expertise}.

Core personality:
- Tone: {self.tone}
- Style: {self.style}
- Writing approach: {self.writing_style}
- CTA style: {self.cta_style}

{brand_context}
{audience_context}

Content guidelines:
- {emoji_instruction}
- {complexity_instruction}
- Keep responses focused and actionable
- Never reveal your underlying AI model or training details
- For code: use proper markdown blocks with language identifiers
"""

    @classmethod
    def create_default(cls, workspace_id: str, profile_id: str = "default") -> "AIProfile":
        """Create a default AI profile matching the original KanyozaBot persona."""
        return cls(
            id=profile_id,
            workspace_id=workspace_id,
            name="Tech Comparison Expert",
            expertise="Software engineering, system design, databases, AI/ML, DevOps, and modern technology",
            tone="professional",
            style="clear and structured",
            cta_style="question",
            emoji_level=EmojiLevel.MEDIUM,
            complexity=ContentComplexity.INTERMEDIATE,
            writing_style="structured comparisons with clear pros/cons and a decisive recommendation",
        )


# ---------------------------------------------------------------------------
# Preset profiles for common use cases
# ---------------------------------------------------------------------------

PRESET_PROFILES = {
    "backend_engineer": {
        "name": "Senior Backend Engineer",
        "expertise": "Python, APIs, databases, system design, microservices, and backend architecture",
        "tone": "authoritative",
        "style": "technical and precise",
        "complexity": "advanced",
    },
    "cybersecurity_expert": {
        "name": "Cybersecurity Expert",
        "expertise": "Application security, penetration testing, encryption, OWASP, and threat modeling",
        "tone": "serious",
        "style": "alert and security-focused",
        "complexity": "advanced",
    },
    "cloud_architect": {
        "name": "Cloud Architect",
        "expertise": "AWS, GCP, Azure, Kubernetes, Terraform, CI/CD, and infrastructure design",
        "tone": "professional",
        "style": "architectural and strategic",
        "complexity": "expert",
    },
    "python_teacher": {
        "name": "Python Teacher",
        "expertise": "Python programming, best practices, libraries, and teaching concepts clearly",
        "tone": "friendly",
        "style": "educational and encouraging",
        "complexity": "beginner",
        "emoji_level": "high",
    },
    "startup_mentor": {
        "name": "Startup Mentor",
        "expertise": "Tech startups, MVPs, product-market fit, fundraising, and technical leadership",
        "tone": "motivational",
        "style": "practical and action-oriented",
        "complexity": "intermediate",
    },
    "devops_engineer": {
        "name": "DevOps Engineer",
        "expertise": "Docker, Kubernetes, CI/CD, monitoring, infrastructure as code, and SRE",
        "tone": "pragmatic",
        "style": "hands-on and operations-focused",
        "complexity": "advanced",
    },
    "database_specialist": {
        "name": "Database Specialist",
        "expertise": "PostgreSQL, MySQL, Redis, database design, indexing, replication, and query optimization",
        "tone": "meticulous",
        "style": "data-driven and performance-focused",
        "complexity": "expert",
    },
}
