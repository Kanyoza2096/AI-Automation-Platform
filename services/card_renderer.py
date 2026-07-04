"""
Card Renderer v12 — design-system upgrade.

Pipeline (per render call):
  0. RenderCache    — return cached PNG immediately on hit
  1. SvgRenderer    — theme-aware decorative background SVG
  2. HtmlRenderer   — Jinja2 template with semantic layout + icon injection
  3. BrowserManager — pooled Playwright page (no Chromium launch per call)
     └─ HCTI fallback — htmlcsstoimage.com if BrowserManager is unavailable

Layout selection (theme → layout kind):
  vs          — comparison / security / database / ai
  before_after — code_tips / backend / frontend
  grid        — architecture / cloud
  pipeline    — system_design
  list        — tutorial / checklist
"""

import logging
import os
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from services.rendering.fonts import font_face_css
from services.rendering.layout import AUTOFIT_JS, FONT_SIZE, palette_for, hex_to_rgba
from services.rendering.typography import parse_winner, prepare_bullets, truncate_chars
from services.rendering.icon_library import get_icon_svg

logger = logging.getLogger("kanyoza.services.card_renderer")

TEMPLATES_DIR = Path(__file__).parent.parent / "templates" / "cards"

CARD_W = 1080
CARD_H = 1080
DEVICE_SCALE_FACTOR = 2          # physical render = 2160×2160 → sharp output

# ── Theme → layout kind ─────────────────────────────────────────────────────
THEME_LAYOUT_MAP: dict = {
    "comparison":    "vs",
    "security":      "vs",
    "database":      "vs",
    "ai":            "vs",
    "code_tips":     "before_after",
    "backend":       "before_after",
    "frontend":      "before_after",
    "architecture":  "grid",
    "system_design": "pipeline",
    "cloud":         "grid",
    "tutorial":      "list",
    "checklist":     "list",
}

WINNER_LABEL_BY_LAYOUT = {
    "vs":           "WINNER",
    "before_after": "BOTTOM LINE",
    "grid":         "RECOMMENDATION",
    "list":         "PRO TIP",
    "pipeline":     "KEY INSIGHT",
}


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

    # ── Step 2: build Jinja2 context ─────────────────────────────────────────

    def _build_context(self, post, bg_decoration: str, theme_val: str) -> dict:
        layout_kind = THEME_LAYOUT_MAP.get(theme_val, "vs")
        palette     = dict(palette_for(theme_val))          # mutable copy

        # Pre-compute rgba glow string for CSS box-shadow / no extra lib needed
        palette["glow_rgba"] = hex_to_rgba(palette["a"], 0.18)

        points_a = self._safe_points(post.points_a)
        points_b = self._safe_points(post.points_b)

        from config.settings import settings as _settings

        # Topic icon — inline SVG scaled for the card header
        icon_svg = get_icon_svg(theme_val, color=palette["accent"], size=20)

        ctx: dict = {
            "title":         truncate_chars(post.title, 100),
            "theme_label":   theme_val.replace("_", " ").upper(),
            "hashtags":      (post.hashtags or [])[:4],
            "bg_decoration": bg_decoration,
            "font_face_css": font_face_css(),
            "font_size":     FONT_SIZE,
            "palette":       palette,
            "layout_kind":   layout_kind,
            "brand_name":    _settings.brand_name,
            "icon_svg":      icon_svg,
            # Populated below by layout branch
            "columns":  [],
            "cells":    [],
            "items":    [],
            "winner":   None,
        }

        # ── Layout-specific data ─────────────────────────────────────────────
        if layout_kind in ("vs", "before_after"):
            name_a = truncate_chars(post.option_a, 24) or "Option A"
            name_b = truncate_chars(post.option_b, 24) or "Option B"
            ctx["columns"] = [
                {
                    "name":    name_a,
                    "side":    "left",
                    "color":   palette["a"],
                    "bullets": prepare_bullets(points_a, limit=4),
                },
                {
                    "name":    name_b,
                    "side":    "right",
                    "color":   palette["b"],
                    "bullets": prepare_bullets(points_b, limit=4),
                },
            ]

        elif layout_kind in ("grid", "pipeline"):
            labels = {
                "grid":     ["APPROACH", "TRADE-OFF", "BEST FOR", "VERDICT"],
                "pipeline": ["STEP 1", "STEP 2", "STEP 3", "STEP 4"],
            }[layout_kind]
            pool = prepare_bullets((points_a + points_b), limit=4, max_words=8)
            while len(pool) < 4:
                pool.append("\u2014")
            ctx["cells"] = [
                {"label": lbl, "text": txt, "accent": idx == 0}
                for idx, (lbl, txt) in enumerate(zip(labels, pool))
            ]

        else:  # list / checklist / tutorial
            pool = prepare_bullets((points_a + points_b), limit=6, max_words=6)
            ctx["items"] = [{"num": i + 1, "text": txt} for i, txt in enumerate(pool)]

        # ── Winner ───────────────────────────────────────────────────────────
        if post.winner:
            name, reason = parse_winner(post.winner, post.option_a, post.option_b)
            if name:
                ctx["winner"] = {
                    "label":  WINNER_LABEL_BY_LAYOUT.get(layout_kind, "WINNER"),
                    "name":   name,
                    "reason": reason,
                }

        return ctx

    # ── Step 2: render Jinja2 template ───────────────────────────────────────

    def _build_html(self, post, svg_chart: str) -> Optional[str]:
        if not self._jinja:
            return None

        theme_val = post.theme.value if hasattr(post.theme, "value") else str(post.theme)

        try:
            tmpl = self._jinja.get_template("card.html")
        except Exception as exc:
            logger.error("card.html not found: %s", exc)
            return None

        ctx = self._build_context(post, svg_chart, theme_val)
        try:
            return tmpl.render(**ctx)
        except Exception as exc:
            logger.error("Jinja2 render error: %s", exc)
            return None

    # ── Step 3a: Playwright screenshot ───────────────────────────────────────

    def _screenshot_playwright(self, html: str) -> Optional[bytes]:
        # Fast path — pooled page from BrowserManager
        try:
            from services.browser_manager import browser_manager
            if browser_manager.is_running:
                with browser_manager.acquire_page() as page:
                    png = self._render_on_page(page, html)
                logger.info(
                    "BrowserManager rendered card (%d bytes, %dx%d physical px)",
                    len(png), CARD_W * DEVICE_SCALE_FACTOR, CARD_H * DEVICE_SCALE_FACTOR,
                )
                return png
        except Exception as exc:
            logger.warning("BrowserManager render failed (%s) — one-shot fallback", exc)

        # Fallback — launch a one-shot Chromium instance
        try:
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
        Load HTML, wait for fonts, run autofit JS safety net, capture PNG.
        Shared by pooled and one-shot paths.
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
        # full_page=False → screenshot the viewport exactly (1080×1080 CSS px
        # → 2160×2160 physical px with DEVICE_SCALE_FACTOR=2)
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

    def render(self, post) -> Optional[bytes]:
        """
        Full pipeline: TechPost → PNG bytes (2160×2160) or None on total failure.

        Cache → SVG bg → Jinja2 HTML → BrowserManager / one-shot Playwright / HCTI
        """
        svg  = self._build_svg(post)
        html = self._build_html(post, svg)
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

    def render_html_only(self, post) -> Optional[str]:
        """Return the rendered HTML string (useful for debugging)."""
        svg = self._build_svg(post)
        return self._build_html(post, svg)


card_renderer = CardRenderer()
