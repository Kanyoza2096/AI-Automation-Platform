"""
Design tokens — spacing, type scale, semantic palettes, background patterns,
and autofit JS. This is the single source of truth for the visual language
of every generated card.

Semantic colour system (v13):
  database    → emerald green      backend    → cyan
  ai          → violet/purple      frontend   → orange
  cloud       → blue               devops     → teal
  security    → red                networking → blue-grey (slate)
  architecture → indigo            code_tips  → amber
  comparison  → indigo+sky         system_design → violet
  tutorial/checklist → emerald

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


# ── Modular type scale ───────────────────────────────────────────────────────
# A fluid scale (ratio ≈ 1.19, "minor third" adjacent) instead of arbitrary
# fixed sizes. Each role has a max (ideal, plenty of room) and a min (the
# floor the autofit JS may shrink to before we consider rewriting content).
FONT_SIZE = {
    "title_max":   54,
    "title_min":   32,
    "heading_max": 28,
    "heading_min": 18,
    "bullet_max":  23,
    "bullet_min":  15,
    "winner_max":  40,
    "winner_min":  24,
    "reason_max":  19,
    "reason_min":  15,
    "badge":       12,
    "footer":      15,
    "label":       11,
}

BULLET_MAX_LINES = 2
TITLE_MAX_LINES  = 2

# Weight scale — used consistently instead of ad-hoc numbers.
WEIGHT = {
    "black":    800,
    "bold":     700,
    "semibold": 600,
    "medium":   500,
    "regular":  400,
}


def scale_for_density(item_count: int, base_max: int, base_min: int, step: int = 3) -> int:
    """
    Return a font size between base_min and base_max, stepping down as
    *item_count* (bullets, cells, etc.) grows — a lightweight density-aware
    sizing pass that runs BEFORE the browser ever sees the content, so the
    in-browser autofit JS only has to correct small residual overflow.
    """
    if item_count <= 2:
        return base_max
    reduction = (item_count - 2) * step
    return max(base_min, base_max - reduction)


# ── Semantic palettes ────────────────────────────────────────────────────────
# Keys used in templates:
#   a, b        — primary / secondary accent (column colours, bullet dots)
#   accent      — lighter highlight for icons / taglines
#   badge_bg    — badge pill background
#   badge_text  — badge pill text
#   tagline     — footer tagline / cell label colour
#   glow_a/b    — hex colours fed to the SVG radial-gradient glows
#   pattern     — background pattern id used by SvgRenderer (see svg_renderer.py)
THEME_PALETTES: dict[str, dict] = {
    "database": {
        "a": "#10B981", "b": "#059669",
        "accent": "#34D399",
        "badge_bg": "#064E3B", "badge_text": "#6EE7B7",
        "tagline": "#34D399",
        "glow_a": "#10B981", "glow_b": "#34D399",
        "pattern": "nodes",
    },
    "ai": {
        "a": "#8B5CF6", "b": "#7C3AED",
        "accent": "#A78BFA",
        "badge_bg": "#2E1065", "badge_text": "#C4B5FD",
        "tagline": "#A78BFA",
        "glow_a": "#7C3AED", "glow_b": "#6D28D9",
        "pattern": "mesh",
    },
    "cloud": {
        "a": "#3B82F6", "b": "#2563EB",
        "accent": "#60A5FA",
        "badge_bg": "#1E3A8A", "badge_text": "#93C5FD",
        "tagline": "#60A5FA",
        "glow_a": "#3B82F6", "glow_b": "#0EA5E9",
        "pattern": "topology",
    },
    "security": {
        "a": "#EF4444", "b": "#DC2626",
        "accent": "#FCA5A5",
        "badge_bg": "#7F1D1D", "badge_text": "#FCA5A5",
        "tagline": "#F87171",
        "glow_a": "#EF4444", "glow_b": "#DC2626",
        "pattern": "hex",
    },
    "architecture": {
        "a": "#6366F1", "b": "#4F46E5",
        "accent": "#818CF8",
        "badge_bg": "#1E1B4B", "badge_text": "#A5B4FC",
        "tagline": "#818CF8",
        "glow_a": "#6366F1", "glow_b": "#4F46E5",
        "pattern": "grid",
    },
    "backend": {
        "a": "#06B6D4", "b": "#0891B2",
        "accent": "#67E8F9",
        "badge_bg": "#164E63", "badge_text": "#67E8F9",
        "tagline": "#22D3EE",
        "glow_a": "#06B6D4", "glow_b": "#0EA5E9",
        "pattern": "nodes",
    },
    "frontend": {
        "a": "#F97316", "b": "#EA580C",
        "accent": "#FDBA74",
        "badge_bg": "#431407", "badge_text": "#FDBA74",
        "tagline": "#FB923C",
        "glow_a": "#F97316", "glow_b": "#EF4444",
        "pattern": "dots",
    },
    "comparison": {
        "a": "#6366F1", "b": "#0EA5E9",
        "accent": "#818CF8",
        "badge_bg": "#1E1B4B", "badge_text": "#A5B4FC",
        "tagline": "#818CF8",
        "glow_a": "#6366F1", "glow_b": "#0EA5E9",
        "pattern": "dots",
    },
    "code_tips": {
        "a": "#F59E0B", "b": "#D97706",
        "accent": "#FDE68A",
        "badge_bg": "#451A03", "badge_text": "#FDE68A",
        "tagline": "#FBBF24",
        "glow_a": "#F59E0B", "glow_b": "#EF4444",
        "pattern": "dots",
    },
    "system_design": {
        "a": "#6366F1", "b": "#7C3AED",
        "accent": "#A78BFA",
        "badge_bg": "#1E1B4B", "badge_text": "#C4B5FD",
        "tagline": "#A78BFA",
        "glow_a": "#6366F1", "glow_b": "#7C3AED",
        "pattern": "grid",
    },
    "tutorial": {
        "a": "#10B981", "b": "#059669",
        "accent": "#34D399",
        "badge_bg": "#022C22", "badge_text": "#6EE7B7",
        "tagline": "#34D399",
        "glow_a": "#10B981", "glow_b": "#0EA5E9",
        "pattern": "dots",
    },
    "checklist": {
        "a": "#10B981", "b": "#059669",
        "accent": "#34D399",
        "badge_bg": "#022C22", "badge_text": "#6EE7B7",
        "tagline": "#34D399",
        "glow_a": "#10B981", "glow_b": "#059669",
        "pattern": "dots",
    },
    "devops": {
        "a": "#14B8A6", "b": "#0D9488",
        "accent": "#5EEAD4",
        "badge_bg": "#042F2E", "badge_text": "#5EEAD4",
        "tagline": "#2DD4BF",
        "glow_a": "#14B8A6", "glow_b": "#0D9488",
        "pattern": "topology",
    },
    "networking": {
        "a": "#64748B", "b": "#475569",
        "accent": "#94A3B8",
        "badge_bg": "#1E293B", "badge_text": "#CBD5E1",
        "tagline": "#94A3B8",
        "glow_a": "#3B82F6", "glow_b": "#64748B",
        "pattern": "nodes",
    },
}
DEFAULT_PALETTE = THEME_PALETTES["comparison"]


# ── Brand visual-style presets ───────────────────────────────────────────────
# Selected via brand.metadata["card_style"].  Controls surface colours and
# overall mood so that pages with different identities look meaningfully
# distinct even when posting similar content.
#
# Each preset carries:
#   bg        — canvas background colour (R,G,B)
#   col_bg    — inner column / card background
#   txt_main  — primary body text
#   txt_muted — secondary / label text
#   txt_dim   — very muted (footer, dividers)
#   rule_clr  — hairline divider colour
#   dark_mode — True = light text on dark bg; False = dark text on light bg
#   footer_label — override string for footer left side (None = keep default)
#
# "tech_dark"   → default dark-navy developer aesthetic (Dev Comparisons page)
# "faith"       → midnight-blue with warm-gold accents (Gospel / devotional)
# "minimal_light" → clean white surface, slate text
# "vibrant"     → near-black with amplified saturation
BRAND_STYLE_PRESETS: dict[str, dict] = {
    "tech_dark": {
        "bg":        (10,  13,  20),
        "col_bg":    (16,  22,  36),
        "txt_main":  (225, 233, 242),
        "txt_muted": (120, 130, 145),
        "txt_dim":   (70,  80,  98),
        "rule_clr":  (28,  38,  54),
        "dark_mode": True,
        "footer_label": None,
    },
    "faith": {
        # Deep royal-blue canvas, warm ivory text, gold accents
        "bg":        (10,  14,  40),
        "col_bg":    (16,  22,  60),
        "txt_main":  (245, 240, 220),
        "txt_muted": (175, 158, 110),
        "txt_dim":   (100,  88,  60),
        "rule_clr":  (35,  48,  90),
        "dark_mode": True,
        "footer_label": None,
    },
    "minimal_light": {
        "bg":        (248, 250, 252),
        "col_bg":    (241, 245, 249),
        "txt_main":  (15,  23,  42),
        "txt_muted": (71,  85, 105),
        "txt_dim":   (148, 163, 184),
        "rule_clr":  (203, 213, 225),
        "dark_mode": False,
        "footer_label": None,
    },
    "vibrant": {
        "bg":        (5,   7,  15),
        "col_bg":    (12,  16,  32),
        "txt_main":  (255, 255, 255),
        "txt_muted": (180, 180, 200),
        "txt_dim":   (100, 100, 130),
        "rule_clr":  (30,  30,  60),
        "dark_mode": True,
        "footer_label": None,
    },
}
DEFAULT_BRAND_STYLE = BRAND_STYLE_PRESETS["tech_dark"]


def brand_style_for(metadata: dict) -> dict:
    """
    Return the brand surface-style dict for a given brand.metadata mapping.
    Falls back to 'tech_dark' if no card_style key is present.
    """
    key = (metadata or {}).get("card_style", "tech_dark")
    return BRAND_STYLE_PRESETS.get(key, DEFAULT_BRAND_STYLE)


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


# ── In-browser autofit (safety net, not primary fitting mechanism) ─────────
# Shrinks any [data-autofit] element in 0.5-px steps until it no longer
# overflows its container, down to data-autofit-min. Runs AFTER Python-side
# density-aware sizing and word-count capping, so it rarely needs to move
# more than a couple of px — this keeps typography predictable card-to-card
# while still guaranteeing zero clipping/overflow as a last resort.
AUTOFIT_JS = """
(() => {
  const fits = (el) =>
    el.scrollHeight <= el.clientHeight + 2 &&
    el.scrollWidth  <= el.clientWidth  + 2;

  document.querySelectorAll('[data-autofit]').forEach(el => {
    const min = parseFloat(el.dataset.autofitMin || '15');
    let size = parseFloat(getComputedStyle(el).fontSize);
    let guard = 0;
    while (!fits(el) && size > min && guard < 100) {
      size -= 0.5;
      el.style.fontSize = size + 'px';
      guard++;
    }
  });

  // Second pass: panels that must stay visually balanced in height —
  // if one sibling panel is much taller than another, nudge padding.
  document.querySelectorAll('[data-balance-group]').forEach(group => {
    const panels = Array.from(group.querySelectorAll('[data-balance-item]'));
    if (panels.length < 2) return;
    const heights = panels.map(p => p.getBoundingClientRect().height);
    const max = Math.max(...heights);
    panels.forEach((p, i) => {
      const diff = max - heights[i];
      if (diff > 4) {
        const pad = parseFloat(getComputedStyle(p).paddingTop) + diff / 2;
        p.style.paddingTop = pad + 'px';
        p.style.paddingBottom = pad + 'px';
      }
    });
  });
})();
"""
