"""
Card Renderer v10.0 — SVG + Jinja2 + Playwright pipeline.

Pipeline (per render call):
  1. SvgRenderer    — builds an inline SVG chart from TechPost data
  2. HtmlRenderer   — renders a Jinja2 template with the SVG embedded
  3. Playwright     — headless Chromium screenshots HTML → PNG 1080x1080
  4. HCTI fallback  — htmlcsstoimage.com if Playwright unavailable

The card is always 1080x1080 pixels (square — optimal for Facebook / Instagram).
"""

import logging
import os
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

logger = logging.getLogger("kanyoza.services.card_renderer")

TEMPLATES_DIR = Path(__file__).parent.parent / "templates" / "cards"

CARD_W = 1080
CARD_H = 1080

THEME_TEMPLATE_MAP: dict = {
    "comparison":    "comparison.html",
    "architecture":  "architecture.html",
    "system_design": "architecture.html",
    "code_tips":     "code_tips.html",
    "backend":       "code_tips.html",
    "frontend":      "code_tips.html",
    "security":      "comparison.html",
    "database":      "comparison.html",
    "cloud":         "architecture.html",
    "tutorial":      "tutorial.html",
    "ai":            "comparison.html",
}


class CardRenderer:
    """
    Renders a TechPost to a 1080x1080 PNG using:
      SVGRenderer → Jinja2 HTML template → Playwright screenshot.

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

    # ── Step 1: SVG Renderer ────────────────────────────────────────────────────

    def _build_svg(self, post) -> str:
        from services.svg_renderer import svg_renderer
        return svg_renderer.render(post)

    # ── Step 2: HTML Renderer (Jinja2) ─────────────────────────────────────────

    def _build_html(self, post, svg_chart: str) -> Optional[str]:
        """Render the Jinja2 template with the SVG chart embedded."""
        if not self._jinja:
            return None

        theme_val = post.theme.value if hasattr(post.theme, "value") else str(post.theme)
        template_name = THEME_TEMPLATE_MAP.get(theme_val, "comparison.html")

        try:
            tmpl = self._jinja.get_template(template_name)
        except Exception as exc:
            logger.warning("Template '%s' not found (%s) — trying comparison.html", template_name, exc)
            try:
                tmpl = self._jinja.get_template("comparison.html")
            except Exception:
                return None

        ctx = {
            "title":       post.title or "",
            "caption":     post.caption or "",
            "option_a":    post.option_a or "",
            "option_b":    post.option_b or "",
            "winner":      post.winner or "",
            "points_a":    post.points_a or [],
            "points_b":    post.points_b or [],
            "hashtags":    post.hashtags or [],
            "theme_label": theme_val.replace("_", " ").title(),
            "svg_chart":   svg_chart,
        }

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
                page = browser.new_page(viewport={"width": CARD_W, "height": CARD_H})
                page.set_content(html, wait_until="networkidle")
                png = page.screenshot(
                    type="png",
                    clip={"x": 0, "y": 0, "width": CARD_W, "height": CARD_H},
                )
                browser.close()
            logger.info("Playwright rendered 1080x1080 card (%d bytes)", len(png))
            return png
        except Exception as exc:
            logger.warning("Playwright screenshot failed: %s", exc)
            return None

    # ── Step 3b: HCTI Fallback ──────────────────────────────────────────────────

    def _screenshot_hcti(self, html: str) -> Optional[bytes]:
        if not (self._hcti_user and self._hcti_key):
            return None
        try:
            import requests
            resp = requests.post(
                "https://hcti.io/v1/image",
                data={"html": html, "viewport_width": CARD_W, "viewport_height": CARD_H},
                auth=(self._hcti_user, self._hcti_key),
                timeout=30,
            )
            if resp.status_code != 200:
                logger.warning("HCTI returned HTTP %s", resp.status_code)
                return None
            url = resp.json().get("url", "")
            if not url:
                return None
            img = requests.get(url, timeout=20)
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
            ├── SVG Renderer  → inline SVG chart
            └── HTML Renderer → Jinja2 template (with SVG embedded)
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
