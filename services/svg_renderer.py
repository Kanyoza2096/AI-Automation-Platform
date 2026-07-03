"""
SVG Renderer v11.0 — Decorative-only SVG generation.

Per the v11 rendering-quality pass, text is never rasterized inside SVG
any more (blurry, non-selectable, hard to autofit). All titles, bullets,
labels, and the winner banner are now plain HTML/CSS, rendered by
Playwright's text engine and covered by the in-browser autofit pass.

This module's sole remaining job is to generate the subtle decorative
background — soft gradient glows and a faint dot grid — that gives each
card its premium, theme-tinted backdrop. No text, ever.
"""

import logging

from models.content import TechPost, PostTheme
from services.rendering.layout import CARD_W, CARD_H, palette_for

logger = logging.getLogger("kanyoza.services.svg_renderer")

SVG_W = CARD_W
SVG_H = CARD_H


class SvgRenderer:
    """Generates a decorative (text-free) background SVG for a TechPost's card."""

    # ── Public API ─────────────────────────────────────────────────────────────

    def render(self, post: TechPost) -> str:
        """Generate the theme-tinted decorative background SVG."""
        try:
            theme_val = post.theme.value if hasattr(post.theme, "value") else str(post.theme)
            palette = palette_for(theme_val)
            return self._decoration_svg(palette["a"], palette["b"])
        except Exception as exc:
            logger.warning("Decorative SVG render failed (%s) — using empty backdrop", exc)
            return self._decoration_svg("#6366F1", "#0EA5E9")

    def render_html_fragment(self, post: TechPost) -> str:
        """Return the decorative SVG wrapped in a positioned container div."""
        svg = self.render(post)
        return f'<div class="bg-decoration">{svg}</div>'

    # ── Decorative background ────────────────────────────────────────────────

    def _decoration_svg(self, color_a: str, color_b: str) -> str:
        """
        Two soft radial glows (top-left tinted `color_a`, bottom-right tinted
        `color_b`) plus a faint dot grid — purely decorative, sits behind the
        card content via CSS z-index.
        """
        return f"""<svg width="{SVG_W}" height="{SVG_H}" viewBox="0 0 {SVG_W} {SVG_H}"
     xmlns="http://www.w3.org/2000/svg">
  <defs>
    <radialGradient id="glowA" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="{color_a}" stop-opacity="0.35"/>
      <stop offset="100%" stop-color="{color_a}" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="glowB" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="{color_b}" stop-opacity="0.30"/>
      <stop offset="100%" stop-color="{color_b}" stop-opacity="0"/>
    </radialGradient>
    <pattern id="dots" width="28" height="28" patternUnits="userSpaceOnUse">
      <circle cx="1.5" cy="1.5" r="1.5" fill="#FFFFFF" fill-opacity="0.035"/>
    </pattern>
  </defs>
  <rect width="{SVG_W}" height="{SVG_H}" fill="url(#dots)"/>
  <circle cx="140" cy="120" r="360" fill="url(#glowA)"/>
  <circle cx="{SVG_W - 120}" cy="{SVG_H - 160}" r="380" fill="url(#glowB)"/>
</svg>"""


svg_renderer = SvgRenderer()
