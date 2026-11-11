"""
Card Renderer v18.0 — Enterprise Multi-Tenant Brand-Aware Card Engine

Responsibilities
-----------------
This module is intentionally an ORCHESTRATOR.

It delegates:
    • Brand identity       → BrandStyleResolver
    • Layout selection     → content-driven selector (v18: semantic analysis)
    • Layout rendering     → dedicated layout strategies
    • Typography           → font/Cairo utilities
    • Caching              → shared render_cache
    • External fallback    → HCTI
    • Observability        → structured logging + metrics

Rendering pipeline
------------------
    Input
      ↓
    Normalize Post
      ↓
    Normalize Brand
      ↓
    Resolve BrandStyle
      ↓
    Select CONTENT-driven Layout (v18: semantic analysis)
      ↓
    Build deterministic cache key
      ↓
    Cache HIT ───────────────→ return PNG
      ↓ MISS
    Preload Fonts
      ↓
    Pillow Layout Renderer (2x supersampling)
      ↓ success
    Validate PNG
      ↓
    Cache + return
      │
      └── failure
            ↓
        HCTI fallback
            ↓
        Validate PNG
            ↓
        Cache + return
            │
            └── failure → None

v18.0 UPGRADES:
  - Semantic layout selection (what IS this content about?)
  - 2x supersampling for crisp social media images
  - Cairo enabled by default with Pillow fallback
  - Gradient backgrounds and soft shadows
  - Anti-aliased text rendering
  - Layout selection no longer hijacked by option_a/option_b
  - Comparison only when content is ACTUALLY comparing things

Supported layouts
-----------------
    comparison
    editorial
    quote
    list
    grid
    hero
    pipeline
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .brand_style import BrandStyleResolver, BrandStyle
from .layout_comparison import ComparisonLayout
from .layout_editorial import EditorialLayout
from .layout_quote import QuoteLayout
from .layout_list import ListLayout
from .layout_grid import GridLayout
from .layout_hero import HeroLayout
from .layout_pipeline import PipelineLayout
from .cairo_text import is_cairo_available
from .render_utils import SCALE, CARD_W, CARD_H, load_font

from services.rendering.fonts import font_face_css
from services.rendering.layout import (
    FONT_SIZE,
    palette_for,
    hex_to_rgba,
)
from services.rendering.typography import (
    parse_winner,
    fit_bullets_dynamic,
    truncate_chars,
    pick_layout_kind,
)
from services.rendering.icon_library import get_icon_svg


logger = logging.getLogger("kanyoza.services.card_renderer")


# ═════════════════════════════════════════════════════════════════════
# VERSION / CONSTANTS
# ═════════════════════════════════════════════════════════════════════

RENDERER_VERSION = "18.0"

# v18 — supersampling for crisp social media images
SUPERSAMPLE_SCALE = 2
RENDER_W = CARD_W * SUPERSAMPLE_SCALE
RENDER_H = CARD_H * SUPERSAMPLE_SCALE

TEMPLATES_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "templates"
    / "cards"
)

DEFAULT_BRAND_NAME = "Kanyoza Systems"

MAX_TITLE_LENGTH = 90
MAX_TOPIC_LENGTH = 300
MAX_CACHE_PAYLOAD_SIZE = 200_000

HCTI_ENDPOINT = "https://hcti.io/v1/image"
HCTI_TIMEOUT = 30
HCTI_DOWNLOAD_TIMEOUT = 20

WINNER_LABEL_BY_LAYOUT = {
    "vs": "WINNER",
    "before_after": "BOTTOM LINE",
    "grid": "RECOMMENDATION",
    "list": "PRO TIP",
    "pipeline": "KEY INSIGHT",
}

GRID_LABELS = [
    "APPROACH", "TRADE-OFF", "BEST FOR", "VERDICT",
]

PIPELINE_LABELS = [
    "STEP 1", "STEP 2", "STEP 3", "STEP 4",
]


# ═════════════════════════════════════════════════════════════════════
# v18 — SEMANTIC CONTENT SIGNALS
# ═════════════════════════════════════════════════════════════════════

# Comparison language — only use comparison layout if content
# is ACTUALLY comparing things, not just has two fields.
_COMPARISON_SIGNALS = re.compile(
    r"\b("
    r"vs\.?|versus|compared?\s+(to|with)|"
    r"difference\s+between|which\s+is\s+better|"
    r"pros?\s*(and|&)\s*cons?|"
    r"head\s*to\s*head|side\s*by\s*side|"
    r"trade.?off|battle|showdown|"
    r"choose\s+between|pick\s+(one|the\s+best)"
    r")\b",
    re.IGNORECASE,
)

# Tutorial/guide signals
_TUTORIAL_SIGNALS = re.compile(
    r"\b("
    r"how\s+to|learn|tutorial|guide|step\s+by\s+step|"
    r"beginner|introduction\s+to|getting\s+started|"
    r"basics\s+of|master|understand|explained|"
    r"walkthrough|deep\s+dive|primer|handbook|"
    r"fundamentals|essential\s+guide"
    r")\b",
    re.IGNORECASE,
)

# List/tips signals
_LIST_SIGNALS = re.compile(
    r"\b("
    r"^\d+\.|\b(top|best)\s+\d+|"
    r"tips|tricks|checklist|cheat\s*sheet|"
    r"things\s+(you|to)\s+(know|do|avoid|remember)|"
    r"quick\s+(tips|wins|guide)|"
    r"essentials|must.?know|pro\s+tips"
    r")\b",
    re.IGNORECASE,
)

# Announcement/hero signals
_HERO_SIGNALS = re.compile(
    r"\b("
    r"announcing|introducing|new\s+(release|launch|feature)|"
    r"we.?(re|are)\s+(excited|proud|thrilled|launching)|"
    r"now\s+available|coming\s+soon|"
    r"big\s+news|major\s+(update|announcement)"
    r")\b",
    re.IGNORECASE,
)

# Pipeline/process signals
_PIPELINE_SIGNALS = re.compile(
    r"\b("
    r"step\s*\d+|phase\s*\d+|stage\s*\d+|"
    r"first|second|third|then|next|finally|"
    r"pipeline|flow|process|workflow|lifecycle|"
    r"how\s+(it|things|this)\s+works|"
    r"behind\s+the\s+scenes"
    r")\b",
    re.IGNORECASE,
)

_QUOTE_MARKERS = (
    '"', "'", "\u201c", "\u201d", "\u2018", "\u2019",
)


# ═════════════════════════════════════════════════════════════════════
# FONT PATHS
# ═════════════════════════════════════════════════════════════════════

_FONT_BOLD_PATHS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
)

_FONT_REGULAR_PATHS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
)


def _find_font_path(
    candidates: Tuple[str, ...]
) -> str:
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0]


FONT_BOLD_PATH = _find_font_path(
    _FONT_BOLD_PATHS
)
FONT_REGULAR_PATH = _find_font_path(
    _FONT_REGULAR_PATHS
)


# ═════════════════════════════════════════════════════════════════════
# UTILITY FUNCTIONS
# ═════════════════════════════════════════════════════════════════════

def _safe_string(
    value: Any, default: str = ""
) -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip()
    try:
        return str(value).strip()
    except Exception:
        return default


def _safe_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return list(value)
    if isinstance(value, str):
        value = value.strip()
        return [value] if value else []
    return [value]


def _safe_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {
            str(k): _json_safe(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if hasattr(value, "value"):
        try:
            return _json_safe(value.value)
        except Exception:
            pass
    return str(value)


# ═════════════════════════════════════════════════════════════════════
# v18 — GRADIENT & SHADOW HELPERS
# ═════════════════════════════════════════════════════════════════════

def _draw_gradient_background(
    draw: ImageDraw.ImageDraw,
    img: Image.Image,
    top_color: str,
    bottom_color: str,
) -> None:
    """Draw a subtle vertical gradient background."""
    try:
        top = _hex_to_rgb(top_color)
        bottom = _hex_to_rgb(bottom_color)

        for y in range(img.height):
            ratio = y / img.height
            r = int(top[0] + (bottom[0] - top[0]) * ratio)
            g = int(top[1] + (bottom[1] - top[1]) * ratio)
            b = int(top[2] + (bottom[2] - top[2]) * ratio)
            draw.line(
                [(0, y), (img.width, y)],
                fill=(r, g, b),
            )
    except Exception:
        pass


def _hex_to_rgb(hex_color: str) -> tuple:
    """Convert hex color to RGB tuple."""
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 3:
        hex_color = "".join(
            c * 2 for c in hex_color
        )
    return tuple(
        int(hex_color[i : i + 2], 16)
        for i in (0, 2, 4)
    )


def _add_soft_shadow(
    img: Image.Image,
    offset: int = 4,
    blur_radius: int = 8,
    opacity: int = 40,
) -> Image.Image:
    """Add a soft drop shadow to the card."""
    try:
        shadow = Image.new(
            "RGBA",
            (
                img.width + blur_radius * 2,
                img.height + blur_radius * 2,
            ),
            (0, 0, 0, 0),
        )

        shadow_draw = ImageDraw.Draw(shadow)
        shadow_draw.rounded_rectangle(
            [
                blur_radius + offset,
                blur_radius + offset,
                img.width + blur_radius - offset,
                img.height + blur_radius - offset,
            ],
            radius=20,
            fill=(0, 0, 0, opacity),
        )

        shadow = shadow.filter(
            ImageFilter.GaussianBlur(blur_radius)
        )

        result = Image.new(
            "RGBA",
            (
                img.width + blur_radius * 2,
                img.height + blur_radius * 2,
            ),
            (0, 0, 0, 0),
        )
        result.paste(shadow, (0, 0), shadow)
        result.paste(
            img.convert("RGBA"),
            (blur_radius, blur_radius),
            img.convert("RGBA"),
        )

        return result.convert("RGB")
    except Exception:
        return img


# ═════════════════════════════════════════════════════════════════════
# CARD RENDERER v18.0
# ═════════════════════════════════════════════════════════════════════

class CardRenderer:
    """Enterprise multi-tenant card renderer.

    v18.0: Semantic layout selection, 2x supersampling,
    gradient backgrounds, soft shadows, Cairo preferred.
    """

    def __init__(self) -> None:
        self._brand_resolver = BrandStyleResolver()

        self._layouts = {
            "comparison": ComparisonLayout(),
            "editorial": EditorialLayout(),
            "quote": QuoteLayout(),
            "list": ListLayout(),
            "grid": GridLayout(),
            "hero": HeroLayout(),
            "pipeline": PipelineLayout(),
        }

        self._hcti_user = os.environ.get(
            "HCTI_USER_ID", ""
        ).strip()
        self._hcti_key = os.environ.get(
            "HCTI_API_KEY", ""
        ).strip()

        # v18 — prefer Cairo for high-quality text
        try:
            self._cairo_enabled = bool(
                is_cairo_available()
            )
        except Exception:
            self._cairo_enabled = False

        if self._cairo_enabled:
            logger.info(
                "Cairo rendering enabled — high-quality text"
            )
        else:
            logger.info(
                "Cairo not available — using Pillow text"
            )

        self._jinja = None
        self._cache = None
        self._cache_initialized = False

        logger.info(
            "CardRenderer v%s initialized | layouts=%s | "
            "cairo=%s | supersample=%dx | hcti=%s",
            RENDERER_VERSION,
            ",".join(self._layouts.keys()),
            "enabled" if self._cairo_enabled else "disabled",
            SUPERSAMPLE_SCALE,
            "configured" if self._hcti_configured else "disabled",
        )

    @property
    def _hcti_configured(self) -> bool:
        return bool(self._hcti_user and self._hcti_key)

    # ═══════════════════════════════════════════════════════════════
    # PUBLIC RENDER API
    # ═══════════════════════════════════════════════════════════════

    def render(
        self,
        post_dict: Any = None,
        brand_profile: Any = None,
        *,
        brand: Any = None,
    ) -> Optional[bytes]:
        """Render a post into high-quality PNG bytes."""
        started = time.monotonic()

        try:
            post = self._normalize_post(post_dict)
            if not post:
                logger.error(
                    "render() received empty post data"
                )
                return None

            if brand is not None:
                brand_profile = brand

            normalized_brand = (
                self._normalize_brand_profile(
                    brand_profile
                )
            )
            brand_style = self._brand_resolver.resolve(
                normalized_brand
            )

            layout_name = self._select_layout(
                post, brand_style
            )

            cache_key = self._cache_key(
                post, brand_style, layout_name
            )
            cached = self._cache_get(cache_key)

            if cached is not None:
                logger.debug(
                    "Render cache HIT | key=%s | layout=%s",
                    cache_key[:24],
                    layout_name,
                )
                return cached

            # v18 — render at 2x for supersampling
            img = Image.new(
                "RGB",
                (RENDER_W, RENDER_H),
                brand_style.bg,
            )

            # v18 — gradient background
            draw = ImageDraw.Draw(img)
            if (
                brand_style.bg
                and brand_style.col_bg
                and brand_style.bg
                != brand_style.col_bg
            ):
                _draw_gradient_background(
                    draw,
                    img,
                    brand_style.bg,
                    brand_style.col_bg,
                )

            fonts = self._preload_fonts(brand_style)

            png = self._render_with_layout(
                post,
                brand_style,
                fonts,
                draw,
                img,
                layout_name,
            )

            if self._validate_png(png):
                # v18 — downscale to target size for crisp output
                final = self._downscale_png(png)

                # v18 — add soft shadow
                final = _add_soft_shadow(final)

                final_bytes = self._png_to_bytes(final)
                self._cache_set(cache_key, final_bytes)

                elapsed_ms = (
                    time.monotonic() - started
                ) * 1000

                logger.info(
                    "Card rendered | brand='%s' | layout=%s | "
                    "size=%d bytes | %.1fms",
                    brand_style.brand_name,
                    layout_name,
                    len(final_bytes),
                    elapsed_ms,
                )

                return final_bytes

            raise RuntimeError(
                f"Layout '{layout_name}' produced invalid PNG"
            )

        except Exception as exc:
            elapsed_ms = (
                time.monotonic() - started
            ) * 1000
            logger.error(
                "Primary card rendering failed | %.1fms | %s",
                elapsed_ms,
                exc,
                exc_info=True,
            )

        # HCTI fallback
        try:
            post = self._normalize_post(post_dict)
            if brand is not None:
                brand_profile = brand
            normalized_brand = (
                self._normalize_brand_profile(
                    brand_profile
                )
            )
            brand_style = self._brand_resolver.resolve(
                normalized_brand
            )
            png = self._fallback_hcti(
                post, brand_style
            )

            if self._validate_png(png):
                layout_name = self._select_layout(
                    post, brand_style
                )
                cache_key = self._cache_key(
                    post, brand_style, layout_name
                )
                self._cache_set(cache_key, png)
                logger.info(
                    "HCTI fallback succeeded | brand='%s' | "
                    "bytes=%d",
                    brand_style.brand_name,
                    len(png),
                )
                return png

        except Exception as exc:
            logger.error(
                "HCTI fallback pipeline failed: %s",
                exc,
                exc_info=True,
            )

        logger.error(
            "CARD_RENDER_FAILED | all rendering strategies exhausted"
        )
        return None

    # ═══════════════════════════════════════════════════════════════
    # v18 — SUPERSAMPLING HELPERS
    # ═══════════════════════════════════════════════════════════════

    def _downscale_png(
        self, png: bytes
    ) -> Image.Image:
        """Downscale 2x rendered image to target size."""
        try:
            img = Image.open(io.BytesIO(png))
            img = img.resize(
                (CARD_W, CARD_H),
                Image.LANCZOS,
            )
            return img
        except Exception:
            return Image.open(io.BytesIO(png))

    def _png_to_bytes(
        self, img: Image.Image
    ) -> bytes:
        """Convert PIL Image to PNG bytes."""
        buf = io.BytesIO()
        img.save(
            buf,
            format="PNG",
            optimize=True,
            quality=95,
        )
        return buf.getvalue()

    # ═══════════════════════════════════════════════════════════════
    # LEGACY API
    # ═══════════════════════════════════════════════════════════════

    def render_legacy(
        self,
        title: str = "",
        subtitle: str = "",
        template: str = "default",
        **kwargs: Any,
    ) -> Optional[bytes]:
        post_dict = {
            "title": title,
            "caption": subtitle,
            "template": template,
            **kwargs,
        }
        brand_profile = {
            "name": (
                kwargs.get("brand_name")
                or DEFAULT_BRAND_NAME
            ),
            "colors": (
                kwargs.get("brand_colors")
                or kwargs.get("colors")
                or {}
            ),
            "metadata": (
                kwargs.get("brand_metadata")
                or kwargs.get("metadata")
                or {}
            ),
            "hashtags": (
                kwargs.get("brand_hashtags")
                or kwargs.get("hashtags")
                or ""
            ),
            "logo_url": kwargs.get("logo_url")
            or kwargs.get("brand_logo"),
            "archetype": kwargs.get("archetype"),
            "font_family": kwargs.get("font_family"),
            "font_scale": kwargs.get("font_scale"),
            "visual_density": kwargs.get(
                "visual_density"
            ),
            "max_hashtags": kwargs.get(
                "max_hashtags"
            ),
            "footer_text": kwargs.get("footer_text"),
            "winner_label": kwargs.get("winner_label"),
        }
        return self.render(post_dict, brand_profile)

    def render_from_post_dict(
        self, post_dict: dict
    ) -> Optional[bytes]:
        if not isinstance(post_dict, dict):
            logger.error(
                "render_from_post_dict expected dict, got %s",
                type(post_dict).__name__,
            )
            return None
        brand_profile = {
            "name": (
                post_dict.get("brand_name")
                or post_dict.get("name")
                or DEFAULT_BRAND_NAME
            ),
            "colors": (
                post_dict.get("brand_colors")
                or post_dict.get("colors")
                or {}
            ),
            "metadata": (
                post_dict.get("brand_metadata")
                or post_dict.get("metadata")
                or {}
            ),
            "hashtags": (
                post_dict.get("brand_hashtags")
                or post_dict.get("hashtags")
                or ""
            ),
            "logo_url": post_dict.get("logo_url")
            or post_dict.get("brand_logo"),
            "archetype": post_dict.get("archetype"),
            "font_family": post_dict.get("font_family"),
            "font_scale": post_dict.get("font_scale"),
            "visual_density": post_dict.get(
                "visual_density"
            ),
            "max_hashtags": post_dict.get(
                "max_hashtags"
            ),
        }
        return self.render(post_dict, brand_profile)

    def render_html_only(
        self,
        post_dict: Any = None,
        brand_profile: Any = None,
        *,
        brand: Any = None,
    ) -> Optional[str]:
        try:
            if brand is not None:
                brand_profile = brand
            post = self._normalize_post(post_dict)
            if not post:
                return None
            normalized_brand = (
                self._normalize_brand_profile(
                    brand_profile
                )
            )
            brand_style = self._brand_resolver.resolve(
                normalized_brand
            )
            return self._build_html(
                post, brand_style
            )
        except Exception as exc:
            logger.error(
                "render_html_only failed: %s",
                exc,
                exc_info=True,
            )
            return None

    # ═══════════════════════════════════════════════════════════════
    # INPUT NORMALIZATION (unchanged)
    # ═══════════════════════════════════════════════════════════════

    def _normalize_post(self, post: Any) -> dict:
        if post is None:
            return {}
        if isinstance(post, dict):
            return dict(post)
        if isinstance(post, Mapping):
            return dict(post)
        if hasattr(post, "model_dump"):
            try:
                return post.model_dump(mode="json")
            except Exception:
                pass
        if hasattr(post, "dict"):
            try:
                return post.dict()
            except Exception:
                pass
        result = {}
        for key in (
            "title", "caption", "body", "content",
            "option_a", "option_b", "points_a",
            "points_b", "items", "cells", "steps",
            "winner", "verdict", "callout", "cta",
            "recommendation", "recommendation_title",
            "quote", "attribution", "context",
            "topic", "theme", "hashtags",
            "brand_hashtags", "content_type", "template",
        ):
            if hasattr(post, key):
                result[key] = getattr(post, key)
        return result

    def _normalize_brand_profile(
        self, brand: Any
    ) -> dict:
        if brand is None:
            return {"name": DEFAULT_BRAND_NAME}
        if isinstance(brand, Mapping):
            data = dict(brand)
            return {
                **data,
                "name": (
                    data.get("name")
                    or data.get("brand_name")
                    or DEFAULT_BRAND_NAME
                ),
                "colors": (
                    data.get("colors")
                    or data.get("brand_colors")
                    or {}
                ),
                "metadata": (
                    data.get("metadata")
                    or data.get("brand_metadata")
                    or {}
                ),
                "hashtags": (
                    data.get("hashtags")
                    or data.get("formatted_hashtags")
                    or ""
                ),
            }
        if hasattr(brand, "model_dump"):
            try:
                data = brand.model_dump(mode="json")
                return {
                    **data,
                    "name": (
                        data.get("name")
                        or DEFAULT_BRAND_NAME
                    ),
                    "archetype": data.get("archetype"),
                    "colors": (
                        data.get("colors")
                        or data.get("brand_colors")
                        or {}
                    ),
                    "metadata": (
                        data.get("metadata")
                        or data.get("brand_metadata")
                        or {}
                    ),
                    "logo_url": data.get("logo_url")
                    or data.get("logo_path"),
                    "hashtags": (
                        data.get("hashtags")
                        or data.get("formatted_hashtags")
                        or ""
                    ),
                    "font_family": data.get(
                        "font_family"
                    ),
                    "font_scale": data.get(
                        "font_scale"
                    ),
                    "visual_density": data.get(
                        "visual_density"
                    ),
                    "max_hashtags": data.get(
                        "max_hashtags"
                    ),
                    "footer_text": data.get(
                        "footer_text"
                    ),
                    "winner_label": data.get(
                        "winner_label"
                    ),
                }
            except Exception as exc:
                logger.debug(
                    "Brand model_dump failed: %s", exc
                )
        return {
            "name": (
                getattr(brand, "name", None)
                or DEFAULT_BRAND_NAME
            ),
            "archetype": getattr(
                brand, "archetype", None
            ),
            "colors": (
                getattr(brand, "colors", None)
                or getattr(
                    brand, "brand_colors", {}
                )
            ),
            "metadata": (
                getattr(brand, "metadata", None)
                or getattr(
                    brand, "brand_metadata", {}
                )
            ),
            "logo_url": getattr(brand, "logo_url", None)
            or getattr(brand, "logo_path", None),
            "hashtags": (
                getattr(
                    brand, "formatted_hashtags", None
                )
                or getattr(brand, "hashtags", "")
            ),
            "font_family": getattr(
                brand, "font_family", None
            ),
            "font_scale": getattr(
                brand, "font_scale", None
            ),
            "visual_density": getattr(
                brand, "visual_density", None
            ),
            "max_hashtags": getattr(
                brand, "max_hashtags", None
            ),
            "footer_text": getattr(
                brand, "footer_text", None
            ),
            "winner_label": getattr(
                brand, "winner_label", None
            ),
        }

    # ═══════════════════════════════════════════════════════════════
    # v18 — SEMANTIC LAYOUT SELECTION
    # ═══════════════════════════════════════════════════════════════

    def _select_layout(
        self,
        post_dict: dict,
        brand: BrandStyle,
    ) -> str:
        """Select layout based on what the content IS.

        v18: Semantic analysis prevents comparison hijacking.
        Comparison only triggers when content is ACTUALLY
        comparing things, not just has two fields.
        """
        title = _safe_string(post_dict.get("title"))
        caption = _safe_string(
            post_dict.get("caption")
        )
        body = _safe_string(
            post_dict.get("body")
            or post_dict.get("content")
        )
        option_a = _safe_string(
            post_dict.get("option_a")
        )
        option_b = _safe_string(
            post_dict.get("option_b")
        )
        points_a = _safe_list(
            post_dict.get("points_a")
        )
        points_b = _safe_list(
            post_dict.get("points_b")
        )
        items = _safe_list(post_dict.get("items"))
        cells = _safe_list(post_dict.get("cells"))
        steps = _safe_list(post_dict.get("steps"))
        content_type = _safe_string(
            post_dict.get("content_type")
        ).lower()
        quote_text = _safe_string(
            post_dict.get("quote")
        )
        theme = _safe_string(
            post_dict.get("theme")
        ).lower()

        # ── 1. Explicit content type always wins ─────────────────
        type_to_layout = {
            "comparison": "comparison",
            "vs": "comparison",
            "before_after": "comparison",
            "educational": "editorial",
            "teaching": "editorial",
            "tip": "editorial",
            "guide": "editorial",
            "story": "editorial",
            "quote": "quote",
            "scripture": "quote",
            "testimony": "quote",
            "list": "list",
            "tips": "list",
            "statistics": "grid",
            "matrix": "grid",
            "announcement": "hero",
            "promotional": "hero",
            "pipeline": "pipeline",
            "steps": "pipeline",
            "workflow": "pipeline",
            "process": "pipeline",
        }
        if (
            content_type
            and content_type in type_to_layout
        ):
            return type_to_layout[content_type]

        # ── 2. Dedicated quote field ─────────────────────────────
        if quote_text:
            return "quote"

        # ── 3. Semantic analysis ─────────────────────────────────
        combined = (
            f"{title} {caption} {body}".lower()
        )

        # v18 — Tutorial/guide → editorial
        if _TUTORIAL_SIGNALS.search(combined):
            return "editorial"

        # v18 — List/tips → list
        if _LIST_SIGNALS.search(combined):
            return "list"

        # v18 — Announcement → hero
        if _HERO_SIGNALS.search(combined):
            return "hero"

        # v18 — Pipeline/process
        if _PIPELINE_SIGNALS.search(combined):
            return "pipeline"

        # v18 — Comparison ONLY if language signals it
        if option_a and option_b:
            if _COMPARISON_SIGNALS.search(combined):
                return "comparison"
            # Has two options but no comparison language
            # → treat as list or editorial
            if points_a and points_b:
                return "list"
            return "editorial"

        # ── 4. Quote markers ─────────────────────────────────────
        if len(combined) <= 400 and any(
            marker in combined
            for marker in _QUOTE_MARKERS
        ):
            return "quote"

        # ── 5. Explicit steps → pipeline ─────────────────────────
        if steps:
            return "pipeline"

        # ── 6. Grid cells ────────────────────────────────────────
        if cells:
            return "grid"

        # ── 7. Multiple items → grid ─────────────────────────────
        total_points = (
            len(points_a) + len(points_b) + len(items)
        )
        if total_points >= 4:
            return "grid"

        # ── 8. Items → list ──────────────────────────────────────
        if items:
            return "list"

        # ── 9. Short content → hero ──────────────────────────────
        if (
            len(combined) < 200
            and not points_a
            and not points_b
        ):
            return "hero"

        # ── 10. Editorial default ────────────────────────────────
        return "editorial"

    # ═══════════════════════════════════════════════════════════════
    # LAYOUT DISPATCH
    # ═══════════════════════════════════════════════════════════════

    def _render_with_layout(
        self,
        post_dict: dict,
        brand: BrandStyle,
        fonts: dict,
        draw: ImageDraw.ImageDraw,
        img: Image.Image,
        layout_name: str,
    ) -> Optional[bytes]:
        strategy = self._layouts.get(layout_name)
        if strategy is None:
            logger.warning(
                "Unknown layout '%s'; using editorial",
                layout_name,
            )
            strategy = self._layouts["editorial"]

        result = strategy.render(
            post_dict, brand, fonts, draw, img
        )
        if result is None:
            return None

        # v18 — convert to bytes for validation
        if isinstance(result, Image.Image):
            buf = io.BytesIO()
            result.save(buf, format="PNG")
            return buf.getvalue()

        return result

    # ═══════════════════════════════════════════════════════════════
    # PNG VALIDATION
    # ═══════════════════════════════════════════════════════════════

    def _validate_png(
        self, png: Optional[bytes]
    ) -> bool:
        if not png:
            return False
        if not isinstance(png, (bytes, bytearray)):
            return False
        if len(png) < 100:
            return False
        try:
            with Image.open(
                io.BytesIO(png)
            ) as image:
                image.verify()
            return True
        except Exception as exc:
            logger.warning(
                "PNG validation failed: %s", exc
            )
            return False

    # ═══════════════════════════════════════════════════════════════
    # CACHE (unchanged)
    # ═══════════════════════════════════════════════════════════════

    def _cache_key(
        self,
        post: dict,
        brand: BrandStyle,
        layout_name: str,
    ) -> str:
        relevant_fields = (
            "title", "caption", "body", "content",
            "option_a", "option_b", "points_a",
            "points_b", "items", "cells", "steps",
            "winner", "verdict", "callout", "cta",
            "recommendation", "recommendation_title",
            "quote", "attribution", "context",
            "topic", "theme", "hashtags",
            "brand_hashtags", "content_type", "template",
        )
        stable_post = {}
        for key in relevant_fields:
            if key in post:
                stable_post[key] = _json_safe(
                    post[key]
                )
        brand_payload = {
            "name": brand.brand_name,
            "archetype": _json_safe(
                getattr(
                    brand.archetype,
                    "value",
                    brand.archetype,
                )
            ),
            "bg": brand.bg,
            "col_bg": brand.col_bg,
            "primary": brand.primary,
            "secondary": brand.secondary,
            "accent": brand.accent,
            "txt_main": brand.txt_main,
            "txt_muted": brand.txt_muted,
            "txt_dim": brand.txt_dim,
            "rule_clr": brand.rule_clr,
            "dark_mode": brand.dark_mode,
            "corner_radius": brand.corner_radius,
            "density_padding": brand.density_padding,
            "density_gap": brand.density_gap,
            "font_scale": brand.font_scale,
            "max_hashtags": brand.max_hashtags,
            "show_date": brand.show_date,
            "footer_label": brand.footer_label,
            "winner_label": brand.winner_label,
            "vs_label": brand.vs_label,
            "logo_path": getattr(
                brand, "logo_path", None
            ),
        }
        payload = {
            "renderer": RENDERER_VERSION,
            "post": stable_post,
            "brand": _json_safe(brand_payload),
            "layout": layout_name,
        }
        serialized = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            default=str,
        )
        digest = hashlib.sha256(
            serialized.encode("utf-8")
        ).hexdigest()
        return (
            f"card:v{RENDERER_VERSION}:"
            f"{digest[:24]}"
        )

    def _cache_get(
        self, key: str
    ) -> Optional[bytes]:
        self._ensure_cache()
        if self._cache is None:
            return None
        try:
            value = self._cache.get(key)
            if self._validate_png(value):
                return value
        except Exception as exc:
            logger.debug(
                "Cache GET failed | key=%s | %s",
                key[:24],
                exc,
            )
        return None

    def _cache_set(
        self, key: str, png: Optional[bytes]
    ) -> None:
        if not self._validate_png(png):
            return
        self._ensure_cache()
        if self._cache is None:
            return
        try:
            self._cache.set(key, png)
        except Exception as exc:
            logger.debug(
                "Cache SET failed | key=%s | %s",
                key[:24],
                exc,
            )

    def _ensure_cache(self) -> None:
        if self._cache_initialized:
            return
        self._cache_initialized = True
        try:
            from services.render_cache import (
                render_cache,
            )
            self._cache = render_cache
        except ImportError:
            pass
        except Exception as exc:
            logger.warning(
                "Render cache init failed: %s", exc
            )

    # ═══════════════════════════════════════════════════════════════
    # FONT SYSTEM (v18 — supersampled sizes)
    # ═══════════════════════════════════════════════════════════════

    def _preload_fonts(
        self, brand: Optional[BrandStyle] = None
    ) -> dict:
        scale = 1.0
        if brand is not None:
            try:
                scale = float(
                    getattr(
                        brand, "font_scale", 1.0
                    )
                    or 1.0
                )
            except (TypeError, ValueError):
                scale = 1.0
        scale = max(0.75, min(scale, 1.50))

        sizes_css = (
            10, 11, 12, 13, 14, 15, 16, 17, 18,
            19, 20, 22, 24, 26, 28, 30, 32, 35,
            36, 40, 42, 47, 50, 54, 58, 64, 72,
            80,
        )

        fonts = {"bold": {}, "regular": {}}

        for size_css in sizes_css:
            # v18 — double the base scale for supersampling
            scaled_size = max(
                1,
                int(
                    size_css
                    * scale
                    * SCALE
                    * SUPERSAMPLE_SCALE
                ),
            )
            key = size_css * SCALE
            fonts["bold"][key] = load_font(
                FONT_BOLD_PATH, scaled_size
            )
            fonts["regular"][key] = load_font(
                FONT_REGULAR_PATH, scaled_size
            )

        return fonts

    # ═══════════════════════════════════════════════════════════════
    # HCTI FALLBACK (unchanged)
    # ═══════════════════════════════════════════════════════════════

    def _fallback_hcti(
        self,
        post_dict: dict,
        brand: BrandStyle,
    ) -> Optional[bytes]:
        if not self._hcti_configured:
            return None
        html = self._build_html(post_dict, brand)
        if not html:
            return None
        try:
            from utils.http_client import HttpClient

            hcti = HttpClient(
                "hcti",
                default_timeout=HCTI_TIMEOUT,
            )
            response = hcti.post(
                HCTI_ENDPOINT,
                data={
                    "html": html,
                    "viewport_width": 1080,
                    "viewport_height": 1080,
                    "device_scale": 2,
                },
                auth=(
                    self._hcti_user,
                    self._hcti_key,
                ),
            )
            if response.status_code != 200:
                return None
            data = response.json()
            image_url = _safe_string(
                data.get("url")
            )
            if not image_url:
                return None
            image_response = hcti.get(
                image_url,
                timeout=HCTI_DOWNLOAD_TIMEOUT,
            )
            if image_response.status_code != 200:
                return None
            png = image_response.content
            if not self._validate_png(png):
                return None
            return png
        except Exception as exc:
            logger.error(
                "HCTI fallback exception: %s",
                exc,
                exc_info=True,
            )
            return None

    # ═══════════════════════════════════════════════════════════════
    # JINJA / HTML (unchanged from v17)
    # ═══════════════════════════════════════════════════════════════

    def _init_jinja(self):
        if self._jinja is not None:
            return self._jinja
        if not TEMPLATES_DIR.exists():
            return None
        try:
            self._jinja = Environment(
                loader=FileSystemLoader(
                    str(TEMPLATES_DIR)
                ),
                autoescape=select_autoescape(
                    ["html", "xml"]
                ),
                trim_blocks=True,
                lstrip_blocks=True,
            )
            return self._jinja
        except Exception as exc:
            logger.error(
                "Jinja init failed: %s", exc
            )
            return None

    def _build_html(
        self,
        post_dict: dict,
        brand: BrandStyle,
    ) -> Optional[str]:
        env = self._init_jinja()
        if env is None:
            return None
        try:
            template = env.get_template("card.html")
        except Exception as exc:
            logger.error(
                "card.html load failed: %s", exc
            )
            return None
        context = self._build_context(
            post_dict, brand
        )
        try:
            return template.render(**context)
        except Exception as exc:
            logger.error(
                "Jinja render failed: %s", exc
            )
            return None

    def _build_context(
        self,
        post_dict: dict,
        brand: BrandStyle,
    ) -> dict:
        title = _safe_string(
            post_dict.get("title")
        )
        theme_raw = _safe_string(
            post_dict.get("theme"), "ai"
        )
        theme_val = theme_raw.split(".")[-1].lower()
        points_a = [
            _safe_string(item)
            for item in _safe_list(
                post_dict.get("points_a")
            )
            if _safe_string(item)
        ]
        points_b = [
            _safe_string(item)
            for item in _safe_list(
                post_dict.get("points_b")
            )
            if _safe_string(item)
        ]
        items_raw = [
            _safe_string(item)
            for item in _safe_list(
                post_dict.get("items")
            )
            if _safe_string(item)
        ]
        cells_raw = _safe_list(
            post_dict.get("cells")
        )
        steps_raw = _safe_list(
            post_dict.get("steps")
        )
        winner_raw = _safe_string(
            post_dict.get("winner")
            or post_dict.get("verdict")
        )
        option_a = _safe_string(
            post_dict.get("option_a")
        )
        option_b = _safe_string(
            post_dict.get("option_b")
        )
        is_debate = bool(option_a and option_b)
        layout_kind = pick_layout_kind(
            theme_val,
            is_debate=is_debate,
            points_a=points_a,
            points_b=points_b,
            winner=winner_raw,
        )
        palette = dict(palette_for(theme_val))
        colors = (
            post_dict.get("brand_colors")
            or post_dict.get("colors")
            or {}
        )
        if isinstance(colors, str):
            try:
                colors = json.loads(colors)
            except Exception:
                colors = {}
        colors = _safe_dict(colors)
        if colors.get("primary"):
            palette["a"] = colors["primary"]
        if colors.get("secondary"):
            palette["b"] = colors["secondary"]
        if colors.get("accent"):
            palette["accent"] = colors["accent"]
        palette["glow_rgba"] = hex_to_rgba(
            palette.get("a", "#6366f1"), 0.18
        )
        try:
            icon_svg = get_icon_svg(
                theme_val,
                color=palette.get(
                    "accent", "#818cf8"
                ),
                size=20,
            )
        except Exception:
            icon_svg = ""
        hashtags = (
            post_dict.get("brand_hashtags")
            or post_dict.get("hashtags")
            or ""
        )
        if isinstance(hashtags, str):
            hashtags = [
                tag.strip()
                for tag in hashtags.split()
                if tag.strip()
            ]
        elif isinstance(
            hashtags, (tuple, list)
        ):
            hashtags = [
                _safe_string(tag)
                for tag in hashtags
                if _safe_string(tag)
            ]
        else:
            hashtags = []
        try:
            max_hashtags = int(
                getattr(
                    brand, "max_hashtags", 5
                )
                or 5
            )
        except (TypeError, ValueError):
            max_hashtags = 5
        max_hashtags = max(
            0, min(max_hashtags, 30)
        )
        hashtags = hashtags[:max_hashtags]
        bullets_a = fit_bullets_dynamic(
            points_a,
            max_count=4,
            min_count=2,
            max_words=5,
        )
        bullets_b = fit_bullets_dynamic(
            points_b,
            max_count=4,
            min_count=2,
            max_words=5,
        )
        winner_context = None
        if winner_raw:
            try:
                winner_name, reason = parse_winner(
                    winner_raw,
                    option_a,
                    option_b,
                )
                if winner_name:
                    winner_context = {
                        "label": (
                            getattr(
                                brand,
                                "winner_label",
                                None,
                            )
                            or WINNER_LABEL_BY_LAYOUT.get(
                                layout_kind,
                                "WINNER",
                            )
                        ),
                        "name": truncate_chars(
                            winner_name, 40
                        ),
                        "reason": truncate_chars(
                            reason or "", 120
                        ),
                    }
            except Exception:
                pass
        context = {
            "title": truncate_chars(
                title, MAX_TITLE_LENGTH
            ),
            "theme_label": theme_val.replace(
                "_", " "
            ).upper(),
            "hashtags": hashtags,
            "bg_decoration": "",
            "font_face_css": font_face_css(),
            "font_size": FONT_SIZE,
            "palette": palette,
            "layout_kind": layout_kind,
            "brand_name": brand.brand_name,
            "brand_logo": getattr(
                brand, "logo_path", None
            ),
            "dark_mode": getattr(
                brand, "dark_mode", False
            ),
            "icon_svg": icon_svg,
            "columns": [],
            "cells": [],
            "items": [],
            "winner": winner_context,
            "cm_left": None,
            "cm_right": None,
            "cm_facts": [],
            "cm_recommendation": None,
            "steps": [],
        }
        if layout_kind in ("vs", "before_after"):
            name_a = (
                truncate_chars(option_a, 22)
                or "Option A"
            )
            name_b = (
                truncate_chars(option_b, 22)
                or "Option B"
            )
            context["columns"] = [
                {
                    "name": name_a,
                    "side": "left",
                    "color": palette.get(
                        "a", "#6366f1"
                    ),
                    "bullets": bullets_a,
                    "strength_label": (
                        "STRENGTHS"
                        if layout_kind == "vs"
                        else "BEFORE"
                    ),
                },
                {
                    "name": name_b,
                    "side": "right",
                    "color": palette.get(
                        "b", "#0ea5e9"
                    ),
                    "bullets": bullets_b,
                    "strength_label": (
                        "STRENGTHS"
                        if layout_kind == "vs"
                        else "AFTER"
                    ),
                },
            ]
        elif layout_kind in ("grid", "pipeline"):
            labels = (
                PIPELINE_LABELS
                if layout_kind == "pipeline"
                else GRID_LABELS
            )
            if cells_raw:
                pool = []
                for cell in cells_raw:
                    if isinstance(cell, Mapping):
                        text = (
                            cell.get("text")
                            or cell.get("content")
                            or cell.get("value")
                            or ""
                        )
                    else:
                        text = cell
                    text = _safe_string(text)
                    if text:
                        pool.append(text)
            else:
                pool = fit_bullets_dynamic(
                    points_a + points_b,
                    max_count=4,
                    min_count=3,
                    max_words=8,
                )
            pool = pool[:4]
            while len(pool) < 4:
                pool.append("—")
            context["cells"] = [
                {
                    "label": label,
                    "text": truncate_chars(
                        text, 90
                    ),
                    "accent": index == 0,
                }
                for index, (label, text) in enumerate(
                    zip(labels, pool)
                )
            ]
        if steps_raw:
            normalized_steps = []
            for index, step in enumerate(
                steps_raw[:6], start=1
            ):
                if isinstance(step, Mapping):
                    step_title = (
                        step.get("title")
                        or step.get("name")
                        or f"STEP {index}"
                    )
                    step_text = (
                        step.get("text")
                        or step.get("content")
                        or step.get("description")
                        or ""
                    )
                else:
                    step_title = f"STEP {index}"
                    step_text = step
                normalized_steps.append({
                    "number": index,
                    "title": truncate_chars(
                        _safe_string(step_title),
                        35,
                    ),
                    "text": truncate_chars(
                        _safe_string(step_text),
                        90,
                    ),
                })
            context["steps"] = normalized_steps
        else:
            pool = fit_bullets_dynamic(
                points_a + points_b,
                max_count=6,
                min_count=3,
                max_words=6,
            )
            if items_raw:
                pool = [
                    truncate_chars(item, 70)
                    for item in items_raw[:6]
                ]
            context["items"] = [
                {"num": index + 1, "text": text}
                for index, text in enumerate(pool)
            ]
        return context


# ═════════════════════════════════════════════════════════════════════
# MODULE SINGLETON
# ═════════════════════════════════════════════════════════════════════

card_renderer = CardRenderer()
