"""
Content domain models — TechPost and related types.

Gemini returns one structured JSON payload; it is parsed into a TechPost once
and never re-fetched for the same post lifecycle.

V2.3 (2026-10-27) — TITLE OPTIONAL, HASHTAGS NOT FROM MODEL:

  Two changes that make TechPost represent both LinkedIn/blog posts (which
  have titles) and Facebook/Twitter/Instagram/WhatsApp captions (which
  don't):

  1. `title` IS NOW OPTIONAL.
     Previously: title: str = Field(..., min_length=1). Every post had to
     have a headline. That forced the model to produce LinkedIn-essay
     structure regardless of target platform — which was the root cause
     of "Designing for Distributed System Resilience: A Tradeoff" style
     output on a Facebook brand. Facebook/Twitter/Instagram/WhatsApp
     captions have no title; the first sentence IS the hook.

     Now: title: str = Field(default="", max_length=300). A TechPost with
     title="" is a caption-format post. CaptionBuilder (v15.6+) detects
     empty title and passes the caption through without the 🔥 title
     wrapper. LinkedIn/blog posts still supply a title when the platform
     contract asks for one.

  2. HASHTAGS ARE NOT TAKEN FROM THE MODEL.
     The prompt (prompt_builder V2.3) no longer requests hashtags. The
     parser tolerates a missing "hashtags" key (returns []) but does not
     fail if it's absent. CaptionBuilder._get_hashtags (v15.7) ignores
     post.hashtags entirely and returns brand.formatted_hashtags — because
     hashtag selection is a brand decision, not a per-post content
     decision. The field remains on TechPost for callers that set it
     programmatically (e.g. a test or a migration).

V2.2 (2026-10-10) — MULTI-VERTICAL THEME COVERAGE
  - PostTheme extended with non-technical themes (scripture_reflection,
    testimony, recipe, reflection) so devotional, culinary, and general
    brands have a home in the enum instead of being mis-tagged as
    tutorials.

V2.1 (2026-10-01) — TOLERANT FIELD MAPPING
  - from_gemini_response accepts a documented alias set for 'title' and
    'caption'. Models occasionally drift (return 'headline' for 'title',
    'body' for 'caption'). This layer absorbs that drift with WARN logs.
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
    """Visual template theme for rendered post images.

    Themes describe the shape and register of the content, not the domain
    it comes from. A "comparison" post about React and Vue and a
    "comparison" post about grace and works both route to the same
    comparison template. The brand voice guide in content_styles.yaml
    supplies the domain vocabulary; the theme supplies the form.

    The technical themes below are the original taxonomy. The devotional,
    culinary, and general themes were added as those verticals came online.
    Do not add themes faster than the renderer can support them — a theme
    with no matching template renders as the wrong card.
    """

    # ── Technical (original taxonomy) ─────────────────────────────────
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

    # ── Devotional ────────────────────────────────────────────────────
    SCRIPTURE_REFLECTION = "scripture_reflection"
    TESTIMONY = "testimony"

    # ── Culinary ──────────────────────────────────────────────────────
    RECIPE = "recipe"

    # ── General / contemplative ───────────────────────────────────────
    REFLECTION = "reflection"


class PostFormat(str, Enum):
    """Distribution format for a post."""

    IMAGE = "image"
    TEXT = "text"
    CAROUSEL = "carousel"
    VIDEO = "video"


class TechPost(KanyozaModel):
    """
    A generated post — either a caption-format post (Facebook, Twitter,
    Instagram, WhatsApp) or a titled post (LinkedIn, blog).

    Populated exactly once from a single model response.
    All downstream services (rendering, publishing, analytics) consume
    this model.

    V2.3 — `title` is now optional. A caption-format post has title=""
    and the caption is the entire content. A titled post has both. The
    platform contract in prompt_builder.py decides which shape to
    produce. `is_caption_format` distinguishes them at read time.
    """

    # Core content — populated from model structured JSON
    title: str = Field(
        default="",
        max_length=300,
        description="Post headline. Optional for caption-format posts (Facebook, Twitter/X, Instagram, WhatsApp). Required by convention for LinkedIn and blog.",
    )
    caption: str = Field(..., min_length=1, description="Main body / caption text")
    option_a: str = Field(default="", description="Comparison option A (debate posts)")
    option_b: str = Field(default="", description="Comparison option B (debate posts)")
    winner: str = Field(default="", description="Which option wins and why")
    points_a: List[str] = Field(default_factory=list, description="Arguments for option A")
    points_b: List[str] = Field(default_factory=list, description="Arguments for option B")
    hashtags: List[str] = Field(
        default_factory=list,
        description=(
            "Hashtags without #. NOT populated from the model in V2.3+; the "
            "publishing layer (CaptionBuilder._get_hashtags) appends brand "
            "hashtags regardless of this field. Kept on the model for "
            "programmatic callers and migrations."
        ),
    )

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
    def is_caption_format(self) -> bool:
        """True when this post has no title — a bare caption.

        CaptionBuilder uses this to skip the 🔥-title template wrapper.
        Facebook, Twitter/X, Instagram, and WhatsApp posts are
        caption-format. LinkedIn and blog posts are not.
        """
        return not str(self.title or "").strip()

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

        1. Legacy free-form — keys: title (optional in V2.3), caption,
           option_a, option_b, winner, points_a, points_b, hashtags
           (ignored by publishing layer).

        2. Balanced structured-comparison — keys: title (optional in
           V2.3), left_title, left_content, right_title, right_content,
           tradeoff, best_for, verdict, recommendation_title,
           recommendation, hashtags (ignored).

        Field-name drift: `title` and `caption` accept a documented alias
        set. When an alias supplies the value, a WARN is logged.

        V2.3 — `title` is no longer required. A payload with only
        `caption` produces a caption-format post (title=""). This is the
        correct shape for Facebook, Twitter/X, Instagram, and WhatsApp
        content. LinkedIn and blog payloads will still include a title
        because their platform contract asks for one.

        Raises:
            ValueError: when the caption is missing (both canonical and
                alias keys absent or empty). The caller (content engine)
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

            # V2.3 — title optional. Empty string is valid.
            title = _first_alias(data, _TITLE_ALIASES, field_label="title")
            caption = _first_alias(data, _CAPTION_ALIASES, field_label="caption")
            if not caption:
                caption = " ".join(
                    p for p in (left_content, right_content, tradeoff, verdict) if p
                )

            if not caption:
                raise ValueError(
                    "Structured payload missing a usable 'caption' "
                    f"(aliases tried: {list(_CAPTION_ALIASES)}); keys={sorted(data.keys())}"
                )

            return cls(
                title=title or "",
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
        # V2.3 — title optional; caption required.
        title = _first_alias(data, _TITLE_ALIASES, field_label="title")
        caption = _first_alias(data, _CAPTION_ALIASES, field_label="caption")

        if not caption:
            raise ValueError(
                "Structured payload missing a usable 'caption' "
                f"(aliases tried: {list(_CAPTION_ALIASES)}); keys={sorted(data.keys())}"
            )

        return cls(
            title=title or "",
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
