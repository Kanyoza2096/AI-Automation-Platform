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
       bank.
V1.9 — Hardened hydration against legacy NULLs and stringified JSONB.
       Every nullable-ish column (topic_axes, topic_direction, colors,
       posting_schedule, custom_topics, topic_categories, hashtags,
       logo_url, style_category, custom_style_guide) now coerces NULL,
       empty strings, and JSON-encoded strings to a safe value. This
       fixes the `find_all` validation error where a single row with
       topic_axes=NULL caused the entire brand list to be rejected.

V2.0 — Double-hash defense on formatted_hashtags.
       `_coerce_and_strip_hashtags` strips `#` from tags on the way in,
       but Pydantic field_validators only run on the constructor. Any
       subsequent mutation (`brand.hashtags = [...]`, `model_copy(update=...)`,
       `model_construct(**data)`) bypasses the validator. If any of those
       paths supply tags that still carry `#`, `formatted_hashtags` was
       producing `##engineering` instead of `#engineering`. Fix:
         - `formatted_hashtags` now strips `#` defensively before
           prefixing. This is the last line of defense and makes the
           property correct regardless of how `self.hashtags` was set.
         - `model_config` now has `validate_assignment=True` so future
           direct-assignment mutations go through the validator.
       Behavior for correctly-constructed brands is unchanged.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import ConfigDict, Field, field_validator

from models.base import KanyozaModel


# ═══════════════════════════════════════════════════════════════════════════
# Coercion helpers — tolerate legacy row shapes without raising
# ═══════════════════════════════════════════════════════════════════════════
#
# Supabase returns JSONB columns either as native dicts/lists or as
# JSON-encoded strings, depending on driver version and column type
# inference. Columns added without a DEFAULT can also come back as NULL
# for pre-migration rows. Every field with a non-primitive type gets a
# coercer so that `Brand(**row)` never raises on a row that is merely
# shaped differently than the model expects.
#

def _coerce_dict(value: Any) -> Dict[str, Any]:
    """Coerce a value to a dict. Returns {} for None / malformed input."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items()}
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return {}
        try:
            parsed = json.loads(stripped)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _coerce_str_dict(value: Any) -> Dict[str, str]:
    """Coerce a value to Dict[str, str]. Drops None-valued entries."""
    base = _coerce_dict(value)
    return {str(k): str(v) for k, v in base.items() if v is not None}


def _coerce_list(value: Any) -> List[Any]:
    """Coerce a value to a list. Returns [] for None / malformed input."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
        except Exception:
            # Fall back to comma-separated parsing; harmless for
            # hashtag-like inputs, empty list for anything else.
            return [p.strip() for p in stripped.split(",") if p.strip()]
        return parsed if isinstance(parsed, list) else []
    return []


def _coerce_str_list(value: Any) -> List[str]:
    """Coerce a value to List[str]. Drops empty / non-string entries."""
    return [str(v).strip() for v in _coerce_list(value) if str(v).strip()]


def _coerce_optional_str(value: Any) -> Optional[str]:
    """Coerce a value to Optional[str]. Empty / whitespace → None."""
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        return s or None
    return str(value)


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

    # V2.0 — validate_assignment=True so that direct mutations of fields
    # (brand.hashtags = [...], brand.style_category = "...") go through
    # field_validators. Without this, only constructor-passed values are
    # validated. Inherit the base config and extend it.
    model_config = ConfigDict(
        **getattr(KanyozaModel, "model_config", {}),
        validate_assignment=True,
    )

    id: str = Field(..., min_length=1, description="Unique brand identifier")
    workspace_id: str = Field(..., min_length=1, description="Owning workspace")
    name: str = Field(..., min_length=1, max_length=200, description="Brand display name")
    slug: str = Field(..., min_length=1, max_length=100, description="URL-safe identifier")

    # Visual identity
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
    language: str = Field(
        default="en",
        max_length=10,
        description="Primary content language (ISO 639-1)",
    )
    audience_description: str = Field(
        default="Software developers and tech enthusiasts",
        description="Target audience for content generation",
    )

    # Explicit content style guide selection
    style_category: Optional[str] = Field(
        default=None,
        max_length=64,
        description="Explicit content style key (matches a key in "
                    "config/content_styles.yaml). Overrides tone/audience inference.",
    )

    # Full per-brand style guide override
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

    # Direction-based content generation
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

    # ═══════════════════════════════════════════════════════════════════════
    # Validators — all mode="before" so they run on raw row input
    # ═══════════════════════════════════════════════════════════════════════

    @field_validator("logo_url", mode="before")
    @classmethod
    def _coerce_logo_url(cls, v):
        return _coerce_optional_str(v)

    @field_validator("style_category", mode="before")
    @classmethod
    def _coerce_style_category(cls, v):
        s = _coerce_optional_str(v)
        return s.lower() if s else None

    @field_validator("custom_style_guide", mode="before")
    @classmethod
    def _coerce_custom_style_guide(cls, v):
        return _coerce_optional_str(v)

    @field_validator("ai_profile_id", mode="before")
    @classmethod
    def _coerce_ai_profile_id(cls, v):
        return _coerce_optional_str(v)

    @field_validator("topic_direction", mode="before")
    @classmethod
    def _coerce_topic_direction(cls, v):
        """Tolerate NULL, empty string, or non-str from legacy rows."""
        return _coerce_optional_str(v)

    @field_validator("topic_axes", mode="before")
    @classmethod
    def _coerce_topic_axes(cls, v):
        """Tolerate NULL, empty string, JSON-encoded string, or dict.

        This is the fix for the `find_all` validation error where a row
        with topic_axes=NULL caused the entire brand list to be rejected
        by Pydantic. Legacy rows (predating the topic_axes column) hold
        NULL; the coercer converts them to {} so they hydrate cleanly.
        """
        return _coerce_str_dict(v)

    @field_validator("colors", mode="before")
    @classmethod
    def _coerce_colors(cls, v):
        """Tolerate NULL or partial colors dict; defaults fill missing keys."""
        if v is None:
            return {
                "primary": "#6366F1",
                "secondary": "#8B5CF6",
                "accent": "#06B6D4",
                "background": "#0F172A",
                "text": "#F8FAFC",
            }
        coerced = _coerce_str_dict(v)
        if not coerced:
            return {
                "primary": "#6366F1",
                "secondary": "#8B5CF6",
                "accent": "#06B6D4",
                "background": "#0F172A",
                "text": "#F8FAFC",
            }
        return coerced

    @field_validator("posting_schedule", mode="before")
    @classmethod
    def _coerce_posting_schedule(cls, v):
        """Tolerate NULL / malformed posting_schedule; apply defaults."""
        default = {
            "timezone": "UTC",
            "content_hours": [1, 3, 5, 7, 9, 11, 13, 15, 17, 19],
            "max_posts_per_day": 10,
            "min_interval_minutes": 90,
        }
        if v is None:
            return default
        coerced = _coerce_dict(v)
        if not coerced:
            return default
        merged = dict(default)
        merged.update(coerced)
        return merged

    @field_validator("custom_topics", mode="before")
    @classmethod
    def _coerce_custom_topics(cls, v):
        """Tolerate NULL / JSON-string; ensure Dict[str, List[str]]."""
        base = _coerce_dict(v)
        return {str(k): _coerce_str_list(val) for k, val in base.items()}

    @field_validator("topic_categories", mode="before")
    @classmethod
    def _coerce_topic_categories(cls, v):
        return _coerce_str_list(v)

    @field_validator("hashtags", mode="before")
    @classmethod
    def _coerce_and_strip_hashtags(cls, tags):
        """Tolerate NULL / JSON-string; strip leading # from each tag.

        V2.0 — This is the primary strip. It runs on construction and, now
        that model_config has validate_assignment=True, also on direct
        attribute assignment. formatted_hashtags strips again defensively
        for model_construct and model_copy paths that bypass validators.
        """
        coerced = _coerce_str_list(tags)
        return [t.lstrip("#").strip() for t in coerced if t.lstrip("#").strip()]

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: str) -> str:
        cleaned = re.sub(r"[^a-z0-9-]", "", v.lower())
        if not cleaned:
            raise ValueError("slug must contain at least one alphanumeric character")
        return cleaned

    @field_validator("status", mode="before")
    @classmethod
    def _coerce_status(cls, v):
        if v is None:
            return BrandStatus.ACTIVE
        if isinstance(v, BrandStatus):
            return v
        s = str(v).strip().lower()
        if s in ("active", "paused", "archived"):
            return s
        return BrandStatus.ACTIVE

    # ═══════════════════════════════════════════════════════════════════════
    # Properties
    # ═══════════════════════════════════════════════════════════════════════

    @property
    def is_active(self) -> bool:
        return self.status == BrandStatus.ACTIVE

    @property
    def formatted_hashtags(self) -> str:
        """Return space-separated hashtag string with # prefix.

        V2.0 — Strips `#` defensively before prefixing. The
        `_coerce_and_strip_hashtags` validator strips on construction,
        but three paths bypass it:
          - `Brand.model_construct(**data)` (Pydantic fast path — no
            validators run)
          - `Brand.model_copy(update={"hashtags": [...]})` (mutates
            without re-validation)
          - direct mutation when validate_assignment was off (pre-V2.0)
        Any of those could leave `self.hashtags` containing tags that
        still carry `#`, which would produce `##engineering` here. This
        strip makes the property correct regardless of how `self.hashtags`
        was populated.

        For correctly-constructed brands, this produces exactly the same
        output as before V2.0 — the strip is a no-op on already-stripped
        tags.
        """
        return " ".join(
            f"#{str(tag).lstrip('#').strip()}"
            for tag in self.hashtags
            if str(tag).lstrip("#").strip()
        )

    @property
    def timezone(self) -> str:
        return self.posting_schedule.get("timezone", "UTC")

    @property
    def content_hours(self) -> List[int]:
        return list(self.posting_schedule.get("content_hours") or [])

    @property
    def max_posts_per_day(self) -> int:
        try:
            return int(self.posting_schedule.get("max_posts_per_day", 10))
        except (TypeError, ValueError):
            return 10

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

    # ═══════════════════════════════════════════════════════════════════════
    # Factories
    # ═══════════════════════════════════════════════════════════════════════

    @classmethod
    def create_default(cls, workspace_id: str, brand_id: str = "default") -> "Brand":
        """Create a default brand for backwards compatibility."""
        return cls(
            id=brand_id,
            workspace_id=workspace_id,
            name="Default Brand",
            slug="default",
        )

    @classmethod
    def default(cls) -> "Brand":
        """Alias used by ContentEngine.generate_post_legacy and similar callers."""
        return cls.create_default("default")
