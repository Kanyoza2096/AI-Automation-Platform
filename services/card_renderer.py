"""
Card Renderer v11.0 — SVG + Jinja2 + Playwright pipeline.

Pipeline (per render call) — unchanged shape from v10:
  1. SvgRenderer    — builds a decorative (text-free) background SVG
  2. HtmlRenderer   — renders a Jinja2 template; all copy is real HTML/CSS text
  3. Playwright     — headless Chromium screenshots HTML → PNG 1080x1080
  4. HCTI fallback  — htmlcsstoimage.com if Playwright unavailable

The card is always 1080x1080 pixels (square — optimal for Facebook / Instagram).

Typography, spacing, and palette decisions live in services/rendering/*
so this module stays focused on orchestration: build context → render
template → screenshot → return bytes. See services/rendering/layout.py
for design tokens and services/rendering/typography.py for text prep.
"""

import logging
import os
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from services.rendering.fonts import font_face_css
from services.rendering.layout import AUTOFIT_JS, FONT_SIZE, palette_for
from services.rendering.typography import parse_winner, prepare_bullets, truncate_chars

logger = logging.getLogger("kanyoza.services.card_renderer")

TEMPLATES_DIR = Path(__file__).parent.parent / "templates" / "cards"

CARD_W = 1080
CARD_H = 1080
DEVICE_SCALE_FACTOR = 2

# Maps content theme -> visual layout kind. Kept separate from the theme
# palette (services/rendering/layout.THEME_PALETTES) so adding a new
# theme never requires touching layout logic.
THEME_LAYOUT_MAP: dict = {
    "comparison":    "vs",
    "security":      "vs",
    "database":      "vs",
    "ai":            "vs",
    "code_tips":     "before_after",
    "backend":       "before_after",
    "frontend":      "before_after",
    "architecture":  "grid",
    "system_design": "grid",
    "cloud":         "grid",
    "tutorial":      "list",
    "checklist":     "list",
}

WINNER_LABEL_BY_LAYOUT = {
    "vs":           "WINNER",
    "before_after": "BOTTOM LINE",
    "grid":         "RECOMMENDATION",
    "list":         "PRO TIP",
}


class CardRenderer:
    """
    Renders a TechPost to a 1080x1080 PNG using:
      SVGRenderer (decorative bg) → Jinja2 HTML template → Playwright screenshot.

    Falls back to HCTI if Playwright is not available.
    Never raises — always returns None on total failure.
    """

    def __init__(self):
        self._jinja: Optional[Environment] = None
        self._playwright_ok: Optional[bool] = None
        self._hcti_user: str = ""
        self._hcti_key: str = ""
        self._init_jinja()
        self._init_hcti()

    # ── Init ────────────────────────────────────────────────────────────────────

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
        self._hcti_key  = os.environ.get("HCTI_API_KEY", "")

    def _check_playwright(self) -> bool:
        if self._playwright_ok is None:
            try:
                from playwright.sync_api import sync_playwright  # noqa: F401
                self._playwright_ok = True
            except ImportError:
                self._playwright_ok = False
                logger.warning("Playwright not available — will try HCTI fallback")
        return self._playwright_ok

    # ── Step 1: SVG Renderer (decorative background only) ─────────────────────

    def _build_svg(self, post) -> str:
        from services.svg_renderer import svg_renderer
        return svg_renderer.render(post)

    # ── Content shaping (typography + layout tokens, no business logic) ───────

    @staticmethod
    def _safe_points(points) -> list:
        return [p for p in (points or []) if p]

    def _build_context(self, post, bg_decoration: str, theme_val: str) -> dict:
        layout_kind = THEME_LAYOUT_MAP.get(theme_val, "vs")
        palette = palette_for(theme_val)

        points_a = self._safe_points(post.points_a)
        points_b = self._safe_points(post.points_b)

        ctx = {
            "title":        truncate_chars(post.title, 110),
            "theme_label":  theme_val.replace("_", " ").title(),
            "hashtags":     (post.hashtags or [])[:5],
            "bg_decoration": bg_decoration,
            "font_face_css": font_face_css(),
            "autofit_js":    AUTOFIT_JS,
            "font_size":     FONT_SIZE,
            "palette":       palette,
            "layout_kind":   layout_kind,
            "columns":      [],
            "cells":        [],
            "items":        [],
            "winner":       None,
        }

        if layout_kind in ("vs", "before_after"):
            name_a = truncate_chars(post.option_a, 26) or "Option A"
            name_b = truncate_chars(post.option_b, 26) or "Option B"
            ctx["columns"] = [
                {
                    "name": name_a,
                    "kind": "bad" if layout_kind == "before_after" else "a",
                    "color": palette["a"],
                    "bullets": prepare_bullets(points_a, limit=4),
                },
                {
                    "name": name_b,
                    "kind": "good" if layout_kind == "before_after" else "b",
                    "color": palette["b"],
                    "bullets": prepare_bullets(points_b, limit=4),
                },
            ]
        elif layout_kind == "grid":
            labels = ["APPROACH", "TRADE-OFF", "BEST FOR", "RECOMMENDATION"]
            pool = prepare_bullets((points_a + points_b), limit=4, max_words=8)
            while len(pool) < 4:
                pool.append("\u2014")
            ctx["cells"] = [
                {"label": lbl, "text": txt, "accent": idx == 0}
                for idx, (lbl, txt) in enumerate(zip(labels, pool))
            ]
        else:  # list / checklist / tutorial
            pool = prepare_bullets((points_a + points_b), limit=6, max_words=8)
            ctx["items"] = [{"num": i + 1, "text": txt} for i, txt in enumerate(pool)]

        if post.winner:
            name, reason = parse_winner(post.winner, post.option_a, post.option_b)
            if name:
                ctx["winner"] = {
                    "label": WINNER_LABEL_BY_LAYOUT.get(layout_kind, "WINNER"),
                    "name": name,
                    "reason": reason,
                }

        return ctx

    # ── Step 2: HTML Renderer (Jinja2) ─────────────────────────────────────────

    def _build_html(self, post, svg_chart: str) -> Optional[str]:
        """Render the unified card.html template with shaped content."""
        if not self._jinja:
            return None

        theme_val = post.theme.value if hasattr(post.theme, "value") else str(post.theme)

        try:
            tmpl = self._jinja.get_template("card.html")
        except Exception as exc:
            logger.error("card.html template not found (%s)", exc)
            return None

        ctx = self._build_context(post, svg_chart, theme_val)

        try:
            return tmpl.render(**ctx)
        except Exception as exc:
            logger.error("Jinja2 render error: %s", exc)
            return None

    # ── Step 3a: Playwright Screenshot ─────────────────────────────────────────

    def _screenshot_playwright(self, html: str) -> Optional[bytes]:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                browser = pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
                page = browser.new_page(
                    viewport={"width": CARD_W, "height": CARD_H},
                    device_scale_factor=DEVICE_SCALE_FACTOR,
                )
                page.set_content(html, wait_until="networkidle")
                # Guarantee the embedded webfont is fully loaded and laid out
                # before any autofit measurement or screenshot happens.
                try:
                    page.evaluate("document.fonts.ready.then(() => true)")
                except Exception:
                    pass
                page.wait_for_load_state("networkidle")
                # Responsive text-fitting safety net — shrinks any
                # [data-autofit] element that still overflows its box.
                try:
                    page.evaluate(AUTOFIT_JS)
                except Exception as exc:
                    logger.debug("Autofit script failed (non-fatal): %s", exc)
                png = page.screenshot(
                    type="png",
                    clip={"x": 0, "y": 0, "width": CARD_W, "height": CARD_H},
                )
                browser.close()
            logger.info(
                "Playwright rendered 1080x1080 card at %sx scale (%d bytes)",
                DEVICE_SCALE_FACTOR, len(png),
            )
            return png
        except Exception as exc:
            logger.warning("Playwright screenshot failed: %s", exc)
            return None

    # ── Step 3b: HCTI Fallback ──────────────────────────────────────────────────

    def _screenshot_hcti(self, html: str) -> Optional[bytes]:
        if not (self._hcti_user and self._hcti_key):
            return None
        try:
            from utils.http_client import HttpClient as _HC
            _hcti_http = _HC("hcti_renderer", default_timeout=30)
            resp = _hcti_http.post(
                "https://hcti.io/v1/image",
                data={
                    "html": html,
                    "viewport_width": CARD_W,
                    "viewport_height": CARD_H,
                    "device_scale": DEVICE_SCALE_FACTOR,
                },
                auth=(self._hcti_user, self._hcti_key),
            )
            if resp.status_code != 200:
                logger.warning("HCTI returned HTTP %s", resp.status_code)
                return None
            url = resp.json().get("url", "")
            if not url:
                return None
            img = _hcti_http.get(url, timeout=20)
            if img.status_code == 200:
                logger.info("HCTI fallback rendered card (%d bytes)", len(img.content))
                return img.content
        except Exception as exc:
            logger.warning("HCTI fallback failed: %s", exc)
        return None

    # ── Public API ──────────────────────────────────────────────────────────────

    def render(self, post) -> Optional[bytes]:
        """
        Full pipeline:
          TechPost
            ├── SVG Renderer  → decorative background SVG (no text)
            └── HTML Renderer → Jinja2 template (real HTML/CSS text)
                     └── Playwright / HCTI → PNG 1080x1080

        Returns PNG bytes, or None if all backends fail.
        """
        svg   = self._build_svg(post)
        html  = self._build_html(post, svg)
        if not html:
            logger.debug("HTML render produced nothing — skipping screenshot")
            return None

        if self._check_playwright():
            png = self._screenshot_playwright(html)
            if png:
                return png

        png = self._screenshot_hcti(html)
        if png:
            return png

        logger.debug("All PNG backends failed (Playwright + HCTI)")
        return None

    def render_html_only(self, post) -> Optional[str]:
        """Return the rendered HTML string — useful for debugging."""
        svg = self._build_svg(post)
        return self._build_html(post, svg)


card_renderer = CardRenderer()
