"""
Content domain models — TechPost and related types.

Gemini returns one structured JSON payload; it is parsed into a TechPost once
and never re-fetched for the same post lifecycle.

V2.1 (2026-10-01) — TOLERANT FIELD MAPPING
  - from_gemini_response accepts a documented alias set for 'title' and
    'caption'. Models occasionally drift (return 'headline' for 'title',
    'body' for 'caption'). This layer absorbs that drift with WARN logs so
    the pipeline does not degrade on well-formed-but-mis-named payloads.
  - The strict schema prompt (POST_SCHEMA_PROMPT_BLOCK) is the primary
    defense; this is the secondary absorption layer.
"""

from __future__ import annotations

import logging
from datetime import datetime
from enum import Enum
from typing import Any, List, Mapping, Optional, Sequence

from pydantic import Field, field_validator

from models.base import KanyozaModel

logger = logging.getLogger("kanyoza.models.content")


# Alias sets for drift tolerance. Canonical key is first.
_TITLE_ALIASES: Sequence[str] = ("title", "headline", "subject", "heading", "post_title", "name")
_CAPTION_ALIASES: Sequence[str] = ("caption", "body", "content", "text", "post_body", "message", "post")


def _coerce_str(value: Any) -> str:
    """Coerce a JSON scalar to a stripped string. Returns '' for None."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    return ""


def _first_alias(
    data: Mapping[str, Any],
    aliases: Sequence[str],
    *,
    field_label: str,
) -> str:
    """Return the first present alias's coerced string.

    Logs WARN when the canonical key (aliases[0]) is absent but an alias
    supplies the value. This is how model drift becomes visible in aggregate.
    """
    canonical = aliases[0]
    if canonical in data:
        return _coerce_str(data[canonical])
    for alt in aliases[1:]:
        if alt in data:
            logger.warning(
                "Schema drift: field %r missing; using alias %r (present keys=%s)",
                field_label, alt, sorted(data.keys()),
            )
            return _coerce_str(data[alt])
    return ""


def _coerce_str_list(value: Any) -> List[str]:
    """Normalize a hashtags-like field to a list of stripped strings."""
    if value is None:
        return []
    if isinstance(value, str):
        parts = [p.strip() for p in value.replace(";", ",").split(",")]
    elif isinstance(value, (list, tuple, set)):
        parts = [_coerce_str(v) for v in value]
    else:
        return []
    seen: set[str] = set()
    out: List[str] = []
    for p in parts:
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


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

    left_content: str = Field(default="", description="What makes option A unique")
    right_content: str = Field(default="", description="What makes option B unique")
    tradeoff: str = Field(default="", description="Biggest engineering compromise between the two")
    best_for: str = Field(default="", description="Which users/use-cases each option serves best")
    verdict: str = Field(default="", description="Objective conclusion, no repetition of earlier points")
    recommendation_title: str = Field(default="Recommendation", description="Label for the recommendation box")
    recommendation: str = Field(default="", description="Recommended option, or 'depends on priorities'")

    theme: PostTheme = Field(default=PostTheme.AI)
    format: PostFormat = Field(default=PostFormat.IMAGE)

    image_url: Optional[str] = Field(default=None, description="Rendered image URL or path")
    post_id: Optional[str] = Field(default=None, description="Platform post ID after publish")
    published_at: Optional[datetime] = Field(default=None)
    platform: Optional[str] = Field(default=None, description="facebook | instagram | etc.")

    correlation_id: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)

    @field_validator("hashtags", mode="before")
    @classmethod
    def strip_hash_prefix(cls, tags):
        """Normalize hashtags — ensure they are stored without leading #."""
        if tags is None:
            return []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.replace(";", ",").split(",")]
        return [str(t).lstrip("#").strip() for t in tags if str(t).strip()]

    @property
    def formatted_hashtags(self) -> str:
        """Return space-separated hashtag string with # prefix."""
        return " ".join(f"#{tag}" for tag in self.hashtags)

    @property
    def is_debate(self) -> bool:
        """True when this post is a structured debate / comparison."""
        return bool(self.option_a and self.option_b)

    @property
    def is_structured_comparison(self) -> bool:
        return bool(self.tradeoff and self.best_for and self.verdict)

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
        Parse a raw model JSON payload into a TechPost.

        Supports two schemas:

        1. Legacy free-form — keys: title, caption, option_a, option_b,
           winner, points_a, points_b, hashtags.

        2. Balanced structured-comparison — keys: title, left_title,
           left_content, right_title, right_content, tradeoff, best_for,
           verdict, recommendation_title, recommendation, hashtags. No
           "caption" key; one is synthesized from the structured fields.

        Field-name drift: `title` and `caption` accept a documented alias
        set. When an alias supplies the value, a WARN is logged. This is
        the parser-side complement to POST_SCHEMA_PROMPT_BLOCK in
        services/prompt_builder.py.

        Raises:
            ValueError: when neither canonical nor alias keys produce a
                non-empty title or caption. The caller (content engine)
                treats this as a structured-generation failure and falls
                through to prose generation.
        """
        if not isinstance(data, Mapping):
            raise ValueError(
                f"TechPost.from_gemini_response expected a mapping, got {type(data).__name__}"
            )

        is_structured = "tradeoff" in data or "left_content" in data

        if is_structured:
            left_title = _coerce_str(data.get("left_title")) or _coerce_str(data.get("option_a"))
            right_title = _coerce_str(data.get("right_title")) or _coerce_str(data.get("option_b"))
            left_content = _coerce_str(data.get("left_content"))
            right_content = _coerce_str(data.get("right_content"))
            tradeoff = _coerce_str(data.get("tradeoff"))
            best_for = _coerce_str(data.get("best_for"))
            verdict = _coerce_str(data.get("verdict"))
            recommendation = _coerce_str(data.get("recommendation"))

            title = _first_alias(data, _TITLE_ALIASES, field_label="title")
            caption = _first_alias(data, _CAPTION_ALIASES, field_label="caption")
            if not caption:
                caption = " ".join(
                    p for p in (left_content, right_content, tradeoff, verdict) if p
                )

            if not title:
                raise ValueError(
                    "Structured payload missing a usable 'title' "
                    f"(aliases tried: {list(_TITLE_ALIASES)}); keys={sorted(data.keys())}"
                )
            if not caption:
                raise ValueError(
                    "Structured payload missing a usable 'caption' "
                    f"(aliases tried: {list(_CAPTION_ALIASES)}); keys={sorted(data.keys())}"
                )

            return cls(
                title=title,
                caption=caption,
                option_a=left_title,
                option_b=right_title,
                left_content=left_content,
                right_content=right_content,
                tradeoff=tradeoff,
                best_for=best_for,
                verdict=verdict,
                recommendation_title=(
                    _coerce_str(data.get("recommendation_title")) or "Recommendation"
                ),
                recommendation=recommendation,
                hashtags=_coerce_str_list(data.get("hashtags")),
                theme=theme,
                correlation_id=correlation_id,
            )

        # ── Legacy free-form path ─────────────────────────────────────
        title = _first_alias(data, _TITLE_ALIASES, field_label="title")
        caption = _first_alias(data, _CAPTION_ALIASES, field_label="caption")

        if not title:
            raise ValueError(
                "Structured payload missing a usable 'title' "
                f"(aliases tried: {list(_TITLE_ALIASES)}); keys={sorted(data.keys())}"
            )
        if not caption:
            raise ValueError(
                "Structured payload missing a usable 'caption' "
                f"(aliases tried: {list(_CAPTION_ALIASES)}); keys={sorted(data.keys())}"
            )

        return cls(
            title=title,
            caption=caption,
            option_a=_coerce_str(data.get("option_a")),
            option_b=_coerce_str(data.get("option_b")),
            winner=_coerce_str(data.get("winner")),
            points_a=_coerce_str_list(data.get("points_a")),
            points_b=_coerce_str_list(data.get("points_b")),
            hashtags=_coerce_str_list(data.get("hashtags")),
            theme=theme,
            correlation_id=correlation_id,
        )


__all__ = ["TechPost", "PostTheme", "PostFormat"]
