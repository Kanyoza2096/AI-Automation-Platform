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

V1.8 (2026-09-28) — UNIVERSAL DESIGN:
  - `complexity` and `emoji_level` are now free-form strings, mirroring
    the DB schema (both columns are `text` with no constraint).
  - Canonical enums live *inside* the model as `CanonicalComplexity` and
    `CanonicalEmojiLevel` — used internally by build_system_prompt() and
    by any business logic that needs type-safe values.
  - Normalizer aliases live at module level (`_COMPLEXITY_ALIASES`,
    `_EMOJI_ALIASES`). Adding a new alias or canonical value is a one-line
    change; unknown values never raise.
  - Properties `.canonical_complexity` / `.canonical_emoji_level` give
    callers a type-safe view without constraining what the DB can store.
  - This ends the entire class of "Pydantic enum vs. drifting DB text"
    validation failures. The model never rejects a row.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import Field

from models.base import KanyozaModel


# ═══════════════════════════════════════════════════════════════════════════
# Canonical vocabularies — internal only, not enforced on input
# ═══════════════════════════════════════════════════════════════════════════

class CanonicalComplexity(str, Enum):
    """Canonical audience complexity levels.

    This is what the model *interprets* `complexity` as. The raw string
    in the DB can be anything — normalizers map it here.
    """
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    EXPERT = "expert"


class CanonicalEmojiLevel(str, Enum):
    """Canonical emoji density levels.

    Same pattern as CanonicalComplexity — internal interpretation of a
    free-form input string.
    """
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# ═══════════════════════════════════════════════════════════════════════════
# Normalizer alias maps — one line to add a new alias
# ═══════════════════════════════════════════════════════════════════════════

_COMPLEXITY_ALIASES: Dict[str, CanonicalComplexity] = {
    # Beginner family
    "simple": CanonicalComplexity.BEGINNER,
    "basic": CanonicalComplexity.BEGINNER,
    "easy": CanonicalComplexity.BEGINNER,
    "intro": CanonicalComplexity.BEGINNER,
    "introductory": CanonicalComplexity.BEGINNER,
    "novice": CanonicalComplexity.BEGINNER,
    "starter": CanonicalComplexity.BEGINNER,
    # Intermediate family
    "medium": CanonicalComplexity.INTERMEDIATE,
    "med": CanonicalComplexity.INTERMEDIATE,
    "normal": CanonicalComplexity.INTERMEDIATE,
    "mid": CanonicalComplexity.INTERMEDIATE,
    "standard": CanonicalComplexity.INTERMEDIATE,
    "default": CanonicalComplexity.INTERMEDIATE,
    # Advanced family
    "hard": CanonicalComplexity.ADVANCED,
    "complex": CanonicalComplexity.ADVANCED,
    "high": CanonicalComplexity.ADVANCED,
    "senior": CanonicalComplexity.ADVANCED,
    "deep": CanonicalComplexity.ADVANCED,
    # Expert family
    "pro": CanonicalComplexity.EXPERT,
    "guru": CanonicalComplexity.EXPERT,
    "master": CanonicalComplexity.EXPERT,
    "principal": CanonicalComplexity.EXPERT,
}

_EMOJI_ALIASES: Dict[str, CanonicalEmojiLevel] = {
    # None family
    "no": CanonicalEmojiLevel.NONE,
    "off": CanonicalEmojiLevel.NONE,
    "disabled": CanonicalEmojiLevel.NONE,
    "zero": CanonicalEmojiLevel.NONE,
    # Low family
    "minimal": CanonicalEmojiLevel.LOW,
    "sparse": CanonicalEmojiLevel.LOW,
    "few": CanonicalEmojiLevel.LOW,
    "subtle": CanonicalEmojiLevel.LOW,
    # Medium family
    "moderate": CanonicalEmojiLevel.MEDIUM,
    "some": CanonicalEmojiLevel.MEDIUM,
    "normal": CanonicalEmojiLevel.MEDIUM,
    "default": CanonicalEmojiLevel.MEDIUM,
    "standard": CanonicalEmojiLevel.MEDIUM,
    # High family
    "many": CanonicalEmojiLevel.HIGH,
    "lots": CanonicalEmojiLevel.HIGH,
    "heavy": CanonicalEmojiLevel.HIGH,
    "frequent": CanonicalEmojiLevel.HIGH,
    "playful": CanonicalEmojiLevel.HIGH,
}


def _normalize(raw: Any, aliases: Dict[str, Any], canonical_cls, default):
    """
    Normalize a raw input against an alias map and a canonical enum.

    Resolution order:
      1. None / empty → default
      2. Exact canonical value → canonical enum
      3. Alias match → mapped canonical enum
      4. Unknown → default (never raises)
    """
    if raw is None:
        return default
    s = str(raw).strip().lower()
    if not s:
        return default
    # Try canonical first (fast path for the common case)
    try:
        return canonical_cls(s)
    except ValueError:
        pass
    # Then alias lookup
    if s in aliases:
        return aliases[s]
    # Unknown — safe default, no crash
    return default


# ═══════════════════════════════════════════════════════════════════════════
# AIProfile model — free-form, never rejects a row
# ═══════════════════════════════════════════════════════════════════════════

class AIProfile(KanyozaModel):
    """
    A reusable AI personality configuration.

    Linked to brands via Brand.ai_profile_id. Multiple brands can
    share the same profile, or each can have its own.

    `complexity` and `emoji_level` are free-form strings that mirror the
    DB schema. Use `.canonical_complexity` and `.canonical_emoji_level`
    for type-safe interpretation.
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

    # Content behaviour — free-form text fields mirror DB columns
    cta_style: str = Field(
        default="question",
        description="Call-to-action approach: question, challenge, share, save, none",
    )
    emoji_level: str = Field(
        default="medium",
        description="Emoji density. Free-form; normalized via .canonical_emoji_level",
    )
    complexity: str = Field(
        default="intermediate",
        description="Target audience level. Free-form; normalized via .canonical_complexity",
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

    # ═══════════════════════════════════════════════════════════════════
    # Canonical views — type-safe interpretation without constraining input
    # ═══════════════════════════════════════════════════════════════════

    @property
    def canonical_complexity(self) -> CanonicalComplexity:
        """Interpret `complexity` as a canonical enum. Never raises."""
        return _normalize(
            self.complexity,
            _COMPLEXITY_ALIASES,
            CanonicalComplexity,
            CanonicalComplexity.INTERMEDIATE,
        )

    @property
    def canonical_emoji_level(self) -> CanonicalEmojiLevel:
        """Interpret `emoji_level` as a canonical enum. Never raises."""
        return _normalize(
            self.emoji_level,
            _EMOJI_ALIASES,
            CanonicalEmojiLevel,
            CanonicalEmojiLevel.MEDIUM,
        )

    # ═══════════════════════════════════════════════════════════════════
    # System prompt builder
    # ═══════════════════════════════════════════════════════════════════

    def build_system_prompt(self, brand_name: str = "", audience: str = "") -> str:
        """
        Generate a system prompt from this profile's fields.

        If `system_prompt_override` is set, returns it directly.
        Otherwise assembles a prompt from the individual fields.
        """
        if self.system_prompt_override:
            return self.system_prompt_override

        emoji = self.canonical_emoji_level
        complexity = self.canonical_complexity

        emoji_instruction = {
            CanonicalEmojiLevel.NONE: "Do not use any emojis.",
            CanonicalEmojiLevel.LOW: "Use emojis sparingly — at most 1-2 per post.",
            CanonicalEmojiLevel.MEDIUM: "Use emojis moderately to add visual interest.",
            CanonicalEmojiLevel.HIGH: "Use emojis liberally to make content engaging and lively.",
        }[emoji]

        complexity_instruction = {
            CanonicalComplexity.BEGINNER: "Explain concepts simply, assuming no prior knowledge.",
            CanonicalComplexity.INTERMEDIATE: "Assume basic technical knowledge. Use some jargon but explain complex concepts.",
            CanonicalComplexity.ADVANCED: "Write for experienced practitioners. Use technical terminology freely.",
            CanonicalComplexity.EXPERT: "Write for domain experts. Deep technical analysis with nuanced trade-offs.",
        }[complexity]

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

    # ═══════════════════════════════════════════════════════════════════
    # Presets
    # ═══════════════════════════════════════════════════════════════════

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
            emoji_level="medium",
            complexity="intermediate",
            writing_style="structured comparisons with clear pros/cons and a decisive recommendation",
        )


# ═══════════════════════════════════════════════════════════════════════════
# Preset profiles for common use cases
# ═══════════════════════════════════════════════════════════════════════════

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
    "gospel_writer": {
        "name": "Gospel Writer",
        "expertise": "Christian devotional writing, scripture interpretation, and pastoral encouragement",
        "tone": "warm",
        "style": "reverent and encouraging",
        "complexity": "beginner",
        "emoji_level": "low",
    },
}
