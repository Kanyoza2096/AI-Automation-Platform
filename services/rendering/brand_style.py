"""
BrandStyle — authoritative brand identity for rendering.

Wraps and extends the existing BRAND_STYLE_PRESETS from layout.py with
archetype-awareness (ministry, technology, education, etc.) while
maintaining full backward compatibility with the current card_style system.

Color precedence chain (single source of truth):
  1. Explicit brand colors (profile.colors.background, etc.)
  2. Brand card_style metadata (faith, tech_dark, minimal_light, vibrant)
  3. Archetype defaults
  4. Global fallback (tech_dark)
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from services.rendering.layout import (
    THEME_PALETTES,
    BRAND_STYLE_PRESETS,
    palette_for,
    brand_style_for as legacy_brand_style_for,
    DEFAULT_BRAND_STYLE,
)


class BrandArchetype(Enum):
    MINISTRY = "ministry"
    TECHNOLOGY = "technology"
    EDUCATION = "education"
    LIFESTYLE = "lifestyle"
    PERSONAL = "personal"
    ENTERPRISE = "enterprise"
    NGO = "ngo"
    MEDIA = "media"
    GENERAL = "general"


class VisualDensity(Enum):
    COMPACT = "compact"
    COMFORTABLE = "comfortable"
    AIRY = "airy"


# ── Archetype → existing preset mapping ──────────────────────────────
# Uses the presets you already defined in layout.py.
# "faith" = deep royal-blue, warm ivory text, gold accents
# "tech_dark" = dark-navy developer aesthetic
# "minimal_light" = clean white surface
# "vibrant" = near-black, high saturation

ARCHETYPE_TO_PRESET: dict[BrandArchetype, str] = {
    BrandArchetype.MINISTRY:    "faith",
    BrandArchetype.TECHNOLOGY:  "tech_dark",
    BrandArchetype.EDUCATION:   "minimal_light",
    BrandArchetype.LIFESTYLE:   "vibrant",
    BrandArchetype.PERSONAL:    "minimal_light",
    BrandArchetype.ENTERPRISE:  "tech_dark",
    BrandArchetype.NGO:         "faith",
    BrandArchetype.MEDIA:       "vibrant",
    BrandArchetype.GENERAL:     "tech_dark",
}

# ── Archetype-specific defaults ──────────────────────────────────────

ARCHETYPE_DEFAULTS: dict[BrandArchetype, dict] = {
    BrandArchetype.MINISTRY: {
        "winner_label": "RECOMMENDATION",
        "vs_label": "compared with",
        "max_hashtags": 2,
        "show_date": True,
        "footer_text": None,
        "density": "airy",
        "font_scale": 1.05,
        "corner_radius": 24,
        "prefers_serif": True,
    },
    BrandArchetype.TECHNOLOGY: {
        "winner_label": "WINNER",
        "vs_label": "VS",
        "max_hashtags": 5,
        "show_date": True,
        "footer_text": None,
        "density": "comfortable",
        "font_scale": 0.95,
        "corner_radius": 16,
        "prefers_serif": False,
    },
    BrandArchetype.EDUCATION: {
        "winner_label": "BEST APPROACH",
        "vs_label": "VS",
        "max_hashtags": 4,
        "show_date": True,
        "footer_text": None,
        "density": "comfortable",
        "font_scale": 1.0,
        "corner_radius": 20,
        "prefers_serif": False,
    },
    BrandArchetype.LIFESTYLE: {
        "winner_label": "OUR PICK",
        "vs_label": "VS",
        "max_hashtags": 4,
        "show_date": True,
        "footer_text": None,
        "density": "airy",
        "font_scale": 1.02,
        "corner_radius": 24,
        "prefers_serif": False,
    },
    BrandArchetype.PERSONAL: {
        "winner_label": "MY TAKE",
        "vs_label": "VS",
        "max_hashtags": 3,
        "show_date": True,
        "footer_text": None,
        "density": "airy",
        "font_scale": 1.05,
        "corner_radius": 24,
        "prefers_serif": False,
    },
    BrandArchetype.ENTERPRISE: {
        "winner_label": "RECOMMENDATION",
        "vs_label": "VS",
        "max_hashtags": 3,
        "show_date": True,
        "footer_text": None,
        "density": "comfortable",
        "font_scale": 0.98,
        "corner_radius": 12,
        "prefers_serif": False,
    },
    BrandArchetype.NGO: {
        "winner_label": "OUR CHOICE",
        "vs_label": "compared with",
        "max_hashtags": 3,
        "show_date": True,
        "footer_text": None,
        "density": "airy",
        "font_scale": 1.0,
        "corner_radius": 20,
        "prefers_serif": False,
    },
    BrandArchetype.MEDIA: {
        "winner_label": "VERDICT",
        "vs_label": "VS",
        "max_hashtags": 4,
        "show_date": True,
        "footer_text": None,
        "density": "comfortable",
        "font_scale": 1.0,
        "corner_radius": 16,
        "prefers_serif": False,
    },
    BrandArchetype.GENERAL: {
        "winner_label": "WINNER",
        "vs_label": "VS",
        "max_hashtags": 4,
        "show_date": True,
        "footer_text": None,
        "density": "comfortable",
        "font_scale": 1.0,
        "corner_radius": 20,
        "prefers_serif": False,
    },
}


@dataclass(frozen=True)
class BrandStyle:
    """
    Immutable, fully resolved brand visual identity.

    Wraps BRAND_STYLE_PRESETS (surface colors) + archetype defaults
    (labels, density, corner radius) into a single object used by
    all layout strategies.
    """

    # ── Identity ──────────────────────────────────────────────────
    brand_name: str = "Kanyoza Systems"
    archetype: BrandArchetype = BrandArchetype.GENERAL

    # ── Surface colors (from BRAND_STYLE_PRESETS, overridable) ────
    bg: tuple = (10, 13, 20)
    col_bg: tuple = (16, 22, 36)
    txt_main: tuple = (225, 233, 242)
    txt_muted: tuple = (120, 130, 145)
    txt_dim: tuple = (70, 80, 98)
    rule_clr: tuple = (28, 38, 54)
    dark_mode: bool = True

    # ── Accent palette (from THEME_PALETTES, overridable) ─────────
    primary: tuple = (99, 102, 241)
    secondary: tuple = (14, 165, 233)
    accent: tuple = (129, 140, 248)
    badge_fill: tuple = (30, 27, 75)
    badge_text: tuple = (165, 180, 252)
    header_text: tuple = (248, 250, 252)

    # ── Typography ────────────────────────────────────────────────
    font_scale: float = 1.0
    prefers_serif: bool = False

    # ── Geometry ──────────────────────────────────────────────────
    corner_radius: int = 20
    density_padding: int = 56
    density_gap: int = 28

    # ── Labels ────────────────────────────────────────────────────
    winner_label: str = "WINNER"
    vs_label: str = "VS"
    footer_label: str = "Powered by Kanyoza AI"

    # ── Content rules ─────────────────────────────────────────────
    max_hashtags: int = 4
    show_date: bool = True
    show_logo: bool = False
    logo_path: Optional[str] = None

    # ── Convenience ───────────────────────────────────────────────
    is_ministry: bool = False
    is_comparison_friendly: bool = True

    def scaled_padding(self, scale: int = 2) -> int:
        return self.density_padding * scale

    def scaled_gap(self, scale: int = 2) -> int:
        return self.density_gap * scale

    def scaled_radius(self, scale: int = 2) -> int:
        return self.corner_radius * scale


# ── Resolver ──────────────────────────────────────────────────────────

class BrandStyleResolver:
    """
    Converts raw brand configuration → fully resolved BrandStyle.

    Color precedence chain:
      1. Explicit brand colors (profile.colors.background)
      2. Brand card_style metadata (faith, tech_dark, etc.)
      3. Archetype defaults
      4. Global fallback (tech_dark)
    """

    @staticmethod
    def _hex_to_rgb(hex_color: str) -> tuple:
        """Convert '#rrggbb' or 'rrggbb' to (r, g, b)."""
        h = hex_color.lstrip("#")
        if len(h) != 6:
            return (99, 102, 241)
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))

    @staticmethod
    def _safe_get(dct: dict, key: str, default):
        """Get value from dict, returning default if missing or None."""
        val = dct.get(key)
        return val if val is not None else default

    def resolve(self, brand_profile: dict | None) -> BrandStyle:
        """
        Resolve a full BrandStyle from a brand profile dict.

        Args:
            brand_profile: Normalized brand dict with keys:
                name, archetype, colors, metadata, logo_url,
                font_family, font_scale, visual_density,
                max_hashtags, footer_text, winner_label

        Returns:
            Fully resolved, immutable BrandStyle
        """
        profile = brand_profile or {}

        # ── Brand name ──────────────────────────────────────────
        brand_name = self._safe_get(
            profile, "name",
            self._safe_get(profile, "brand_name", "Kanyoza Systems"),
        )

        # ── Archetype ───────────────────────────────────────────
        archetype_str = self._safe_get(profile, "archetype", "general")
        try:
            archetype = BrandArchetype(archetype_str.lower())
        except ValueError:
            archetype = BrandArchetype.GENERAL

        # ── Metadata ────────────────────────────────────────────
        metadata = (
            profile.get("metadata")
            or profile.get("brand_metadata")
            or {}
        )
        if isinstance(metadata, str):
            import json
            try:
                metadata = json.loads(metadata)
            except Exception:
                metadata = {}

        # ── Surface style (from existing layout.py) ─────────────
        surface = dict(legacy_brand_style_for(metadata))

        # ── Explicit brand colors (precedence 1) ────────────────
        colors = (
            profile.get("colors")
            or profile.get("brand_colors")
            or {}
        )
        if isinstance(colors, str):
            import json
            try:
                colors = json.loads(colors)
            except Exception:
                colors = {}

        dark_mode = self._safe_get(
            metadata, "dark_mode",
            self._safe_get(surface, "dark_mode", True),
        )

        # Explicit brand background wins over card_style preset
        if "background" in colors:
            bg = self._hex_to_rgb(colors["background"])
            if dark_mode:
                col_bg = tuple(min(c + 8, 255) for c in bg)
            else:
                col_bg = tuple(max(c - 3, 0) for c in bg)
            surface["bg"] = bg
            surface["col_bg"] = col_bg

        # Explicit brand text colors
        if "text" in colors:
            surface["txt_main"] = self._hex_to_rgb(colors["text"])
        if "text_secondary" in colors or "muted_text" in colors:
            surface["txt_muted"] = self._hex_to_rgb(
                colors.get("text_secondary") or colors.get("muted_text")
            )

        # ── Accent palette (from THEME_PALETTES) ────────────────
        theme_raw = self._safe_get(profile, "theme", "ai")
        theme_key = str(theme_raw).split(".")[-1].lower()
        palette = dict(palette_for(theme_key))

        primary = self._hex_to_rgb(
            self._safe_get(colors, "primary",
            self._safe_get(palette, "a", "#6366f1"))
        )
        secondary = self._hex_to_rgb(
            self._safe_get(colors, "secondary",
            self._safe_get(palette, "b", "#0ea5e9"))
        )
        accent = self._hex_to_rgb(
            self._safe_get(colors, "accent",
            self._safe_get(palette, "accent", "#818cf8"))
        )
        badge_fill = self._hex_to_rgb(
            self._safe_get(palette, "badge_bg", "#1e1b4b")
        )
        badge_text = self._hex_to_rgb(
            self._safe_get(palette, "badge_text", "#a5b4fc")
        )
        header_text = (248, 250, 252) if dark_mode else (255, 255, 255)

        # ── Archetype defaults ──────────────────────────────────
        arch = ARCHETYPE_DEFAULTS.get(
            archetype,
            ARCHETYPE_DEFAULTS[BrandArchetype.GENERAL],
        )

        # ── Typography ──────────────────────────────────────────
        font_family = self._safe_get(
            profile, "font_family",
            self._safe_get(metadata, "font_family", "sans-serif"),
        )
        prefers_serif = (
            self._safe_get(arch, "prefers_serif", False)
            or font_family == "serif"
        )
        font_scale = float(
            self._safe_get(profile, "font_scale",
            self._safe_get(arch, "font_scale", 1.0))
        )

        # ── Density → pixel values ──────────────────────────────
        density_str = self._safe_get(
            profile, "visual_density",
            self._safe_get(metadata, "density",
            self._safe_get(arch, "density", "comfortable")),
        )
        density_map = {
            "compact": (48, 24),
            "comfortable": (56, 28),
            "airy": (64, 32),
        }
        density_padding, density_gap = density_map.get(
            density_str, (56, 28),
        )

        # ── Geometry ────────────────────────────────────────────
        corner_radius = int(
            self._safe_get(profile, "corner_radius",
            self._safe_get(arch, "corner_radius", 20))
        )

        # ── Labels ──────────────────────────────────────────────
        footer_label = (
            self._safe_get(profile, "footer_text",
            self._safe_get(arch, "footer_text", None))
            or surface.get("footer_label")
            or "Powered by Kanyoza AI"
        )
        winner_label = self._safe_get(
            profile, "winner_label",
            self._safe_get(arch, "winner_label", "WINNER"),
        )
        vs_label = self._safe_get(
            arch, "vs_label", "VS",
        )

        # ── Content rules ───────────────────────────────────────
        max_hashtags = int(
            self._safe_get(profile, "max_hashtags",
            self._safe_get(arch, "max_hashtags", 4))
        )
        show_date = self._safe_get(
            profile, "show_date",
            self._safe_get(arch, "show_date", True),
        )

        return BrandStyle(
            brand_name=brand_name,
            archetype=archetype,
            bg=surface["bg"],
            col_bg=surface["col_bg"],
            txt_main=surface["txt_main"],
            txt_muted=surface["txt_muted"],
            txt_dim=surface["txt_dim"],
            rule_clr=surface["rule_clr"],
            dark_mode=dark_mode,
            primary=primary,
            secondary=secondary,
            accent=accent,
            badge_fill=badge_fill,
            badge_text=badge_text,
            header_text=header_text,
            font_scale=font_scale,
            prefers_serif=prefers_serif,
            corner_radius=corner_radius,
            density_padding=density_padding,
            density_gap=density_gap,
            winner_label=winner_label,
            vs_label=vs_label,
            footer_label=footer_label,
            max_hashtags=max_hashtags,
            show_date=show_date,
            show_logo=bool(profile.get("logo_url")),
            logo_path=profile.get("logo_url"),
            is_ministry=(archetype == BrandArchetype.MINISTRY),
            is_comparison_friendly=(
                archetype
                not in (BrandArchetype.MINISTRY, BrandArchetype.NGO)
            ),
        )
