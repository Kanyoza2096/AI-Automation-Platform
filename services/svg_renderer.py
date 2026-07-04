"""
SVG Renderer v12 — theme-aware decorative background.

Generates a layered SVG background combining:
  1. A CSS-style dot-grid pattern (always present, very subtle)
  2. Two large radial colour glows derived from the theme palette
  3. A secondary soft vignette to ground the card edges

The SVG is text-free — all copy is handled by Playwright's HTML
renderer so it stays sharp, selectable, and autofittable.
"""

from __future__ import annotations

import logging

from models.content import TechPost
from services.rendering.layout import CARD_W, CARD_H, palette_for, hex_to_rgba

logger = logging.getLogger("kanyoza.services.svg_renderer")

_W = CARD_W
_H = CARD_H


class SvgRenderer:
    """Generates a decorative, text-free background SVG for a TechPost card."""

    def render(self, post: TechPost) -> str:
        """Return the full SVG background string for *post*."""
        try:
            theme_val = (
                post.theme.value if hasattr(post.theme, "value") else str(post.theme)
            )
            palette = palette_for(theme_val)
            return self._build(palette)
        except Exception as exc:
            logger.warning("SVG background render failed (%s) — using fallback", exc)
            return self._build({"glow_a": "#6366F1", "glow_b": "#0EA5E9"})

    # ── Private ──────────────────────────────────────────────────────────────

    def _build(self, palette: dict) -> str:
        ga = palette.get("glow_a", "#6366F1")
        gb = palette.get("glow_b", "#0EA5E9")

        # Convert to rgba for the glow stops (higher opacity than before)
        ga_mid  = hex_to_rgba(ga, 0.28)
        ga_edge = hex_to_rgba(ga, 0.0)
        gb_mid  = hex_to_rgba(gb, 0.22)
        gb_edge = hex_to_rgba(gb, 0.0)

        # Edge vignette — subtle dark ring around the outside
        vig_mid  = "rgba(0,0,0,0.0)"
        vig_edge = "rgba(0,0,0,0.45)"

        return f"""<svg width="{_W}" height="{_H}" viewBox="0 0 {_W} {_H}"
  xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">
  <defs>
    <!-- Subtle dot grid -->
    <pattern id="dots" width="28" height="28" patternUnits="userSpaceOnUse">
      <circle cx="1" cy="1" r="1.2" fill="#FFFFFF" fill-opacity="0.032"/>
    </pattern>

    <!-- Primary glow (top-left, theme primary colour) -->
    <radialGradient id="glowA" cx="20%" cy="18%" r="52%" gradientUnits="objectBoundingBox">
      <stop offset="0%"   stop-color="{ga_mid}"/>
      <stop offset="100%" stop-color="{ga_edge}"/>
    </radialGradient>

    <!-- Secondary glow (bottom-right, theme secondary colour) -->
    <radialGradient id="glowB" cx="82%" cy="84%" r="52%" gradientUnits="objectBoundingBox">
      <stop offset="0%"   stop-color="{gb_mid}"/>
      <stop offset="100%" stop-color="{gb_edge}"/>
    </radialGradient>

    <!-- Edge vignette -->
    <radialGradient id="vig" cx="50%" cy="50%" r="70%" gradientUnits="objectBoundingBox">
      <stop offset="30%"  stop-color="{vig_mid}"/>
      <stop offset="100%" stop-color="{vig_edge}"/>
    </radialGradient>
  </defs>

  <!-- Dot grid overlay -->
  <rect width="{_W}" height="{_H}" fill="url(#dots)"/>

  <!-- Colour glows -->
  <rect width="{_W}" height="{_H}" fill="url(#glowA)"/>
  <rect width="{_W}" height="{_H}" fill="url(#glowB)"/>

  <!-- Edge vignette (darkens the outer ring, draws focus inward) -->
  <rect width="{_W}" height="{_H}" fill="url(#vig)"/>
</svg>"""

    def render_html_fragment(self, post: TechPost) -> str:
        """Return the SVG wrapped in a positioned container div."""
        return f'<div class="bg-layer">{self.render(post)}</div>'


svg_renderer = SvgRenderer()
