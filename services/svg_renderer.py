"""
SVG Renderer v13 — theme-aware decorative background engine.

Generates a layered, text-free SVG background combining:
  1. A theme-selected subtle pattern (dot grid / hex / mesh gradient /
     node graph / topology lines) — chosen via palette["pattern"]
  2. Two large radial colour glows derived from the theme palette
  3. A secondary soft vignette to ground the card edges

Backgrounds are deliberately subtle (low opacity, decorative only) so they
never compete with foreground content — per the background-engine spec.
All copy is handled by Playwright's HTML renderer so it stays sharp,
selectable, and autofittable; SVG here never rasterizes text.
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
            return self._build({"glow_a": "#6366F1", "glow_b": "#0EA5E9", "pattern": "dots"})

    # ── Private ──────────────────────────────────────────────────────────────

    def _pattern_markup(self, pattern: str) -> str:
        """Return the <defs> pattern block + the <rect> that paints it."""
        builders = {
            "dots":     self._pattern_dots,
            "grid":     self._pattern_grid,
            "hex":      self._pattern_hex,
            "nodes":    self._pattern_nodes,
            "mesh":     self._pattern_mesh,
            "topology": self._pattern_topology,
        }
        return builders.get(pattern, self._pattern_dots)()

    @staticmethod
    def _pattern_dots() -> str:
        return (
            '<pattern id="pat" width="28" height="28" patternUnits="userSpaceOnUse">'
            '<circle cx="1" cy="1" r="1.2" fill="#FFFFFF" fill-opacity="0.032"/>'
            '</pattern>'
            f'<rect width="{_W}" height="{_H}" fill="url(#pat)"/>'
        )

    @staticmethod
    def _pattern_grid() -> str:
        return (
            '<pattern id="pat" width="54" height="54" patternUnits="userSpaceOnUse">'
            '<path d="M54 0H0V54" fill="none" stroke="#FFFFFF" stroke-opacity="0.035" stroke-width="1"/>'
            '</pattern>'
            f'<rect width="{_W}" height="{_H}" fill="url(#pat)"/>'
        )

    @staticmethod
    def _pattern_hex() -> str:
        # Flat-top hexagon tile, very low opacity outline only.
        return (
            '<pattern id="pat" width="52" height="90" patternUnits="userSpaceOnUse" '
            'patternTransform="scale(1)">'
            '<path d="M26 0L52 15V45L26 60L0 45V15Z" fill="none" '
            'stroke="#FFFFFF" stroke-opacity="0.035" stroke-width="1"/>'
            '<path d="M26 60L52 75V105L26 120L0 105V75Z" fill="none" '
            'stroke="#FFFFFF" stroke-opacity="0.035" stroke-width="1"/>'
            '</pattern>'
            f'<rect width="{_W}" height="{_H}" fill="url(#pat)"/>'
        )

    @staticmethod
    def _pattern_nodes() -> str:
        # Sparse scatter of small circles connected by faint lines —
        # database / network node graph motif.
        import random
        rnd = random.Random(42)
        pts = [(rnd.randint(60, _W - 60), rnd.randint(60, _H - 60)) for _ in range(14)]
        circles = "".join(
            f'<circle cx="{x}" cy="{y}" r="2.4" fill="#FFFFFF" fill-opacity="0.05"/>'
            for x, y in pts
        )
        lines = "".join(
            f'<line x1="{pts[i][0]}" y1="{pts[i][1]}" x2="{pts[i+1][0]}" y2="{pts[i+1][1]}" '
            'stroke="#FFFFFF" stroke-opacity="0.028" stroke-width="1"/>'
            for i in range(len(pts) - 1)
        )
        return f'<g>{lines}{circles}</g>'

    @staticmethod
    def _pattern_mesh() -> str:
        # Soft diagonal mesh lines — used for AI/ML themes (neural-net feel).
        lines = []
        step = 72
        for x in range(-_H, _W + _H, step):
            lines.append(
                f'<line x1="{x}" y1="0" x2="{x + _H}" y2="{_H}" '
                'stroke="#FFFFFF" stroke-opacity="0.022" stroke-width="1"/>'
            )
        return f'<g>{"".join(lines)}</g>'

    @staticmethod
    def _pattern_topology() -> str:
        # Gentle wavy topology lines — cloud/devops motif.
        paths = []
        for i, y in enumerate((220, 460, 700, 900)):
            amp = 26 + i * 6
            paths.append(
                f'<path d="M-40 {y} Q {_W*0.25} {y-amp} {_W*0.5} {y} '
                f'T {_W+40} {y}" fill="none" stroke="#FFFFFF" '
                f'stroke-opacity="0.028" stroke-width="1.2"/>'
            )
        return f'<g>{"".join(paths)}</g>'

    def _build(self, palette: dict) -> str:
        ga = palette.get("glow_a", "#6366F1")
        gb = palette.get("glow_b", "#0EA5E9")
        pattern = palette.get("pattern", "dots")

        # Convert to rgba for the glow stops
        ga_mid  = hex_to_rgba(ga, 0.26)
        ga_edge = hex_to_rgba(ga, 0.0)
        gb_mid  = hex_to_rgba(gb, 0.20)
        gb_edge = hex_to_rgba(gb, 0.0)

        vig_mid  = "rgba(0,0,0,0.0)"
        vig_edge = "rgba(0,0,0,0.45)"

        pattern_markup = self._pattern_markup(pattern)

        return f"""<svg width="{_W}" height="{_H}" viewBox="0 0 {_W} {_H}"
  xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">
  <defs>
    <radialGradient id="glowA" cx="20%" cy="18%" r="52%" gradientUnits="objectBoundingBox">
      <stop offset="0%"   stop-color="{ga_mid}"/>
      <stop offset="100%" stop-color="{ga_edge}"/>
    </radialGradient>

    <radialGradient id="glowB" cx="82%" cy="84%" r="52%" gradientUnits="objectBoundingBox">
      <stop offset="0%"   stop-color="{gb_mid}"/>
      <stop offset="100%" stop-color="{gb_edge}"/>
    </radialGradient>

    <radialGradient id="vig" cx="50%" cy="50%" r="70%" gradientUnits="objectBoundingBox">
      <stop offset="30%"  stop-color="{vig_mid}"/>
      <stop offset="100%" stop-color="{vig_edge}"/>
    </radialGradient>
  </defs>

  <!-- Theme-selected background pattern (subtle, decorative only) -->
  {pattern_markup}

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
