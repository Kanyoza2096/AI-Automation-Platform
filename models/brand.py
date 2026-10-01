"""Brand domain model — a distinct content identity within a workspace.

Each brand has its own:
  - Visual identity (logo, colors)
  - Voice (tone, language, audience)
  - Content strategy (hashtags, posting schedule, topics)
  - AI personality (linked AI Profile)
  - Connected social accounts

The content engine generates completely different content depending
on the brand context.

V1.6 — `logo_url` accepts NULL from Postgres.
V1.7 — Added `style_category` and `custom_style_guide` so brands can
       explicitly select or override a content style guide from the
       config/content_styles.yaml registry. When both are None, the
       PromptBuilder falls back to tone/audience/name inference.
V1.8 — Added `topic_direction` and `topic_axes` for direction-based
       AI topic generation. When topic_direction is set, the workflow
       generates topics from it rather than selecting from a fixed
       bank. Previously these columns existed on the brands table but
       were stripped by the model on load, so the selector never saw
       them and fell through to custom_topics.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import Field, field_validator

from models.base import KanyozaModel


class BrandStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class Brand(KanyozaModel):
    """
    A distinct content identity within a workspace.

    Example workspace "Kanyoza Systems" might have brands:
      - Tech News
      - AI Tips
      - Cyber Security
      - Python Tutorials
      - DevOps Daily
      - KDO Gospel
    """

    id: str = Field(..., min_length=1, description="Unique brand identifier")
    workspace_id: str = Field(..., min_length=1, description="Owning workspace")
    name: str = Field(..., min_length=1, max_length=200, description="Brand display name")
    slug: str = Field(..., min_length=1, max_length=100, description="URL-safe identifier")

    # Visual identity
    # ✅ V1.6 — logo_url is nullable in the DB; accept None explicitly.
    logo_url: Optional[str] = Field(default=None, description="Brand logo URL or path")
    colors: Dict[str, str] = Field(
        default_factory=lambda: {
            "primary": "#6366F1",
            "secondary": "#8B5CF6",
            "accent": "#06B6D4",
            "background": "#0F172A",
            "text": "#F8FAFC",
        },
        description="Brand colour palette (hex codes)",
    )

    # Voice & tone
    tone: str = Field(
        default="professional",
        description="Content tone: professional, casual, technical, friendly, "
                    "authoritative, playful, warm, devotional, pastoral, etc.",
    )
    language: str = Field(default="en", max_length=10, description="Primary content language (ISO 639-1)")
    audience_description: str = Field(
        default="Software developers and tech enthusiasts",
        description="Target audience for content generation",
    )

    # ✅ V1.7 — Explicit content style guide selection.
    # When set, this key selects a style guide from
    # config/content_styles.yaml. Overrides tone/audience inference.
    # Examples: "devotional", "professional", "casual", "culinary", "fitness"
    style_category: Optional[str] = Field(
        default=None,
        max_length=64,
        description="Explicit content style key (matches a key in "
                    "config/content_styles.yaml). Overrides tone/audience inference.",
    )

    # ✅ V1.7 — Full per-brand style guide override.
    # When set, this text is injected directly into the LLM prompt,
    # taking precedence over both style_category and any inferred style.
    # Use this for brands with a very specific voice the registry
    # cannot capture.
    custom_style_guide: Optional[str] = Field(
        default=None,
        max_length=4000,
        description="Custom style guide text. Overrides registry and inference "
                    "when present.",
    )

    # Content strategy
    hashtags: List[str] = Field(
        default_factory=lambda: ["TechTips", "Coding", "SoftwareEngineering"],
        description="Default hashtags (without # prefix)",
    )
    topic_categories: List[str] = Field(
        default_factory=list,
        description="Custom topic categories for this brand (empty = use global defaults)",
    )
    custom_topics: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="Brand-specific topic bank: {category: [topics]}",
    )

    # ✅ V1.8 — Direction-based content generation.
    #
    # When topic_direction is set, the workflow uses the AI fabric to
    # generate topics from it rather than selecting from custom_topics.
    # topic_axes decomposes the direction into thematic buckets the AI
    # picks from, keeping generated topics varied and coherent.
    #
    # Both are optional. When absent, the topic selector falls back to
    # custom_topics / KB / brand_config (existing behavior).
    topic_direction: Optional[str] = Field(
        default=None,
        max_length=2000,
        description="Voice and scope brief for AI-generated topics. When set, "
                    "the workflow generates topics from this direction rather "
                    "than selecting from custom_topics.",
    )

    topic_axes: Dict[str, str] = Field(
        default_factory=dict,
        description="Thematic axes for direction-based generation: "
                    "{axis_name: one-sentence description}. When empty, the "
                    "generator uses only topic_direction.",
    )

    # Scheduling
    posting_schedule: Dict[str, Any] = Field(
        default_factory=lambda: {
            "timezone": "UTC",
            "content_hours": [1, 3, 5, 7, 9, 11, 13, 15, 17, 19],
            "max_posts_per_day": 10,
            "min_interval_minutes": 90,
        },
        description="Brand-specific posting schedule",
    )

    # AI configuration
    ai_profile_id: Optional[str] = Field(
        default=None,
        description="Linked AI Profile ID (None = use workspace default)",
    )

    # State
    status: BrandStatus = Field(default=BrandStatus.ACTIVE)
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)
    updated_at: Optional[datetime] = Field(default=None)

    # Metadata for extensibility
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("hashtags", mode="before")
    @classmethod
    def strip_hash_prefix(cls, tags) -> List[str]:
        if isinstance(tags, list):
            return [t.lstrip("#").strip() for t in tags if t.strip()]
        return tags

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: str) -> str:
        import re

        cleaned = re.sub(r"[^a-z0-9-]", "", v.lower())
        if not cleaned:
            raise ValueError("slug must contain at least one alphanumeric character")
        return cleaned

    @field_validator("style_category", mode="before")
    @classmethod
    def normalize_style_category(cls, v):
        if v is None:
            return None
        if not isinstance(v, str):
            return v
        s = v.strip().lower()
        return s or None

    @property
    def is_active(self) -> bool:
        return self.status == BrandStatus.ACTIVE

    @property
    def formatted_hashtags(self) -> str:
        """Return space-separated hashtag string with # prefix."""
        return " ".join(f"#{tag}" for tag in self.hashtags)

    @property
    def timezone(self) -> str:
        return self.posting_schedule.get("timezone", "UTC")

    @property
    def content_hours(self) -> List[int]:
        return self.posting_schedule.get("content_hours", [])

    @property
    def max_posts_per_day(self) -> int:
        return self.posting_schedule.get("max_posts_per_day", 10)

    @property
    def has_explicit_style(self) -> bool:
        """True when the brand either names a style or provides its own guide."""
        return bool(self.style_category) or bool(
            self.custom_style_guide and self.custom_style_guide.strip()
        )

    @property
    def has_direction(self) -> bool:
        """True when the brand declares an AI-topic direction."""
        return bool(self.topic_direction and self.topic_direction.strip())

    @classmethod
    def create_default(cls, workspace_id: str, brand_id: str = "default") -> "Brand":
        """Create a default brand for backwards compatibility."""
        return cls(
            id=brand_id,
            workspace_id=workspace_id,
            name="Default Brand",
            slug="default",
        )
