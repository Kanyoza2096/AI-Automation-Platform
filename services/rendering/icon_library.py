"""
SVG Icon Library — topic-aware inline icons for card headers.

Each icon is a complete <svg> element using viewBox="0 0 24 24" with
stroke-based rendering (no fill) so it inherits its colour from CSS
`color` or an explicit `stroke` attribute passed at render time.

Usage:
    from services.rendering.icon_library import get_icon_svg, THEME_ICONS
    svg = get_icon_svg("database", color="#34D399", size=20)
"""

from __future__ import annotations

# Map theme → canonical icon name (also accepts the icon name directly)
THEME_ICONS: dict[str, str] = {
    "database":    "database",
    "ai":          "ai",
    "cloud":       "cloud",
    "security":    "shield",
    "architecture":"blocks",
    "backend":     "server",
    "frontend":    "browser",
    "comparison":  "scale",
    "code_tips":   "code",
    "system_design": "cog",
    "tutorial":    "book",
    "checklist":   "checklist",
    "devops":      "cog",
    "networking":  "network",
}

# SVG path/shape definitions (viewBox 0 0 24 24, stroke-based)
_PATHS: dict[str, str] = {
    "database": (
        '<ellipse cx="12" cy="7" rx="8.5" ry="3" stroke-width="1.5"/>'
        '<path stroke-width="1.5" stroke-linecap="round" d="M3.5 7v5c0 1.66 3.81 3 8.5 3s8.5-1.34 8.5-3V7"/>'
        '<path stroke-width="1.5" stroke-linecap="round" d="M3.5 12v5c0 1.66 3.81 3 8.5 3s8.5-1.34 8.5-3v-5"/>'
    ),
    "ai": (
        '<rect x="7" y="7" width="10" height="10" rx="2" stroke-width="1.5"/>'
        '<path stroke-width="1.5" stroke-linecap="round" d="M7 9.5H4M7 12H4M7 14.5H4'
        'M17 9.5h3M17 12h3M17 14.5h3'
        'M9.5 7V4M12 7V4M14.5 7V4'
        'M9.5 17v3M12 17v3M14.5 17v3"/>'
        '<circle cx="12" cy="12" r="1.5" fill="currentColor" stroke="none"/>'
    ),
    "cloud": (
        '<path stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"'
        ' d="M6.5 19a4.5 4.5 0 01-.58-8.96A5.5 5.5 0 0116.87 9.5'
        ' 3.5 3.5 0 0120 13c0 1.93-1.57 3.5-3.5 3.5H6.5z"/>'
    ),
    "shield": (
        '<path stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"'
        ' d="M12 2l8 3v7c0 4.97-3.46 9.6-8 11C7.46 23 4 18.37 4 13V5l8-3z"/>'
        '<path stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"'
        ' d="M9 12l2 2 4-4"/>'
    ),
    "blocks": (
        '<rect x="3" y="3" width="7" height="7" rx="1.5" stroke-width="1.5"/>'
        '<rect x="14" y="3" width="7" height="7" rx="1.5" stroke-width="1.5"/>'
        '<rect x="3" y="14" width="7" height="7" rx="1.5" stroke-width="1.5"/>'
        '<rect x="14" y="14" width="7" height="7" rx="1.5" stroke-width="1.5"/>'
    ),
    "server": (
        '<rect x="2" y="2" width="20" height="9" rx="2" stroke-width="1.5"/>'
        '<rect x="2" y="13" width="20" height="9" rx="2" stroke-width="1.5"/>'
        '<circle cx="6" cy="6.5" r="1.5" fill="currentColor" stroke="none"/>'
        '<circle cx="6" cy="17.5" r="1.5" fill="currentColor" stroke="none"/>'
        '<path stroke-width="1.5" stroke-linecap="round" d="M10 6.5h6M10 17.5h6"/>'
    ),
    "browser": (
        '<rect x="2" y="3" width="20" height="18" rx="2.5" stroke-width="1.5"/>'
        '<path stroke-width="1.5" d="M2 8.5h20"/>'
        '<circle cx="5.5" cy="5.75" r="1" fill="currentColor" stroke="none"/>'
        '<circle cx="8.5" cy="5.75" r="1" fill="currentColor" stroke="none"/>'
        '<circle cx="11.5" cy="5.75" r="1" fill="currentColor" stroke="none"/>'
    ),
    "code": (
        '<path stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"'
        ' d="M16 18l6-6-6-6M8 6l-6 6 6 6"/>'
        '<path stroke-width="1.5" stroke-linecap="round" d="M14.5 4.5l-5 15"/>'
    ),
    "scale": (
        '<path stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"'
        ' d="M12 2v20M4 6h16M6 10l-2 6h4L6 10zM18 10l-2 6h4l-2-6z"/>'
        '<path stroke-width="1.5" stroke-linecap="round" d="M4 22h16"/>'
    ),
    "cog": (
        '<circle cx="12" cy="12" r="4" stroke-width="1.5"/>'
        '<path stroke-width="1.5" stroke-linecap="round"'
        ' d="M12 2v2M12 20v2M2 12h2M20 12h2'
        'M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41'
        'M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/>'
    ),
    "book": (
        '<path stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"'
        ' d="M4 19.5A2.5 2.5 0 016.5 17H20"/>'
        '<path stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"'
        ' d="M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z"/>'
        '<path stroke-width="1.5" stroke-linecap="round" d="M8 7h8M8 11h5"/>'
    ),
    "checklist": (
        '<path stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"'
        ' d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2"/>'
        '<rect x="9" y="3" width="6" height="4" rx="2" stroke-width="1.5"/>'
        '<path stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"'
        ' d="M9 12l2 2 4-4"/>'
    ),
    "network": (
        '<circle cx="12" cy="12" r="2.5" stroke-width="1.5"/>'
        '<circle cx="4"  cy="6"  r="2" stroke-width="1.5"/>'
        '<circle cx="20" cy="6"  r="2" stroke-width="1.5"/>'
        '<circle cx="4"  cy="18" r="2" stroke-width="1.5"/>'
        '<circle cx="20" cy="18" r="2" stroke-width="1.5"/>'
        '<path stroke-width="1.2" d="M6 6.5l4.5 4.5M18 6.5l-4.5 4.5'
        'M6 17.5l4.5-4.5M18 17.5l-4.5-4.5"/>'
    ),
}


def get_icon_name(theme: str) -> str:
    """Return the canonical icon name for *theme*."""
    return THEME_ICONS.get(theme, "cog")


def get_icon_svg(
    theme: str,
    color: str = "currentColor",
    size: int = 20,
) -> str:
    """
    Return a complete inline <svg> element for the given *theme*.

    Args:
        theme:  PostTheme value string (e.g. "database", "ai").
        color:  CSS colour for the stroke/fill (default "currentColor").
        size:   Width and height in px (default 20).
    """
    icon_name = get_icon_name(theme)
    paths = _PATHS.get(icon_name, _PATHS["cog"])
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{size}" height="{size}" viewBox="0 0 24 24" '
        f'fill="none" stroke="{color}" '
        f'stroke-linecap="round" stroke-linejoin="round">'
        f'{paths}'
        f'</svg>'
    )
