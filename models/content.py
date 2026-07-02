"""
Content domain models — TechPost and related types.

Gemini returns one structured JSON payload; it is parsed into a TechPost once
and never re-fetched for the same post lifecycle.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import Field, field_validator

from models.base import KanyozaModel


class PostTheme(str, Enum):
    """Visual template theme for rendered post images."""

    COMPARISON = "comparison"
    TUTORIAL = "tutorial"
    CHECKLIST = "checklist"
    ARCHITECTURE = "architecture"
    CODE_TIPS = "code_tips"
    AI = "ai"
    SECURITY = "security"
    DATABASE = "database"
    BACKEND = "backend"
    FRONTEND = "frontend"
    SYSTEM_DESIGN = "system_design"
    CLOUD = "cloud"


class PostFormat(str, Enum):
    """Distribution format for a post."""

    IMAGE = "image"
    TEXT = "text"
    CAROUSEL = "carousel"
    VIDEO = "video"


class TechPost(KanyozaModel):
    """
    A fully generated technology post.

    Populated exactly once from a single Gemini response.
    All downstream services (rendering, publishing, analytics) consume this model.
    """

    # Core content — populated from Gemini structured JSON
    title: str = Field(..., min_length=1, max_length=300, description="Post headline")
    caption: str = Field(..., min_length=1, description="Main body / caption text")
    option_a: str = Field(default="", description="Comparison option A (debate posts)")
    option_b: str = Field(default="", description="Comparison option B (debate posts)")
    winner: str = Field(default="", description="Which option wins and why")
    points_a: List[str] = Field(default_factory=list, description="Arguments for option A")
    points_b: List[str] = Field(default_factory=list, description="Arguments for option B")
    hashtags: List[str] = Field(default_factory=list, description="Hashtags without #")

    # Rendering metadata
    theme: PostTheme = Field(default=PostTheme.AI)
    format: PostFormat = Field(default=PostFormat.IMAGE)

    # Publishing state (optional — filled after scheduling / posting)
    image_url: Optional[str] = Field(default=None, description="Rendered image URL or path")
    post_id: Optional[str] = Field(default=None, description="Platform post ID after publish")
    published_at: Optional[datetime] = Field(default=None)
    platform: Optional[str] = Field(default=None, description="facebook | instagram | etc.")

    # Tracing
    correlation_id: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)

    @field_validator("hashtags", mode="before")
    @classmethod
    def strip_hash_prefix(cls, tags: List[str]) -> List[str]:
        """Normalize hashtags — ensure they are stored without leading #."""
        return [t.lstrip("#").strip() for t in tags if t.strip()]

    @property
    def formatted_hashtags(self) -> str:
        """Return space-separated hashtag string with # prefix."""
        return " ".join(f"#{tag}" for tag in self.hashtags)

    @property
    def is_debate(self) -> bool:
        """True when this post is a structured debate / comparison."""
        return bool(self.option_a and self.option_b)

    @property
    def is_published(self) -> bool:
        return self.post_id is not None

    @classmethod
    def from_gemini_response(
        cls,
        data: dict,
        theme: PostTheme = PostTheme.AI,
        correlation_id: Optional[str] = None,
    ) -> "TechPost":
        """
        Parse a raw Gemini structured JSON dict into a TechPost.

        Expected keys: title, caption, option_a, option_b, winner,
                       points_a, points_b, hashtags
        """
        return cls(
            title=data.get("title", ""),
            caption=data.get("caption", ""),
            option_a=data.get("option_a", ""),
            option_b=data.get("option_b", ""),
            winner=data.get("winner", ""),
            points_a=data.get("points_a", []),
            points_b=data.get("points_b", []),
            hashtags=data.get("hashtags", []),
            theme=theme,
            correlation_id=correlation_id,
        )
