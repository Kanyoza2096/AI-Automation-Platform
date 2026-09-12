"""
Brand domain model — a distinct content identity within a workspace.

Each brand has its own:
  - Visual identity (logo, colors)
  - Voice (tone, language, audience)
  - Content strategy (hashtags, posting schedule, topics)
  - AI personality (linked AI Profile)
  - Connected social accounts

The content engine generates completely different content depending
on the brand context.
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
    """

    id: str = Field(..., min_length=1, description="Unique brand identifier")
    workspace_id: str = Field(..., min_length=1, description="Owning workspace")
    name: str = Field(..., min_length=1, max_length=200, description="Brand display name")
    slug: str = Field(..., min_length=1, max_length=100, description="URL-safe identifier")

    # Visual identity
    logo_url: str = Field(default="", description="Brand logo URL or path")
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
        description="Content tone: professional, casual, technical, friendly, authoritative, playful",
    )
    language: str = Field(default="en", max_length=10, description="Primary content language (ISO 639-1)")
    audience_description: str = Field(
        default="Software developers and tech enthusiasts",
        description="Target audience for content generation",
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

    @classmethod
    def create_default(cls, workspace_id: str, brand_id: str = "default") -> "Brand":
        """Create a default brand for backwards compatibility."""
        return cls(
            id=brand_id,
            workspace_id=workspace_id,
            name="Default Brand",
            slug="default",
        )
