"""
Design tokens and the in-browser autofit script shared by every card template.

Keeping these values in one place is what lets the templates stay
"responsive" — a template never hard-codes a pixel position that would
clip when a title or bullet is longer/shorter than expected.
"""

from __future__ import annotations

CARD_W = 1080
CARD_H = 1080

# ── Spacing scale (4px base grid) ───────────────────────────────────────────
SPACE = {
    "xs": 8,
    "sm": 16,
    "md": 24,
    "lg": 32,
    "xl": 48,
    "xxl": 64,
}

# ── Type scale (per spec) ───────────────────────────────────────────────────
FONT_SIZE = {
    "title_max": 64,
    "title_min": 40,
    "heading_max": 38,
    "heading_min": 24,
    "bullet_max": 30,
    "bullet_min": 20,
    "winner_max": 34,
    "winner_min": 22,
    "reason": 22,
    "badge": 15,
    "footer": 17,
}

BULLET_MAX_LINES = 3
TITLE_MAX_LINES = 2

# ── Theme palettes ───────────────────────────────────────────────────────────
# Every theme gets a primary/secondary accent pair used for badges,
# column headers, dividers, and the winner banner gradient.
THEME_PALETTES = {
    "comparison":    {"a": "#6366F1", "b": "#0EA5E9", "badge": ("#4F46E5", "#7C3AED"), "tagline": "#818CF8"},
    "security":      {"a": "#6366F1", "b": "#0EA5E9", "badge": ("#4F46E5", "#7C3AED"), "tagline": "#818CF8"},
    "database":      {"a": "#6366F1", "b": "#0EA5E9", "badge": ("#4F46E5", "#7C3AED"), "tagline": "#818CF8"},
    "ai":            {"a": "#6366F1", "b": "#0EA5E9", "badge": ("#4F46E5", "#7C3AED"), "tagline": "#818CF8"},
    "code_tips":     {"a": "#F43F5E", "b": "#10B981", "badge": ("#D97706", "#F59E0B"), "tagline": "#FBBF24"},
    "backend":       {"a": "#F43F5E", "b": "#10B981", "badge": ("#D97706", "#F59E0B"), "tagline": "#FBBF24"},
    "frontend":      {"a": "#F43F5E", "b": "#10B981", "badge": ("#D97706", "#F59E0B"), "tagline": "#FBBF24"},
    "architecture":  {"a": "#0EA5E9", "b": "#6366F1", "badge": ("#0EA5E9", "#6366F1"), "tagline": "#38BDF8"},
    "system_design": {"a": "#0EA5E9", "b": "#6366F1", "badge": ("#0EA5E9", "#6366F1"), "tagline": "#38BDF8"},
    "cloud":         {"a": "#0EA5E9", "b": "#6366F1", "badge": ("#0EA5E9", "#6366F1"), "tagline": "#38BDF8"},
    "tutorial":      {"a": "#10B981", "b": "#059669", "badge": ("#059669", "#10B981"), "tagline": "#34D399"},
}
DEFAULT_PALETTE = THEME_PALETTES["comparison"]

SURFACE = {
    "bg": "#0B1120",
    "card": "#141C2E",
    "card_border": "#26324A",
    "divider": "#2B3752",
    "text_primary": "#F8FAFC",
    "text_secondary": "#CBD5E1",
    "text_muted": "#64748B",
}


def palette_for(theme_value: str) -> dict:
    return THEME_PALETTES.get(theme_value, DEFAULT_PALETTE)


# ── In-browser autofit ──────────────────────────────────────────────────────
# Runs once, after web fonts are ready and the page has settled. Any
# element flagged with [data-autofit] shrinks its own font-size in 1px
# steps (down to data-min) until it no longer overflows its box. This is
# what makes "no overflow, no truncation" a guarantee rather than a hope
# — Python-side wrapping/shortening handles the common case, this JS
# pass is the safety net for edge cases (very long option names, narrow
# glyphs, unexpected locales, etc).
AUTOFIT_JS = """
(() => {
    const nodes = document.querySelectorAll('[data-autofit]');
    nodes.forEach((el) => {
        const min = parseFloat(el.dataset.autofitMin || '16');
        let size = parseFloat(getComputedStyle(el).fontSize);
        let guard = 0;
        const fits = () =>
            el.scrollHeight <= el.clientHeight + 1 &&
            el.scrollWidth <= el.clientWidth + 1;
        while (!fits() && size > min && guard < 60) {
            size -= 1;
            el.style.fontSize = size + 'px';
            guard += 1;
        }
    });
})();
"""
