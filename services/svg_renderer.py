"""
SVG Renderer v10.0 — Generates inline SVG charts from TechPost data.

The SVG is embedded directly into the Jinja2 HTML card template, which
Playwright then screenshots to produce the final 1080x1080 PNG.

No external dependencies — pure Python string generation.
Theme dispatch:
  - is_debate (any theme)         → two-column VS comparison
  - code_tips                     → before / after split
  - architecture, system_design,
    cloud                         → 2x2 architectural grid
  - tutorial / default            → numbered step list
"""

import html as _html
import logging
from typing import List

from models.content import TechPost, PostTheme

logger = logging.getLogger("kanyoza.services.svg_renderer")

SVG_W = 916
SVG_H = 660


class SvgRenderer:
    """Generates an inline SVG string from a TechPost."""

    _ARCH_THEMES = {PostTheme.ARCHITECTURE, PostTheme.SYSTEM_DESIGN, PostTheme.CLOUD}
    _TIPS_THEMES = {PostTheme.CODE_TIPS, PostTheme.BACKEND, PostTheme.FRONTEND}

    def render(self, post: TechPost) -> str:
        try:
            if post.is_debate:
                return self._comparison_svg(post)
            if post.theme in self._TIPS_THEMES:
                return self._codequality_svg(post)
            if post.theme in self._ARCH_THEMES:
                return self._architecture_svg(post)
            return self._list_svg(post)
        except Exception as exc:
            logger.warning("SVG render failed (%s) — using fallback", exc)
            return self._fallback_svg(post)

    def render_html_fragment(self, post: TechPost) -> str:
        """Return SVG wrapped in a container div for direct HTML embedding."""
        svg = self.render(post)
        return f'<div class="svg-chart">{svg}</div>'

    # ── Private helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _esc(text: str) -> str:
        return _html.escape(str(text))

    @staticmethod
    def _trunc(text: str, n: int) -> str:
        s = str(text).strip()
        return s if len(s) <= n else s[: n - 1] + "\u2026"

    def _open(self) -> str:
        return (
            f'<svg width="{SVG_W}" height="{SVG_H}" '
            f'xmlns="http://www.w3.org/2000/svg" '
            f'font-family="Segoe UI, system-ui, -apple-system, sans-serif">'
        )

    def _gradient(self, gid: str, c1: str, c2: str) -> str:
        return (
            f'<defs><linearGradient id="{gid}" x1="0" y1="0" x2="1" y2="0" '
            f'gradientUnits="objectBoundingBox">'
            f'<stop offset="0%" stop-color="{c1}"/>'
            f'<stop offset="100%" stop-color="{c2}"/>'
            f"</linearGradient></defs>"
        )

    # ── Two-column VS comparison ────────────────────────────────────────────────

    def _comparison_svg(self, post: TechPost) -> str:
        COL_A = "#4F46E5"
        COL_B = "#0EA5E9"
        BG    = "#0F172A"
        TEXT  = "#CBD5E1"
        DIV   = "#334155"

        col_w  = 415
        gap    = 86
        col_bx = col_w + gap      # x-start of column B  = 501
        box_h  = 568
        pt_y0  = 88
        pt_dy  = 90

        p: List[str] = []
        p.append(self._open())
        p.append(self._gradient("wg", "#4F46E5", "#7C3AED"))

        # ── Column A ──────────────────────────────────────────────────────────
        p.append(f'<rect x="0" y="0" width="{col_w}" height="{box_h}" rx="16" fill="{BG}" stroke="{COL_A}" stroke-width="2"/>')
        p.append(f'<rect x="0" y="0" width="{col_w}" height="60" rx="16" fill="{COL_A}"/>')
        p.append(f'<rect x="0" y="44" width="{col_w}" height="16" fill="{COL_A}"/>')
        name_a = self._esc(self._trunc(post.option_a or "Option A", 22))
        p.append(f'<text x="{col_w // 2}" y="38" text-anchor="middle" dominant-baseline="middle" fill="white" font-size="22" font-weight="700">{name_a}</text>')
        for i, pt in enumerate(post.points_a[:5]):
            py = pt_y0 + i * pt_dy
            p.append(f'<circle cx="22" cy="{py}" r="6" fill="{COL_A}" opacity="0.8"/>')
            p.append(f'<text x="38" y="{py + 6}" fill="{TEXT}" font-size="16">{self._esc(self._trunc(pt, 36))}</text>')

        # ── VS divider ────────────────────────────────────────────────────────
        vx = col_w + gap // 2          # center of gap = 458
        p.append(f'<line x1="{col_w + 10}" y1="32" x2="{col_w + 10}" y2="{box_h - 32}" stroke="{DIV}" stroke-width="1" stroke-dasharray="4 4"/>')
        p.append(f'<line x1="{col_bx - 10}" y1="32" x2="{col_bx - 10}" y2="{box_h - 32}" stroke="{DIV}" stroke-width="1" stroke-dasharray="4 4"/>')
        p.append(f'<rect x="{vx - 28}" y="{box_h // 2 - 26}" width="56" height="52" rx="10" fill="#1E293B" stroke="{DIV}" stroke-width="1"/>')
        p.append(f'<text x="{vx}" y="{box_h // 2 + 10}" text-anchor="middle" fill="#64748B" font-size="20" font-weight="900">VS</text>')

        # ── Column B ──────────────────────────────────────────────────────────
        p.append(f'<rect x="{col_bx}" y="0" width="{col_w}" height="{box_h}" rx="16" fill="{BG}" stroke="{COL_B}" stroke-width="2"/>')
        p.append(f'<rect x="{col_bx}" y="0" width="{col_w}" height="60" rx="16" fill="{COL_B}"/>')
        p.append(f'<rect x="{col_bx}" y="44" width="{col_w}" height="16" fill="{COL_B}"/>')
        name_b = self._esc(self._trunc(post.option_b or "Option B", 22))
        p.append(f'<text x="{col_bx + col_w // 2}" y="38" text-anchor="middle" dominant-baseline="middle" fill="white" font-size="22" font-weight="700">{name_b}</text>')
        for i, pt in enumerate(post.points_b[:5]):
            py = pt_y0 + i * pt_dy
            bx0 = col_bx + 22
            p.append(f'<circle cx="{bx0}" cy="{py}" r="6" fill="{COL_B}" opacity="0.8"/>')
            p.append(f'<text x="{bx0 + 16}" y="{py + 6}" fill="{TEXT}" font-size="16">{self._esc(self._trunc(pt, 36))}</text>')

        # ── Winner banner ─────────────────────────────────────────────────────
        by = box_h + 18
        if post.winner:
            wt = self._esc(self._trunc(post.winner, 58))
            p.append(f'<rect x="0" y="{by}" width="{SVG_W}" height="56" rx="12" fill="url(#wg)"/>')
            p.append(f'<text x="{SVG_W // 2}" y="{by + 34}" text-anchor="middle" dominant-baseline="middle" fill="white" font-size="19" font-weight="700">\U0001f3c6 Winner: {wt}</text>')

        p.append("</svg>")
        return "\n".join(p)

    # ── Before / After (code quality) ──────────────────────────────────────────

    def _codequality_svg(self, post: TechPost) -> str:
        BAD  = "#EF4444"
        GOOD = "#10B981"
        BG   = "#0F172A"
        TEXT = "#CBD5E1"

        col_w  = 440
        gap    = 36
        col_bx = col_w + gap
        box_h  = 568
        pt_y0  = 88
        pt_dy  = 84

        p: List[str] = []
        p.append(self._open())

        # Bad column
        p.append(f'<rect x="0" y="0" width="{col_w}" height="{box_h}" rx="16" fill="{BG}" stroke="{BAD}" stroke-width="2"/>')
        p.append(f'<rect x="0" y="0" width="{col_w}" height="60" rx="16" fill="{BAD}"/>')
        p.append(f'<rect x="0" y="44" width="{col_w}" height="16" fill="{BAD}"/>')
        bad_label = self._esc(self._trunc(post.option_a or "The Wrong Way", 24))
        p.append(f'<text x="{col_w // 2}" y="38" text-anchor="middle" dominant-baseline="middle" fill="white" font-size="20" font-weight="700">\u274c {bad_label}</text>')
        for i, pt in enumerate(post.points_a[:5]):
            py = pt_y0 + i * pt_dy
            p.append(f'<text x="18" y="{py + 6}" fill="#F87171" font-size="16">\u2717</text>')
            p.append(f'<text x="36" y="{py + 6}" fill="{TEXT}" font-size="15">{self._esc(self._trunc(pt, 38))}</text>')

        # Divider
        dx = col_w + gap // 2
        p.append(f'<line x1="{dx}" y1="16" x2="{dx}" y2="{box_h - 16}" stroke="#334155" stroke-width="2"/>')

        # Good column
        p.append(f'<rect x="{col_bx}" y="0" width="{col_w}" height="{box_h}" rx="16" fill="{BG}" stroke="{GOOD}" stroke-width="2"/>')
        p.append(f'<rect x="{col_bx}" y="0" width="{col_w}" height="60" rx="16" fill="{GOOD}"/>')
        p.append(f'<rect x="{col_bx}" y="44" width="{col_w}" height="16" fill="{GOOD}"/>')
        good_label = self._esc(self._trunc(post.option_b or "The Right Way", 24))
        p.append(f'<text x="{col_bx + col_w // 2}" y="38" text-anchor="middle" dominant-baseline="middle" fill="white" font-size="20" font-weight="700">\u2705 {good_label}</text>')
        for i, pt in enumerate(post.points_b[:5]):
            py = pt_y0 + i * pt_dy
            bx0 = col_bx + 18
            p.append(f'<text x="{bx0}" y="{py + 6}" fill="#34D399" font-size="16">\u2713</text>')
            p.append(f'<text x="{bx0 + 18}" y="{py + 6}" fill="{TEXT}" font-size="15">{self._esc(self._trunc(pt, 38))}</text>')

        # Bottom tip
        if post.winner:
            by = box_h + 18
            wt = self._esc(self._trunc(post.winner, 72))
            p.append(f'<rect x="0" y="{by}" width="{SVG_W}" height="56" rx="12" fill="rgba(245,158,11,0.15)" stroke="rgba(245,158,11,0.4)" stroke-width="1"/>')
            p.append(f'<text x="20" y="{by + 34}" dominant-baseline="middle" fill="#FCD34D" font-size="17" font-weight="600">\u26a1 Bottom line: {wt}</text>')

        p.append("</svg>")
        return "\n".join(p)

    # ── Architecture grid ───────────────────────────────────────────────────────

    def _architecture_svg(self, post: TechPost) -> str:
        BG    = "#0F172A"
        TEXT  = "#E2E8F0"
        SUB   = "#94A3B8"
        TITLE = "#818CF8"

        labels = ["Approach", "Trade-off", "Best For", "Recommendation"]
        points = (post.points_a + post.points_b)[:4]
        while len(points) < 4:
            points.append("—")

        cell_w = 440
        cell_h = 260
        gap    = 36

        p: List[str] = []
        p.append(self._open())
        p.append(self._gradient("ag", "#0EA5E9", "#6366F1"))

        for idx, (lbl, pt) in enumerate(zip(labels, points)):
            col = idx % 2
            row = idx // 2
            cx  = col * (cell_w + gap)
            cy  = row * (cell_h + gap)
            border = "#4F46E5" if idx == 0 else "#334155"
            bw     = "2" if idx == 0 else "1"
            p.append(f'<rect x="{cx}" y="{cy}" width="{cell_w}" height="{cell_h}" rx="14" fill="{BG}" stroke="{border}" stroke-width="{bw}"/>')
            p.append(f'<text x="{cx + 22}" y="{cy + 34}" fill="{TITLE if idx == 0 else SUB}" font-size="12" font-weight="700" letter-spacing="1">{self._esc(lbl.upper())}</text>')
            line1, line2 = self._wrap(pt, 42)
            p.append(f'<text x="{cx + 22}" y="{cy + 70}" fill="{TEXT}" font-size="17" font-weight="600">{self._esc(line1)}</text>')
            if line2:
                p.append(f'<text x="{cx + 22}" y="{cy + 96}" fill="{TEXT}" font-size="17">{self._esc(line2)}</text>')

        if post.winner:
            by = 2 * cell_h + gap + 18
            wt = self._esc(self._trunc(post.winner, 62))
            p.append(f'<rect x="0" y="{by}" width="{SVG_W}" height="52" rx="12" fill="url(#ag)"/>')
            p.append(f'<text x="{SVG_W // 2}" y="{by + 32}" text-anchor="middle" dominant-baseline="middle" fill="white" font-size="18" font-weight="700">\U0001f3d7\ufe0f Recommendation: {wt}</text>')

        p.append("</svg>")
        return "\n".join(p)

    # ── Numbered list ───────────────────────────────────────────────────────────

    def _list_svg(self, post: TechPost) -> str:
        ACCENT = "#10B981"
        BG     = "#0F172A"
        TEXT   = "#CBD5E1"

        points = (post.points_a + post.points_b)[:6]
        item_h = 84
        total_h = len(points) * item_h

        p: List[str] = []
        p.append(self._open())

        for i, pt in enumerate(points):
            iy = i * item_h
            p.append(f'<rect x="0" y="{iy}" width="{SVG_W}" height="{item_h - 8}" rx="12" fill="{BG}" stroke="#1E293B" stroke-width="1"/>')
            p.append(f'<rect x="0" y="{iy}" width="4" height="{item_h - 8}" rx="2" fill="{ACCENT}"/>')
            num_cx = 40
            num_cy = iy + (item_h - 8) // 2
            p.append(f'<circle cx="{num_cx}" cy="{num_cy}" r="18" fill="{ACCENT}"/>')
            p.append(f'<text x="{num_cx}" y="{num_cy + 7}" text-anchor="middle" fill="white" font-size="16" font-weight="700">{i + 1}</text>')
            line1, line2 = self._wrap(pt, 62)
            ty = num_cy - (10 if line2 else 0)
            p.append(f'<text x="74" y="{ty}" fill="{TEXT}" font-size="17">{self._esc(line1)}</text>')
            if line2:
                p.append(f'<text x="74" y="{ty + 24}" fill="{TEXT}" font-size="17" opacity="0.8">{self._esc(line2)}</text>')

        if post.winner:
            by = total_h + 16
            wt = self._esc(self._trunc(post.winner, 68))
            p.append(f'<rect x="0" y="{by}" width="{SVG_W}" height="52" rx="12" fill="rgba(16,185,129,0.12)" stroke="rgba(16,185,129,0.35)" stroke-width="1"/>')
            p.append(f'<text x="20" y="{by + 32}" dominant-baseline="middle" fill="#6EE7B7" font-size="17" font-weight="600">\U0001f4a1 Pro tip: {wt}</text>')

        p.append("</svg>")
        return "\n".join(p)

    # ── Fallback (minimal) ──────────────────────────────────────────────────────

    def _fallback_svg(self, post: TechPost) -> str:
        title = self._esc(self._trunc(post.title, 60))
        caption = self._esc(self._trunc(post.caption, 100))
        p = [self._open()]
        p.append(f'<rect x="0" y="0" width="{SVG_W}" height="{SVG_H}" rx="20" fill="#0F172A"/>')
        p.append(f'<text x="{SVG_W // 2}" y="280" text-anchor="middle" fill="#F1F5F9" font-size="28" font-weight="800">{title}</text>')
        p.append(f'<text x="{SVG_W // 2}" y="340" text-anchor="middle" fill="#94A3B8" font-size="18">{caption}</text>')
        p.append("</svg>")
        return "\n".join(p)

    # ── Text wrap helper ────────────────────────────────────────────────────────

    @staticmethod
    def _wrap(text: str, max_chars: int):
        """Split text into at most two lines at a word boundary."""
        text = str(text).strip()
        if len(text) <= max_chars:
            return text, ""
        cut = text.rfind(" ", 0, max_chars)
        if cut == -1:
            cut = max_chars
        return text[:cut], text[cut:].strip()


svg_renderer = SvgRenderer()
