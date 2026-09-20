"""
Card Renderer v16.1 — brand-aware adaptive card engine.

Pipeline (per render call):
  0. RenderCache    — return cached PNG immediately on hit
  1. Normalize brand — accept Brand object, dict, or None
  2. BrandStyleResolver — resolve brand identity from profile + archetype
  3. Content-based layout selection (comparison, editorial, quote, etc.)
  4. Layout strategy — measure + render with Pillow (primary)
  5. HCTI — external fallback (htmlcsstoimage.com)

Architecture:
  Brand profile → BrandStyle → Content analysis → Layout strategy
                                                      ↓
                                                    Pillow → PNG
                                                      ↓ (fallback)
                                                    HCTI → PNG

v16.1 changes (from v15):
  - BrandStyle with 9 archetypes (ministry, technology, education, etc.)
  - Content-based layout selection (not hardcoded comparison)
  - Brand object, dict, or None all accepted via _normalize_brand_profile()
  - font_scale actually applied to typography
  - vs_label handles both short ("VS") and long ("compared with") labels
  - Color precedence: explicit brand > card_style > archetype > default
  - render_legacy() for backward compatibility with render_queue.py
  - Removed Playwright dependency (RAM constraint)
  - Cache key includes resolved brand style + layout
"""

from services.rendering.engine import CardRenderer, card_renderer

__all__ = ["CardRenderer", "card_renderer"]
