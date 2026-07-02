"""
Card Renderer v10.0 — Jinja2 → PNG pipeline
Renders beautiful themed HTML cards from TechPost model data.

Rendering priority:
  1. Playwright (headless Chromium) — best quality, requires `playwright install`
  2. HCTI API (htmlcsstoimage.com) — requires HCTI_USER_ID / HCTI_API_KEY
  3. None — caller falls back to image_engine

The Jinja2 templates live in templates/cards/ and work with any renderer.
"""

import io
import logging
import os
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

logger = logging.getLogger("kanyoza.services.card_renderer")

TEMPLATES_DIR = Path(__file__).parent.parent / "templates" / "cards"

THEME_TEMPLATE_MAP: dict = {
    "comparison":      "comparison.html",
    "architecture":    "architecture.html",
    "system_design":   "architecture.html",
    "code_tips":       "code_tips.html",
    "backend":         "code_tips.html",
    "frontend":        "code_tips.html",
    "security":        "code_tips.html",
    "database":        "comparison.html",
    "cloud":           "architecture.html",
    "tutorial":        "tutorial.html",
    "ai":              "comparison.html",
}

CARD_WIDTH = 1200
CARD_HEIGHT = 630


class CardRenderer:
    """
    Renders a TechPost into a PNG image using the best available backend.
    Falls back gracefully — never raises, always returns None on failure.
    """

    def __init__(self):
        self._jinja_env: Optional[Environment] = None
        self._playwright_available: Optional[bool] = None
        self._hcti_user: Optional[str] = None
        self._hcti_key: Optional[str] = None
        self._init_jinja()
        self._init_hcti()

    def _init_jinja(self) -> None:
        if not TEMPLATES_DIR.exists():
            logger.warning("templates/cards/ not found — card renderer disabled")
            return
        self._jinja_env = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            autoescape=select_autoescape(["html"]),
        )
        logger.info("Jinja2 card environment ready (templates: %s)", TEMPLATES_DIR)

    def _init_hcti(self) -> None:
        self._hcti_user = os.environ.get("HCTI_USER_ID", "")
        self._hcti_key = os.environ.get("HCTI_API_KEY", "")

    def _check_playwright(self) -> bool:
        if self._playwright_available is not None:
            return self._playwright_available
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
            self._playwright_available = True
        except ImportError:
            self._playwright_available = False
        return self._playwright_available

    # ── Template rendering ─────────────────────────────────────────────────────

    def _render_html(self, post) -> Optional[str]:
        """Render the Jinja2 template for the given TechPost."""
        if not self._jinja_env:
            return None

        theme_val = post.theme.value if hasattr(post.theme, "value") else str(post.theme)
        template_name = THEME_TEMPLATE_MAP.get(theme_val, "comparison.html")

        try:
            template = self._jinja_env.get_template(template_name)
        except Exception as exc:
            logger.warning("Template '%s' not found: %s", template_name, exc)
            try:
                template = self._jinja_env.get_template("comparison.html")
            except Exception:
                return None

        ctx = {
            "title":     post.title or "",
            "caption":   post.caption or "",
            "option_a":  post.option_a or "",
            "option_b":  post.option_b or "",
            "winner":    post.winner or "",
            "points_a":  post.points_a or [],
            "points_b":  post.points_b or [],
            "hashtags":  post.hashtags or [],
            "theme_label": theme_val.replace("_", " ").title(),
        }

        try:
            return template.render(**ctx)
        except Exception as exc:
            logger.error("Jinja2 render error: %s", exc)
            return None

    # ── PNG backends ───────────────────────────────────────────────────────────

    def _render_via_playwright(self, html: str) -> Optional[bytes]:
        """Render HTML to PNG bytes using Playwright (headless Chromium)."""
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(args=["--no-sandbox"])
                page = browser.new_page(viewport={"width": CARD_WIDTH, "height": CARD_HEIGHT})
                page.set_content(html, wait_until="networkidle")
                png = page.screenshot(type="png", clip={
                    "x": 0, "y": 0, "width": CARD_WIDTH, "height": CARD_HEIGHT
                })
                browser.close()
                logger.info("Playwright rendered card (%d bytes)", len(png))
                return png
        except Exception as exc:
            logger.warning("Playwright render failed: %s", exc)
            return None

    def _render_via_hcti(self, html: str) -> Optional[bytes]:
        """Render HTML to PNG using HtmlCssToImage API."""
        if not (self._hcti_user and self._hcti_key):
            return None
        try:
            import requests
            resp = requests.post(
                "https://hcti.io/v1/image",
                data={"html": html},
                auth=(self._hcti_user, self._hcti_key),
                timeout=30,
            )
            if resp.status_code != 200:
                logger.warning("HCTI returned %s", resp.status_code)
                return None
            image_url = resp.json().get("url", "")
            if not image_url:
                return None
            img_resp = requests.get(image_url, timeout=20)
            if img_resp.status_code == 200:
                logger.info("HCTI rendered card (%d bytes)", len(img_resp.content))
                return img_resp.content
        except Exception as exc:
            logger.warning("HCTI render failed: %s", exc)
        return None

    # ── Public API ─────────────────────────────────────────────────────────────

    def render(self, post) -> Optional[bytes]:
        """
        Render a TechPost to PNG bytes.
        Returns None if all backends fail (caller should fall back to image_engine).
        """
        html = self._render_html(post)
        if not html:
            logger.debug("No HTML rendered — skipping card render")
            return None

        if self._check_playwright():
            result = self._render_via_playwright(html)
            if result:
                return result

        result = self._render_via_hcti(html)
        if result:
            return result

        logger.debug("No PNG renderer available (Playwright not installed, HCTI not configured)")
        return None

    def render_html_only(self, post) -> Optional[str]:
        """Return rendered HTML string — useful for debugging or future use."""
        return self._render_html(post)


card_renderer = CardRenderer()
