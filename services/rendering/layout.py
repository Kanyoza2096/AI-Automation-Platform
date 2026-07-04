"""
Design tokens — spacing, type scale, semantic palettes, and autofit JS.

Semantic colour system (v12):
  database  → emerald green      backend   → cyan
  ai        → violet/purple      frontend  → orange
  cloud     → blue               devops    → teal
  security  → red                networking → slate
  architecture → indigo          code_tips → amber
  comparison → indigo+sky        system_design → violet

hex_to_rgba() lets the template compose CSS rgba() strings from palette
hex values without any extra library — used for glow box-shadows.
"""

from __future__ import annotations

CARD_W = 1080
CARD_H = 1080


# ── Spacing (8-px grid) ─────────────────────────────────────────────────────
SPACE = {
    "xs":   8,
    "sm":  16,
    "md":  24,
    "lg":  32,
    "xl":  48,
    "xxl": 64,
}


# ── Type scale ───────────────────────────────────────────────────────────────
FONT_SIZE = {
    "title_max":   56,
    "title_min":   34,
    "heading_max": 30,
    "heading_min": 18,
    "bullet_max":  24,
    "bullet_min":  15,
    "winner_max":  36,
    "winner_min":  22,
    "reason":      17,
    "badge":       13,
    "footer":      16,
}

BULLET_MAX_LINES = 2
TITLE_MAX_LINES  = 2


# ── Semantic palettes ────────────────────────────────────────────────────────
# Keys used in templates:
#   a, b        — primary / secondary accent (column colours, bullet dots)
#   accent      — lighter highlight for icons / taglines
#   badge_bg    — badge pill background
#   badge_text  — badge pill text
#   tagline     — footer tagline / cell label colour
#   glow_a/b    — hex colours fed to the SVG radial-gradient glows
THEME_PALETTES: dict[str, dict] = {
    "database": {
        "a": "#10B981", "b": "#059669",
        "accent": "#34D399",
        "badge_bg": "#064E3B", "badge_text": "#6EE7B7",
        "tagline": "#34D399",
        "glow_a": "#10B981", "glow_b": "#34D399",
    },
    "ai": {
        "a": "#8B5CF6", "b": "#7C3AED",
        "accent": "#A78BFA",
        "badge_bg": "#2E1065", "badge_text": "#C4B5FD",
        "tagline": "#A78BFA",
        "glow_a": "#7C3AED", "glow_b": "#6D28D9",
    },
    "cloud": {
        "a": "#3B82F6", "b": "#2563EB",
        "accent": "#60A5FA",
        "badge_bg": "#1E3A8A", "badge_text": "#93C5FD",
        "tagline": "#60A5FA",
        "glow_a": "#3B82F6", "glow_b": "#0EA5E9",
    },
    "security": {
        "a": "#EF4444", "b": "#DC2626",
        "accent": "#FCA5A5",
        "badge_bg": "#7F1D1D", "badge_text": "#FCA5A5",
        "tagline": "#F87171",
        "glow_a": "#EF4444", "glow_b": "#DC2626",
    },
    "architecture": {
        "a": "#6366F1", "b": "#4F46E5",
        "accent": "#818CF8",
        "badge_bg": "#1E1B4B", "badge_text": "#A5B4FC",
        "tagline": "#818CF8",
        "glow_a": "#6366F1", "glow_b": "#4F46E5",
    },
    "backend": {
        "a": "#06B6D4", "b": "#0891B2",
        "accent": "#67E8F9",
        "badge_bg": "#164E63", "badge_text": "#67E8F9",
        "tagline": "#22D3EE",
        "glow_a": "#06B6D4", "glow_b": "#0EA5E9",
    },
    "frontend": {
        "a": "#F97316", "b": "#EA580C",
        "accent": "#FDBA74",
        "badge_bg": "#431407", "badge_text": "#FDBA74",
        "tagline": "#FB923C",
        "glow_a": "#F97316", "glow_b": "#EF4444",
    },
    "comparison": {
        "a": "#6366F1", "b": "#0EA5E9",
        "accent": "#818CF8",
        "badge_bg": "#1E1B4B", "badge_text": "#A5B4FC",
        "tagline": "#818CF8",
        "glow_a": "#6366F1", "glow_b": "#0EA5E9",
    },
    "code_tips": {
        "a": "#F59E0B", "b": "#D97706",
        "accent": "#FDE68A",
        "badge_bg": "#451A03", "badge_text": "#FDE68A",
        "tagline": "#FBBF24",
        "glow_a": "#F59E0B", "glow_b": "#EF4444",
    },
    "system_design": {
        "a": "#6366F1", "b": "#7C3AED",
        "accent": "#A78BFA",
        "badge_bg": "#1E1B4B", "badge_text": "#C4B5FD",
        "tagline": "#A78BFA",
        "glow_a": "#6366F1", "glow_b": "#7C3AED",
    },
    "tutorial": {
        "a": "#10B981", "b": "#059669",
        "accent": "#34D399",
        "badge_bg": "#022C22", "badge_text": "#6EE7B7",
        "tagline": "#34D399",
        "glow_a": "#10B981", "glow_b": "#0EA5E9",
    },
    "checklist": {
        "a": "#10B981", "b": "#059669",
        "accent": "#34D399",
        "badge_bg": "#022C22", "badge_text": "#6EE7B7",
        "tagline": "#34D399",
        "glow_a": "#10B981", "glow_b": "#059669",
    },
}
DEFAULT_PALETTE = THEME_PALETTES["comparison"]


def palette_for(theme_value: str) -> dict:
    """Return the palette for *theme_value*, falling back to the default."""
    return THEME_PALETTES.get(theme_value, DEFAULT_PALETTE)


def hex_to_rgba(hex_color: str, alpha: float) -> str:
    """Convert a 6-digit hex colour + alpha float → CSS rgba() string."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return f"rgba(99,102,241,{alpha})"
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


# ── Surface tokens ───────────────────────────────────────────────────────────
SURFACE = {
    "bg":           "#07090F",
    "card":         "#111827",
    "card_border":  "rgba(255,255,255,0.07)",
    "divider":      "rgba(255,255,255,0.08)",
    "text_primary": "#F8FAFC",
    "text_secondary": "#CBD5E1",
    "text_muted":   "#64748B",
}


# ── In-browser autofit (safety net) ─────────────────────────────────────────
# Shrinks any [data-autofit] element in 1-px steps until it no longer
# overflows its container, down to data-autofit-min (default 15px).
AUTOFIT_JS = """
(() => {
  document.querySelectorAll('[data-autofit]').forEach(el => {
    const min = parseFloat(el.dataset.autofitMin || '15');
    let size = parseFloat(getComputedStyle(el).fontSize);
    let guard = 0;
    const fits = () =>
      el.scrollHeight <= el.clientHeight + 2 &&
      el.scrollWidth  <= el.clientWidth  + 2;
    while (!fits() && size > min && guard < 80) {
      size -= 0.5;
      el.style.fontSize = size + 'px';
      guard++;
    }
  });
})();
"""
