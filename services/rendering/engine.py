"""
Card Renderer v17.0 — Enterprise Multi-Tenant Brand-Aware Card Engine

Responsibilities
-----------------
This module is intentionally an ORCHESTRATOR.

It delegates:
    • Brand identity       → BrandStyleResolver
    • Layout selection     → content-driven selector
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
    Select CONTENT-driven Layout
      ↓
    Build deterministic cache key
      ↓
    Cache HIT ───────────────→ return PNG
      ↓ MISS
    Preload Fonts
      ↓
    Pillow Layout Renderer
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

Supported layouts
-----------------
    comparison
    editorial
    quote
    list
    grid
    hero
    pipeline

Supported calling conventions
-----------------------------
    # ContentEngine
    render(post, brand=brand)

    # New API
    render(post_dict, brand_profile)

    # Legacy queue
    render_legacy(title="...", subtitle="...")

    # v15 compatibility
    render_from_post_dict(post_dict)

    # Dashboard preview
    render_html_only(post, brand=brand)

Important contract
------------------
    render() returns:
        bytes | None

    It NEVER returns:
        str
        dict
        MediaArtifact

MediaArtifact conversion belongs to ContentEngine.

Version
-------
    17.0
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

from PIL import Image, ImageDraw
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
from services.rendering.layout import FONT_SIZE, palette_for, hex_to_rgba
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

RENDERER_VERSION = "17.0"

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
    "APPROACH",
    "TRADE-OFF",
    "BEST FOR",
    "VERDICT",
]

PIPELINE_LABELS = [
    "STEP 1",
    "STEP 2",
    "STEP 3",
    "STEP 4",
]


# ═════════════════════════════════════════════════════════════════════
# CONTENT SIGNALS
# ═════════════════════════════════════════════════════════════════════

_SEQUENCE_SIGNALS = re.compile(
    r"\b("
    r"step\s*\d+|"
    r"phase\s*\d+|"
    r"first|"
    r"second|"
    r"third|"
    r"then|"
    r"next|"
    r"finally|"
    r"pipeline|"
    r"flow|"
    r"process|"
    r"workflow"
    r")\b",
    re.IGNORECASE,
)


_QUOTE_MARKERS = (
    '"',
    "'",
    "\u201c",
    "\u201d",
    "\u2018",
    "\u2019",
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


def _find_font_path(candidates: Tuple[str, ...]) -> str:
    """
    Return the first existing font path.

    The first candidate is retained as the fallback path so
    load_font() can still provide its own failure handling.
    """
    for path in candidates:
        if os.path.exists(path):
            return path

    return candidates[0]


FONT_BOLD_PATH = _find_font_path(_FONT_BOLD_PATHS)
FONT_REGULAR_PATH = _find_font_path(_FONT_REGULAR_PATHS)


# ═════════════════════════════════════════════════════════════════════
# UTILITY FUNCTIONS
# ═════════════════════════════════════════════════════════════════════

def _safe_string(value: Any, default: str = "") -> str:
    """
    Convert arbitrary values into safe strings.

    Prevents renderer crashes caused by unexpected None/object values.
    """
    if value is None:
        return default

    if isinstance(value, str):
        return value.strip()

    try:
        return str(value).strip()
    except Exception:
        return default


def _safe_list(value: Any) -> list:
    """
    Normalize a value into a list.

    Strings become one-element lists instead of being iterated
    character-by-character.
    """
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
    """Normalize arbitrary mapping-like values into a dict."""
    if isinstance(value, dict):
        return value

    if isinstance(value, Mapping):
        return dict(value)

    return {}


def _json_safe(value: Any) -> Any:
    """
    Convert arbitrary values into deterministic JSON-compatible data.
    """
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
# CARD RENDERER
# ═════════════════════════════════════════════════════════════════════

class CardRenderer:
    """
    Enterprise multi-tenant card renderer.

    The renderer is intentionally responsible for orchestration only.
    Layout implementations remain isolated in their respective strategy
    classes.
    """

    def __init__(self) -> None:
        # ─────────────────────────────────────────────────────────────
        # Brand
        # ─────────────────────────────────────────────────────────────

        self._brand_resolver = BrandStyleResolver()

        # ─────────────────────────────────────────────────────────────
        # Layout strategies
        # ─────────────────────────────────────────────────────────────

        self._layouts = {
            "comparison": ComparisonLayout(),
            "editorial": EditorialLayout(),
            "quote": QuoteLayout(),
            "list": ListLayout(),
            "grid": GridLayout(),
            "hero": HeroLayout(),
            "pipeline": PipelineLayout(),
        }

        # ─────────────────────────────────────────────────────────────
        # External rendering
        # ─────────────────────────────────────────────────────────────

        self._hcti_user = os.environ.get("HCTI_USER_ID", "").strip()
        self._hcti_key = os.environ.get("HCTI_API_KEY", "").strip()

        # ─────────────────────────────────────────────────────────────
        # Optional Cairo support
        # ─────────────────────────────────────────────────────────────

        try:
            self._cairo_enabled = bool(is_cairo_available())
        except Exception:
            self._cairo_enabled = False

        # ─────────────────────────────────────────────────────────────
        # Lazy services
        # ─────────────────────────────────────────────────────────────

        self._jinja: Optional[Environment] = None
        self._cache = None
        self._cache_initialized = False

        logger.info(
            "CardRenderer v%s initialized | layouts=%s | cairo=%s | hcti=%s",
            RENDERER_VERSION,
            ",".join(self._layouts.keys()),
            "enabled" if self._cairo_enabled else "disabled",
            "configured" if self._hcti_configured else "disabled",
        )

    # ═══════════════════════════════════════════════════════════════
    # PROPERTIES
    # ═══════════════════════════════════════════════════════════════

    @property
    def _hcti_configured(self) -> bool:
        """Whether HCTI credentials are available."""
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
        """
        Render a post into PNG bytes.

        Supported calls:

            renderer.render(post, brand=brand)

            renderer.render(post_dict, brand_profile)

        Returns:
            PNG bytes on success.
            None if all rendering paths fail.
        """
        started = time.monotonic()

        try:
            # ─────────────────────────────────────────────────────
            # 1. Normalize post
            # ─────────────────────────────────────────────────────

            post = self._normalize_post(post_dict)

            if not post:
                logger.error("render() received empty post data")
                return None

            # ─────────────────────────────────────────────────────
            # 2. Normalize brand
            # ─────────────────────────────────────────────────────

            if brand is not None:
                brand_profile = brand

            normalized_brand = self._normalize_brand_profile(
                brand_profile
            )

            # ─────────────────────────────────────────────────────
            # 3. Resolve brand identity
            # ─────────────────────────────────────────────────────

            brand_style = self._brand_resolver.resolve(
                normalized_brand
            )

            # ─────────────────────────────────────────────────────
            # 4. Select layout
            # ─────────────────────────────────────────────────────

            layout_name = self._select_layout(
                post,
                brand_style,
            )

            # ─────────────────────────────────────────────────────
            # 5. Cache lookup
            # ─────────────────────────────────────────────────────

            cache_key = self._cache_key(
                post,
                brand_style,
                layout_name,
            )

            cached = self._cache_get(cache_key)

            if cached is not None:
                logger.debug(
                    "Render cache HIT | key=%s | layout=%s",
                    cache_key[:24],
                    layout_name,
                )
                return cached

            logger.debug(
                "Render cache MISS | key=%s | layout=%s",
                cache_key[:24],
                layout_name,
            )

            # ─────────────────────────────────────────────────────
            # 6. Preload fonts
            # ─────────────────────────────────────────────────────

            fonts = self._preload_fonts(brand_style)

            # ─────────────────────────────────────────────────────
            # 7. Create canvas
            # ─────────────────────────────────────────────────────

            img = Image.new(
                "RGB",
                (CARD_W, CARD_H),
                brand_style.bg,
            )

            draw = ImageDraw.Draw(img)

            # ─────────────────────────────────────────────────────
            # 8. Render primary path
            # ─────────────────────────────────────────────────────

            png = self._render_with_layout(
                post,
                brand_style,
                fonts,
                draw,
                img,
                layout_name,
            )

            # ─────────────────────────────────────────────────────
            # 9. Validate result
            # ─────────────────────────────────────────────────────

            if self._validate_png(png):
                self._cache_set(cache_key, png)

                elapsed_ms = (
                    time.monotonic() - started
                ) * 1000

                logger.info(
                    "Card rendered | brand='%s' | layout=%s | "
                    "size=%d bytes | %.1fms",
                    brand_style.brand_name,
                    layout_name,
                    len(png),
                    elapsed_ms,
                )

                return png

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

        # ═══════════════════════════════════════════════════════════
        # HCTI FALLBACK
        # ═══════════════════════════════════════════════════════════

        try:
            post = self._normalize_post(post_dict)

            if brand is not None:
                brand_profile = brand

            normalized_brand = self._normalize_brand_profile(
                brand_profile
            )

            brand_style = self._brand_resolver.resolve(
                normalized_brand
            )

            png = self._fallback_hcti(
                post,
                brand_style,
            )

            if self._validate_png(png):
                layout_name = self._select_layout(
                    post,
                    brand_style,
                )

                cache_key = self._cache_key(
                    post,
                    brand_style,
                    layout_name,
                )

                self._cache_set(cache_key, png)

                logger.info(
                    "HCTI fallback succeeded | brand='%s' | bytes=%d",
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
    # LEGACY API
    # ═══════════════════════════════════════════════════════════════

    def render_legacy(
        self,
        title: str = "",
        subtitle: str = "",
        template: str = "default",
        **kwargs: Any,
    ) -> Optional[bytes]:
        """
        Legacy renderer interface used by older RenderQueue code.
        """

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
            "logo_url": (
                kwargs.get("logo_url")
                or kwargs.get("brand_logo")
            ),
            "archetype": kwargs.get("archetype"),
            "font_family": kwargs.get("font_family"),
            "font_scale": kwargs.get("font_scale"),
            "visual_density": kwargs.get("visual_density"),
            "max_hashtags": kwargs.get("max_hashtags"),
            "footer_text": kwargs.get("footer_text"),
            "winner_label": kwargs.get("winner_label"),
        }

        return self.render(
            post_dict,
            brand_profile,
        )

    # ═══════════════════════════════════════════════════════════════
    # V15 COMPATIBILITY
    # ═══════════════════════════════════════════════════════════════

    def render_from_post_dict(
        self,
        post_dict: dict,
    ) -> Optional[bytes]:
        """
        Compatibility interface for v15-era post dictionaries.

        Brand information embedded inside the post is extracted and
        normalized before rendering.
        """

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
            "logo_url": (
                post_dict.get("logo_url")
                or post_dict.get("brand_logo")
            ),
            "archetype": post_dict.get("archetype"),
            "font_family": post_dict.get("font_family"),
            "font_scale": post_dict.get("font_scale"),
            "visual_density": post_dict.get("visual_density"),
            "max_hashtags": post_dict.get("max_hashtags"),
        }

        return self.render(
            post_dict,
            brand_profile,
        )

    # ═══════════════════════════════════════════════════════════════
    # HTML PREVIEW API
    # ═══════════════════════════════════════════════════════════════

    def render_html_only(
        self,
        post_dict: Any = None,
        brand_profile: Any = None,
        *,
        brand: Any = None,
    ) -> Optional[str]:
        """
        Generate HTML without rendering it through Pillow.

        Used for:
            • Dashboard preview
            • HCTI fallback
            • debugging
        """

        try:
            if brand is not None:
                brand_profile = brand

            post = self._normalize_post(post_dict)

            if not post:
                return None

            normalized_brand = self._normalize_brand_profile(
                brand_profile
            )

            brand_style = self._brand_resolver.resolve(
                normalized_brand
            )

            return self._build_html(
                post,
                brand_style,
            )

        except Exception as exc:
            logger.error(
                "render_html_only failed: %s",
                exc,
                exc_info=True,
            )
            return None

    # ═══════════════════════════════════════════════════════════════
    # INPUT NORMALIZATION
    # ═══════════════════════════════════════════════════════════════

    def _normalize_post(self, post: Any) -> dict:
        """
        Convert Pydantic models, dataclasses, mappings and ordinary
        objects into a renderer-compatible dictionary.
        """

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

        # Last-resort object extraction.
        result = {}

        for key in (
            "title",
            "caption",
            "body",
            "content",
            "option_a",
            "option_b",
            "points_a",
            "points_b",
            "items",
            "cells",
            "steps",
            "winner",
            "verdict",
            "callout",
            "cta",
            "recommendation",
            "recommendation_title",
            "quote",
            "attribution",
            "context",
            "topic",
            "theme",
            "hashtags",
            "brand_hashtags",
            "content_type",
            "template",
        ):
            if hasattr(post, key):
                result[key] = getattr(post, key)

        return result

    # ═══════════════════════════════════════════════════════════════
    # BRAND NORMALIZATION
    # ═══════════════════════════════════════════════════════════════

    def _normalize_brand_profile(self, brand: Any) -> dict:
        """
        Normalize Brand objects and dictionaries into the canonical
        BrandStyleResolver input schema.
        """

        if brand is None:
            return {
                "name": DEFAULT_BRAND_NAME,
            }

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

        # Pydantic
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
                    "logo_url": (
                        data.get("logo_url")
                        or data.get("logo_path")
                    ),
                    "hashtags": (
                        data.get("hashtags")
                        or data.get("formatted_hashtags")
                        or ""
                    ),
                    "font_family": data.get("font_family"),
                    "font_scale": data.get("font_scale"),
                    "visual_density": data.get("visual_density"),
                    "max_hashtags": data.get("max_hashtags"),
                    "footer_text": data.get("footer_text"),
                    "winner_label": data.get("winner_label"),
                }

            except Exception as exc:
                logger.debug(
                    "Brand model_dump failed: %s",
                    exc,
                )

        # Generic object
        return {
            "name": (
                getattr(brand, "name", None)
                or DEFAULT_BRAND_NAME
            ),
            "archetype": getattr(
                brand,
                "archetype",
                None,
            ),
            "colors": (
                getattr(brand, "colors", None)
                or getattr(
                    brand,
                    "brand_colors",
                    {},
                )
            ),
            "metadata": (
                getattr(brand, "metadata", None)
                or getattr(
                    brand,
                    "brand_metadata",
                    {},
                )
            ),
            "logo_url": (
                getattr(brand, "logo_url", None)
                or getattr(brand, "logo_path", None)
            ),
            "hashtags": (
                getattr(
                    brand,
                    "formatted_hashtags",
                    None,
                )
                or getattr(
                    brand,
                    "hashtags",
                    "",
                )
            ),
            "font_family": getattr(
                brand,
                "font_family",
                None,
            ),
            "font_scale": getattr(
                brand,
                "font_scale",
                None,
            ),
            "visual_density": getattr(
                brand,
                "visual_density",
                None,
            ),
            "max_hashtags": getattr(
                brand,
                "max_hashtags",
                None,
            ),
            "footer_text": getattr(
                brand,
                "footer_text",
                None,
            ),
            "winner_label": getattr(
                brand,
                "winner_label",
                None,
            ),
        }

    # ═══════════════════════════════════════════════════════════════
    # LAYOUT SELECTION
    # ═══════════════════════════════════════════════════════════════

    def _select_layout(
        self,
        post_dict: dict,
        brand: BrandStyle,
    ) -> str:
        """
        Select layout according to CONTENT structure.

        Brand identity affects visual treatment, not semantic layout.

        Priority:

            1. Explicit content_type
            2. Dedicated quote
            3. Comparison structure
            4. Short quoted text
            5. Explicit steps
            6. Sequence language
            7. Explicit cells
            8. 4+ content items
            9. Bulleted/list structure
            10. Short announcement
            11. Editorial
        """

        title = _safe_string(post_dict.get("title"))
        caption = _safe_string(post_dict.get("caption"))
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

        items = _safe_list(
            post_dict.get("items")
        )

        cells = _safe_list(
            post_dict.get("cells")
        )

        steps = _safe_list(
            post_dict.get("steps")
        )

        content_type = _safe_string(
            post_dict.get("content_type")
        ).lower()

        quote_text = _safe_string(
            post_dict.get("quote")
        )

        # ─────────────────────────────────────────────────────────
        # 1. Explicit content type
        # ─────────────────────────────────────────────────────────

        if content_type:

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
                "promotion": "hero",

                "pipeline": "pipeline",
                "steps": "pipeline",
                "workflow": "pipeline",
                "process": "pipeline",
            }

            layout = type_to_layout.get(content_type)

            if layout in self._layouts:
                return layout

        # ─────────────────────────────────────────────────────────
        # 2. Dedicated quote
        # ─────────────────────────────────────────────────────────

        if quote_text:
            return "quote"

        # ─────────────────────────────────────────────────────────
        # 3. Comparison
        # ─────────────────────────────────────────────────────────

        if option_a and option_b:
            return "comparison"

        # ─────────────────────────────────────────────────────────
        # 4. Short quotation
        # ─────────────────────────────────────────────────────────

        combined = " ".join(
            x for x in (
                title,
                caption,
                body,
            )
            if x
        )

        if (
            len(combined) <= 400
            and any(
                marker in combined
                for marker in _QUOTE_MARKERS
            )
        ):
            return "quote"

        # ─────────────────────────────────────────────────────────
        # 5. Explicit steps
        # ─────────────────────────────────────────────────────────

        if steps:
            return "pipeline"

        # ─────────────────────────────────────────────────────────
        # 6. Sequence language
        # ─────────────────────────────────────────────────────────

        all_items_text = " ".join(
            _safe_string(item)
            for item in (
                points_a
                + points_b
                + items
            )
        )

        sequence_text = (
            f"{combined} {all_items_text}"
        )

        if (
            _SEQUENCE_SIGNALS.search(sequence_text)
            and len(points_a + points_b + items) <= 6
        ):
            return "pipeline"

        # ─────────────────────────────────────────────────────────
        # 7. Explicit grid
        # ─────────────────────────────────────────────────────────

        if cells:
            return "grid"

        # ─────────────────────────────────────────────────────────
        # 8. Four or more items
        # ─────────────────────────────────────────────────────────

        total_points = (
            len(points_a)
            + len(points_b)
            + len(items)
        )

        if total_points >= 4:
            return "grid"

        # ─────────────────────────────────────────────────────────
        # 9. List
        # ─────────────────────────────────────────────────────────

        if items:
            return "list"

        if (
            (option_a or option_b)
            and (points_a or points_b)
        ):
            return "list"

        # ─────────────────────────────────────────────────────────
        # 10. Hero
        # ─────────────────────────────────────────────────────────

        if (
            len(combined) < 200
            and not points_a
            and not points_b
            and not items
        ):
            return "hero"

        # ─────────────────────────────────────────────────────────
        # 11. Editorial
        # ─────────────────────────────────────────────────────────

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
        """
        Dispatch rendering to the selected strategy.
        """

        strategy = self._layouts.get(layout_name)

        if strategy is None:
            logger.warning(
                "Unknown layout '%s'; using editorial fallback",
                layout_name,
            )

            strategy = self._layouts["editorial"]

        result = strategy.render(
            post_dict,
            brand,
            fonts,
            draw,
            img,
        )

        if result is None:
            return None

        return result

    # ═══════════════════════════════════════════════════════════════
    # PNG VALIDATION
    # ═══════════════════════════════════════════════════════════════

    def _validate_png(
        self,
        png: Optional[bytes],
    ) -> bool:
        """
        Validate that rendering actually produced a readable PNG.

        Merely checking len(png) is insufficient because a corrupt
        or non-image byte sequence could otherwise be accepted.
        """

        if not png:
            return False

        if not isinstance(png, (bytes, bytearray)):
            return False

        if len(png) < 100:
            return False

        try:
            with Image.open(io.BytesIO(png)) as image:
                image.verify()

            return True

        except Exception as exc:
            logger.warning(
                "PNG validation failed: %s",
                exc,
            )
            return False

    # ═══════════════════════════════════════════════════════════════
    # CACHE
    # ═══════════════════════════════════════════════════════════════

    def _cache_key(
        self,
        post: dict,
        brand: BrandStyle,
        layout_name: str,
    ) -> str:
        """
        Generate a deterministic cache key.

        The key contains every visual/content property capable of
        changing the resulting image.
        """

        relevant_fields = (
            "title",
            "caption",
            "body",
            "content",
            "option_a",
            "option_b",
            "points_a",
            "points_b",
            "items",
            "cells",
            "steps",
            "winner",
            "verdict",
            "callout",
            "cta",
            "recommendation",
            "recommendation_title",
            "quote",
            "attribution",
            "context",
            "topic",
            "theme",
            "hashtags",
            "brand_hashtags",
            "content_type",
            "template",
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
                brand,
                "logo_path",
                None,
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

        if len(serialized) > MAX_CACHE_PAYLOAD_SIZE:
            logger.warning(
                "Cache payload unusually large: %d bytes",
                len(serialized),
            )

        digest = hashlib.sha256(
            serialized.encode("utf-8")
        ).hexdigest()

        return (
            f"card:v{RENDERER_VERSION}:"
            f"{digest[:24]}"
        )

    def _cache_get(
        self,
        key: str,
    ) -> Optional[bytes]:
        """
        Read cached PNG bytes.
        """

        self._ensure_cache()

        if self._cache is None:
            return None

        try:
            value = self._cache.get(key)

            if self._validate_png(value):
                return value

            if value is not None:
                logger.debug(
                    "Ignoring invalid cached image: %s",
                    key[:24],
                )

        except Exception as exc:
            logger.debug(
                "Cache GET failed | key=%s | %s",
                key[:24],
                exc,
            )

        return None

    def _cache_set(
        self,
        key: str,
        png: Optional[bytes],
    ) -> None:
        """
        Store validated PNG bytes in shared cache.
        """

        if not self._validate_png(png):
            return

        self._ensure_cache()

        if self._cache is None:
            return

        try:
            self._cache.set(
                key,
                png,
            )

        except Exception as exc:
            logger.debug(
                "Cache SET failed | key=%s | %s",
                key[:24],
                exc,
            )

    def _ensure_cache(self) -> None:
        """
        Lazily initialize the shared render cache once.
        """

        if self._cache_initialized:
            return

        self._cache_initialized = True

        try:
            from services.render_cache import render_cache

            self._cache = render_cache

            logger.debug(
                "Render cache initialized"
            )

        except ImportError:
            logger.debug(
                "Shared render_cache unavailable"
            )

        except Exception as exc:
            logger.warning(
                "Render cache initialization failed: %s",
                exc,
            )

    # ═══════════════════════════════════════════════════════════════
    # FONT SYSTEM
    # ═══════════════════════════════════════════════════════════════

    def _preload_fonts(
        self,
        brand: Optional[BrandStyle] = None,
    ) -> dict:
        """
        Preload the font sizes commonly used by all layouts.

        Brand font_scale is applied centrally.
        """

        scale = 1.0

        if brand is not None:
            try:
                scale = float(
                    getattr(
                        brand,
                        "font_scale",
                        1.0,
                    )
                    or 1.0
                )
            except (TypeError, ValueError):
                scale = 1.0

        # Prevent pathological configuration from creating enormous
        # fonts or effectively invisible typography.
        scale = max(
            0.75,
            min(scale, 1.50),
        )

        sizes_css = (
            10, 11, 12, 13, 14, 15, 16,
            17, 18, 19, 20, 22, 24, 26,
            28, 30, 32, 35, 36, 40, 42,
            47, 50, 54, 58, 64, 72, 80,
        )

        fonts = {
            "bold": {},
            "regular": {},
        }

        for size_css in sizes_css:

            scaled_size = max(
                1,
                int(
                    size_css
                    * scale
                    * SCALE
                ),
            )

            key = size_css * SCALE

            fonts["bold"][key] = load_font(
                FONT_BOLD_PATH,
                scaled_size,
            )

            fonts["regular"][key] = load_font(
                FONT_REGULAR_PATH,
                scaled_size,
            )

        return fonts

    # ═══════════════════════════════════════════════════════════════
    # HCTI FALLBACK
    # ═══════════════════════════════════════════════════════════════

    def _fallback_hcti(
        self,
        post_dict: dict,
        brand: BrandStyle,
    ) -> Optional[bytes]:
        """
        External HCTI fallback.

        HCTI is deliberately isolated from the primary renderer so
        external-service failures never corrupt the Pillow path.
        """

        if not self._hcti_configured:
            logger.debug(
                "HCTI fallback skipped: credentials unavailable"
            )
            return None

        html = self._build_html(
            post_dict,
            brand,
        )

        if not html:
            logger.error(
                "HCTI fallback skipped: HTML generation failed"
            )
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
                logger.error(
                    "HCTI API failed | status=%s | body=%s",
                    response.status_code,
                    _safe_string(
                        getattr(
                            response,
                            "text",
                            "",
                        )
                    )[:300],
                )
                return None

            data = response.json()

            image_url = _safe_string(
                data.get("url")
            )

            if not image_url:
                logger.error(
                    "HCTI response did not contain image URL"
                )
                return None

            image_response = hcti.get(
                image_url,
                timeout=HCTI_DOWNLOAD_TIMEOUT,
            )

            if image_response.status_code != 200:
                logger.error(
                    "HCTI image download failed | status=%s",
                    image_response.status_code,
                )
                return None

            png = image_response.content

            if not self._validate_png(png):
                logger.error(
                    "HCTI returned invalid PNG"
                )
                return None

            logger.info(
                "HCTI render successful | brand='%s' | bytes=%d",
                brand.brand_name,
                len(png),
            )

            return png

        except Exception as exc:
            logger.error(
                "HCTI fallback exception: %s",
                exc,
                exc_info=True,
            )
            return None

    # ═══════════════════════════════════════════════════════════════
    # JINJA
    # ═══════════════════════════════════════════════════════════════

    def _init_jinja(
        self,
    ) -> Optional[Environment]:
        """
        Lazily initialize Jinja2 environment.
        """

        if self._jinja is not None:
            return self._jinja

        if not TEMPLATES_DIR.exists():
            logger.warning(
                "Card templates directory does not exist: %s",
                TEMPLATES_DIR,
            )
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
                "Jinja initialization failed: %s",
                exc,
                exc_info=True,
            )
            return None

    # ═══════════════════════════════════════════════════════════════
    # HTML BUILDING
    # ═══════════════════════════════════════════════════════════════

    def _build_html(
        self,
        post_dict: dict,
        brand: BrandStyle,
    ) -> Optional[str]:
        """
        Build HTML for HCTI and dashboard preview.
        """

        env = self._init_jinja()

        if env is None:
            return None

        try:
            template = env.get_template(
                "card.html"
            )

        except Exception as exc:
            logger.error(
                "card.html template load failed: %s",
                exc,
            )
            return None

        context = self._build_context(
            post_dict,
            brand,
        )

        try:
            html = template.render(
                **context
            )

            return html

        except Exception as exc:
            logger.error(
                "Jinja card render failed: %s",
                exc,
                exc_info=True,
            )
            return None

    # ═══════════════════════════════════════════════════════════════
    # TEMPLATE CONTEXT
    # ═══════════════════════════════════════════════════════════════

    def _build_context(
        self,
        post_dict: dict,
        brand: BrandStyle,
    ) -> dict:
        """
        Build the canonical context consumed by card.html.
        """

        title = _safe_string(
            post_dict.get("title")
        )

        theme_raw = _safe_string(
            post_dict.get("theme"),
            "ai",
        )

        theme_val = (
            theme_raw
            .split(".")[-1]
            .lower()
        )

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

        is_debate = bool(
            option_a and option_b
        )

        layout_kind = pick_layout_kind(
            theme_val,
            is_debate=is_debate,
            points_a=points_a,
            points_b=points_b,
            winner=winner_raw,
        )

        palette = dict(
            palette_for(theme_val)
        )

        # ─────────────────────────────────────────────────────────
        # Brand color overrides
        # ─────────────────────────────────────────────────────────

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
            palette.get(
                "a",
                "#6366f1",
            ),
            0.18,
        )

        # ─────────────────────────────────────────────────────────
        # Icon
        # ─────────────────────────────────────────────────────────

        try:
            icon_svg = get_icon_svg(
                theme_val,
                color=palette.get(
                    "accent",
                    "#818cf8",
                ),
                size=20,
            )
        except Exception as exc:
            logger.debug(
                "Icon generation failed: %s",
                exc,
            )
            icon_svg = ""

        # ─────────────────────────────────────────────────────────
        # Hashtags
        # ─────────────────────────────────────────────────────────

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

        elif isinstance(hashtags, (tuple, list)):
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
                    brand,
                    "max_hashtags",
                    5,
                )
                or 5
            )
        except (TypeError, ValueError):
            max_hashtags = 5

        max_hashtags = max(
            0,
            min(max_hashtags, 30),
        )

        hashtags = hashtags[:max_hashtags]

        # ─────────────────────────────────────────────────────────
        # Bullets
        # ─────────────────────────────────────────────────────────

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

        # ─────────────────────────────────────────────────────────
        # Winner
        # ─────────────────────────────────────────────────────────

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
                            winner_name,
                            40,
                        ),
                        "reason": truncate_chars(
                            reason or "",
                            120,
                        ),
                    }

            except Exception as exc:
                logger.debug(
                    "Winner parsing failed: %s",
                    exc,
                )

        # ─────────────────────────────────────────────────────────
        # Base context
        # ─────────────────────────────────────────────────────────

        context = {
            "title": truncate_chars(
                title,
                MAX_TITLE_LENGTH,
            ),

            "theme_label": (
                theme_val
                .replace("_", " ")
                .upper()
            ),

            "hashtags": hashtags,

            "bg_decoration": "",

            "font_face_css": font_face_css(),

            "font_size": FONT_SIZE,

            "palette": palette,

            "layout_kind": layout_kind,

            "brand_name": brand.brand_name,

            "brand_logo": getattr(
                brand,
                "logo_path",
                None,
            ),

            "dark_mode": getattr(
                brand,
                "dark_mode",
                False,
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

        # ═════════════════════════════════════════════════════════
        # COMPARISON
        # ═════════════════════════════════════════════════════════

        if layout_kind in (
            "vs",
            "before_after",
        ):

            name_a = (
                truncate_chars(
                    option_a,
                    22,
                )
                or "Option A"
            )

            name_b = (
                truncate_chars(
                    option_b,
                    22,
                )
                or "Option B"
            )

            context["columns"] = [
                {
                    "name": name_a,
                    "side": "left",
                    "color": palette.get(
                        "a",
                        "#6366f1",
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
                        "b",
                        "#0ea5e9",
                    ),
                    "bullets": bullets_b,
                    "strength_label": (
                        "STRENGTHS"
                        if layout_kind == "vs"
                        else "AFTER"
                    ),
                },
            ]

        # ═════════════════════════════════════════════════════════
        # GRID / PIPELINE
        # ═════════════════════════════════════════════════════════

        elif layout_kind in (
            "grid",
            "pipeline",
        ):

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
                        text,
                        90,
                    ),
                    "accent": index == 0,
                }
                for index, (
                    label,
                    text,
                ) in enumerate(
                    zip(
                        labels,
                        pool,
                    )
                )
            ]

        # ═════════════════════════════════════════════════════════
        # PIPELINE STEPS
        # ═════════════════════════════════════════════════════════
        #
        # Some layouts may consume explicit "steps" instead of cells.
        # Preserve them in the context as a canonical representation.

        if steps_raw:

            normalized_steps = []

            for index, step in enumerate(
                steps_raw[:6],
                start=1,
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

                normalized_steps.append(
                    {
                        "number": index,
                        "title": truncate_chars(
                            _safe_string(
                                step_title
                            ),
                            35,
                        ),
                        "text": truncate_chars(
                            _safe_string(
                                step_text
                            ),
                            90,
                        ),
                    }
                )

            context["steps"] = normalized_steps

        # ═════════════════════════════════════════════════════════
        # LIST / EDITORIAL / HERO / QUOTE
        # ═════════════════════════════════════════════════════════

        else:

            pool = fit_bullets_dynamic(
                points_a + points_b,
                max_count=6,
                min_count=3,
                max_words=6,
            )

            # If the AI supplied explicit items, prefer them.
            if items_raw:

                pool = [
                    truncate_chars(
                        item,
                        70,
                    )
                    for item in items_raw[:6]
                ]

            context["items"] = [
                {
                    "num": index + 1,
                    "text": text,
                }
                for index, text in enumerate(pool)
            ]

        return context


# ═════════════════════════════════════════════════════════════════════
# MODULE SINGLETON
# ═════════════════════════════════════════════════════════════════════

card_renderer = CardRenderer()
