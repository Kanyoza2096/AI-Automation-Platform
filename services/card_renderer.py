"""
Card Renderer v13 — production-quality design-system engine.

Pipeline (per render call):
  0. RenderCache    — return cached PNG immediately on hit
  1. SvgRenderer    — theme-aware decorative background SVG (varied pattern)
  2. HtmlRenderer   — Jinja2 template with adaptive layout + icon injection
  3. BrowserManager — pooled Playwright page (no Chromium launch per call)
     └─ HCTI fallback — htmlcsstoimage.com if BrowserManager is unavailable

Layout selection is now content-aware (see typography.pick_layout_kind):
it inspects bullet counts, sequence-signal words, and debate structure —
not just the theme label — so posts of the same theme can still render
with different, better-fitted layouts (vs / before_after / grid / pipeline
/ list).

Rendering is supersampled: Playwright captures the 1080×1080 CSS viewport
at device_scale_factor=2 → a 2160×2160 physical PNG, which is dramatically
sharper than a native 1080 render, especially for text edges and hairline
borders.
"""

import logging
import os
from pathlib import Path
from typing import Any, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from services.rendering.fonts import font_face_css
from services.rendering.layout import AUTOFIT_JS, FONT_SIZE, palette_for, hex_to_rgba, scale_for_density
from services.rendering.typography import (
    parse_winner,
    prepare_bullets,
    fit_bullets_dynamic,
    truncate_chars,
    pick_layout_kind,
)
from services.rendering.icon_library import get_icon_svg

logger = logging.getLogger("kanyoza.services.card_renderer")

TEMPLATES_DIR = Path(__file__).parent.parent / "templates" / "cards"

CARD_W = 1080
CARD_H = 1080
DEVICE_SCALE_FACTOR = 2          # physical render = 2160×2160 → sharp output

WINNER_LABEL_BY_LAYOUT = {
    "vs":           "WINNER",
    "before_after": "BOTTOM LINE",
    "grid":         "RECOMMENDATION",
    "list":         "PRO TIP",
    "pipeline":     "KEY INSIGHT",
}

# Structural labels per grid/pipeline cell — used only when the content
# doesn't supply its own natural label (kept short, all-caps, scannable).
GRID_LABELS = ["APPROACH", "TRADE-OFF", "BEST FOR", "VERDICT"]
PIPELINE_LABELS = ["STEP 1", "STEP 2", "STEP 3", "STEP 4"]


class CardRenderer:
    """
    Renders a TechPost to a 2160×2160 PNG (1080 CSS px × 2× device scale).

    Always returns None on total failure — never raises.
    """

    def __init__(self):
        self._jinja: Optional[Environment] = None
        self._playwright_ok: Optional[bool] = None
        self._hcti_user: str = ""
        self._hcti_key:  str = ""
        self._init_jinja()
        self._init_hcti()

    # ── Cache helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _cache_get(html: str) -> Optional[bytes]:
        try:
            from services.render_cache import render_cache
            return render_cache.get(render_cache.key_for(html))
        except Exception:
            return None

    @staticmethod
    def _cache_set(html: str, png: bytes) -> None:
        try:
            from services.render_cache import render_cache
            render_cache.set(render_cache.key_for(html), png)
        except Exception:
            pass

    # ── Init ─────────────────────────────────────────────────────────────────

    def _init_jinja(self) -> None:
        if not TEMPLATES_DIR.exists():
            logger.warning("templates/cards/ not found — HTML rendering disabled")
            return
        self._jinja = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            autoescape=select_autoescape(["html"]),
        )
        logger.info("Jinja2 card environment ready (%s)", TEMPLATES_DIR)

    def _init_hcti(self) -> None:
        self._hcti_user = os.environ.get("HCTI_USER_ID", "")
        self._hcti_key  = os.environ.get("HCTI_API_KEY",  "")

    def _check_playwright(self) -> bool:
        if self._playwright_ok is None:
            try:
                from playwright.sync_api import sync_playwright  # noqa: F401
                self._playwright_ok = True
            except ImportError:
                self._playwright_ok = False
                logger.warning("Playwright not available — will try HCTI fallback")
        return self._playwright_ok  # type: ignore[return-value]

    # ── Step 1: background SVG ───────────────────────────────────────────────

    def _build_svg(self, post) -> str:
        from services.svg_renderer import svg_renderer
        return svg_renderer.render(post)

    # ── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _safe_points(points) -> list:
        return [p for p in (points or []) if p]



    # ── Step 2: build Jinja2 context ────────────────-------------------------

    def _build_context(self, post, bg_decoration: str, theme_val: str, brand: Optional[Any] = None) -> dict:
        points_a = self._safe_points(post.points_a)
        points_b = self._safe_points(post.points_b)

        is_structured_comparison = getattr(post, "is_structured_comparison", False)

        layout_kind = (
            "comparison_matrix" if is_structured_comparison else
            pick_layout_kind(
                theme_val,
                is_debate=post.is_debate,
                points_a=points_a,
                points_b=points_b,
                winner=post.winner or "",
            )
        )

        palette = dict(palette_for(theme_val))          # mutable copy
        
        # Override palette colors with brand colors if present
        if brand and getattr(brand, "colors", None):
            brand_colors = brand.colors
            if isinstance(brand_colors, str):
                try:
                    import json
                    brand_colors = json.loads(brand_colors)
                except Exception:
                    brand_colors = {}
            if brand_colors:
                if "primary" in brand_colors:
                    palette["a"] = brand_colors["primary"]
                    palette["accent"] = brand_colors["primary"]
                if "secondary" in brand_colors:
                    palette["b"] = brand_colors["secondary"]
                if "background" in brand_colors:
                    palette["bg"] = brand_colors["background"]
                if "text" in brand_colors:
                    palette["text"] = brand_colors["text"]
                    palette["title"] = brand_colors["text"]

        palette["glow_rgba"] = hex_to_rgba(palette["a"], 0.18)

        from config.settings import settings as _settings

        icon_svg = get_icon_svg(theme_val, color=palette["accent"], size=20)

        # Check for dark mode in brand settings or metadata, default to True
        dark_mode = True
        if brand and hasattr(brand, "metadata") and brand.metadata:
            dark_mode = brand.metadata.get("dark_mode", True)

        ctx: dict = {
            "title":         truncate_chars(post.title, 90),
            "theme_label":   theme_val.replace("_", " ").upper(),
            "hashtags":      (post.hashtags or [])[:4],
            "bg_decoration": bg_decoration,
            "font_face_css": font_face_css(),
            "font_size":     FONT_SIZE,
            "palette":       palette,
            "layout_kind":   layout_kind,
            "brand_name":    brand.name if brand else _settings.brand_name,
            "brand_logo":    brand.logo_url if brand else None,
            "dark_mode":     dark_mode,
            "icon_svg":      icon_svg,
            # Populated below by layout branch
            "columns":         [],
            "cells":           [],
            "items":           [],
            "winner":          None,
            "cm_left":         None,
            "cm_right":        None,
            "cm_facts":        [],
            "cm_recommendation": None,
        }

        # ── Layout-specific data ─────────────────────────────────────────────
        if layout_kind == "comparison_matrix":
            name_a = truncate_chars(post.option_a, 22) or "Option A"
            name_b = truncate_chars(post.option_b, 22) or "Option B"
            ctx["cm_left"] = {
                "title":   name_a,
                "content": truncate_chars(post.left_content, 140),
            }
            ctx["cm_right"] = {
                "title":   name_b,
                "content": truncate_chars(post.right_content, 140),
            }
            ctx["cm_facts"] = [
                {"label": "TRADE-OFF", "text": truncate_chars(post.tradeoff, 130)},
                {"label": "BEST FOR",  "text": truncate_chars(post.best_for, 130)},
                {"label": "VERDICT",   "text": truncate_chars(post.verdict, 130)},
            ]

        elif layout_kind in ("vs", "before_after"):
            name_a = truncate_chars(post.option_a, 22) or "Option A"
            name_b = truncate_chars(post.option_b, 22) or "Option B"
            bullets_a = fit_bullets_dynamic(points_a, max_count=4, min_count=2, max_words=5)
            bullets_b = fit_bullets_dynamic(points_b, max_count=4, min_count=2, max_words=5)
            bullet_font = scale_for_density(
                max(len(bullets_a), len(bullets_b)),
                FONT_SIZE["bullet_max"], FONT_SIZE["bullet_min"],
            )
            ctx["bullet_font"] = bullet_font
            ctx["columns"] = [
                {
                    "name":    name_a,
                    "side":    "left",
                    "color":   palette["a"],
                    "bullets": bullets_a,
                    "strength_label": "STRENGTHS" if layout_kind == "vs" else "BEFORE",
                },
                {
                    "name":    name_b,
                    "side":    "right",
                    "color":   palette["b"],
                    "bullets": bullets_b,
                    "strength_label": "STRENGTHS" if layout_kind == "vs" else "AFTER",
                },
            ]

        elif layout_kind in ("grid", "pipeline"):
            labels = PIPELINE_LABELS if layout_kind == "pipeline" else GRID_LABELS
            pool = fit_bullets_dynamic(
                (points_a + points_b), max_count=4, min_count=3, max_words=8,
            )
            while len(pool) < 4:
                pool.append("\u2014")
            cell_font = scale_for_density(len(pool), 24, 17, step=2)
            ctx["cell_font"] = cell_font
            ctx["cells"] = [
                {"label": lbl, "text": txt, "accent": idx == 0}
                for idx, (lbl, txt) in enumerate(zip(labels, pool))
            ]

        else:  # list / checklist / tutorial
            pool = fit_bullets_dynamic(
                (points_a + points_b), max_count=6, min_count=3, max_words=6,
            )
            item_font = scale_for_density(len(pool), 24, 16, step=2)
            ctx["item_font"] = item_font
            ctx["items"] = [{"num": i + 1, "text": txt} for i, txt in enumerate(pool)]

        # ── Winner / recommendation ──────────────────────────────────────────
        if layout_kind == "comparison_matrix":
            if post.recommendation:
                ctx["cm_recommendation"] = {
                    "label": (post.recommendation_title or "Recommendation").upper(),
                    "text":  truncate_chars(post.recommendation, 220),
                }
        elif post.winner:
            name, reason = parse_winner(post.winner, post.option_a, post.option_b)
            if name:
                ctx["winner"] = {
                    "label":  WINNER_LABEL_BY_LAYOUT.get(layout_kind, "WINNER"),
                    "name":   name,
                    "reason": reason,
                }

        return ctx

    # ── Step 2: render Jinja2 template ───────────────────────────────────────

    def _build_html(self, post, svg_chart: str, brand: Optional[Any] = None) -> Optional[str]:
        if not self._jinja:
            return None

        theme_val = post.theme.value if hasattr(post.theme, "value") else str(post.theme)

        try:
            tmpl = self._jinja.get_template("card.html")
        except Exception as exc:
            logger.error("card.html not found: %s", exc)
            return None

        ctx = self._build_context(post, svg_chart, theme_val, brand=brand)
        try:
            return tmpl.render(**ctx)
        except Exception as exc:
            logger.error("Jinja2 render error: %s", exc)
            return None

    # ── Step 3a: Playwright screenshot ───────────────────────────────────────

    def _screenshot_playwright(self, html: str) -> Optional[bytes]:
        # Fast path — BrowserManager's dedicated Playwright thread.
        try:
            from services.browser_manager import browser_manager
            if browser_manager.is_running:
                png = browser_manager.render_with_page(
                    lambda page: self._render_on_page(page, html)
                )
                logger.info(
                    "BrowserManager rendered card (%d bytes, %dx%d physical px)",
                    len(png), CARD_W * DEVICE_SCALE_FACTOR, CARD_H * DEVICE_SCALE_FACTOR,
                )
                return png
        except Exception as exc:
            logger.warning("BrowserManager render failed (%s) — one-shot fallback", exc)

        # Fallback — one-shot Chromium instance.
        try:
            from services.browser_manager import browser_manager as _bm

            def _one_shot() -> bytes:
                from playwright.sync_api import sync_playwright
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(
                        args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
                        headless=True,
                    )
                    page = browser.new_page(
                        viewport={"width": CARD_W, "height": CARD_H},
                        device_scale_factor=DEVICE_SCALE_FACTOR,
                    )
                    png = self._render_on_page(page, html)
                    browser.close()
                    return png

            if _bm._started:
                png = _bm.call_in_thread(_one_shot, timeout=60.0)
            else:
                png = _one_shot()

            logger.info(
                "One-shot Playwright rendered card (%d bytes, %dx%d physical px)",
                len(png), CARD_W * DEVICE_SCALE_FACTOR, CARD_H * DEVICE_SCALE_FACTOR,
            )
            return png
        except Exception as exc:
            logger.warning("Playwright screenshot failed: %s", exc)
            return None

    @staticmethod
    def _render_on_page(page, html: str) -> bytes:
        """
        Load HTML, wait for fonts, run autofit + quality-control JS safety
        net, capture PNG. Shared by pooled and one-shot paths.
        """
        page.set_content(html, wait_until="networkidle")
        try:
            page.evaluate("document.fonts.ready.then(() => true)")
        except Exception:
            pass
        page.wait_for_load_state("networkidle")
        try:
            page.evaluate(AUTOFIT_JS)
        except Exception as exc:
            logger.debug("Autofit JS failed (non-fatal): %s", exc)
        return page.screenshot(type="png", full_page=False)

    # ── Step 3b: HCTI fallback ───────────────────────────────────────────────

    def _screenshot_hcti(self, html: str) -> Optional[bytes]:
        if not (self._hcti_user and self._hcti_key):
            return None
        try:
            from utils.http_client import HttpClient as _HC
            hcti = _HC("hcti", default_timeout=30)
            resp = hcti.post(
                "https://hcti.io/v1/image",
                data={
                    "html": html,
                    "viewport_width":  CARD_W,
                    "viewport_height": CARD_H,
                    "device_scale":    DEVICE_SCALE_FACTOR,
                },
                auth=(self._hcti_user, self._hcti_key),
            )
            if resp.status_code != 200:
                logger.warning("HCTI returned HTTP %s", resp.status_code)
                return None
            url = resp.json().get("url", "")
            if not url:
                return None
            img = hcti.get(url, timeout=20)
            if img.status_code == 200:
                logger.info("HCTI fallback rendered card (%d bytes)", len(img.content))
                return img.content
        except Exception as exc:
            logger.warning("HCTI fallback failed: %s", exc)
        return None

    # ── Public API ───────────────────────────────────────────────────────────

    def render(self, post, brand: Optional[Any] = None) -> Optional[bytes]:
        """
        Full pipeline: TechPost → PNG bytes (2160×2160) or None on total failure.

        Cache → SVG bg → Jinja2 HTML → BrowserManager / one-shot Playwright / HCTI
        """
        svg  = self._build_svg(post)
        html = self._build_html(post, svg, brand=brand)
        if not html:
            logger.debug("HTML render produced nothing — skipping screenshot")
            return None

        cached = self._cache_get(html)
        if cached is not None:
            logger.debug("Returning cached PNG (%d bytes)", len(cached))
            return cached

        if self._check_playwright():
            png = self._screenshot_playwright(html)
            if png:
                self._cache_set(html, png)
                return png

        png = self._screenshot_hcti(html)
        if png:
            self._cache_set(html, png)
        return png

    def render_html_only(self, post, brand: Optional[Any] = None) -> Optional[str]:
        """Return the rendered HTML string (useful for debugging)."""
        svg = self._build_svg(post)
        return self._build_html(post, svg, brand=brand)


card_renderer = CardRenderer()
