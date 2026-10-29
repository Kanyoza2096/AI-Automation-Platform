"""
Self-hosted webfont embedding for the card renderer.

Fonts are embedded as base64 data URIs directly in the rendered HTML so
Playwright never depends on network access or filesystem path resolution
at screenshot time. The single variable-weight Inter file covers the
whole 400-900 weight range used across all card templates.
"""

from __future__ import annotations

import base64
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger("kanyoza.services.rendering.fonts")

_FONT_PATH = Path(__file__).parent.parent.parent / "static" / "fonts" / "Inter-Variable.woff2"

FONT_FAMILY = "Inter"
_FALLBACK_STACK = "'Inter', 'Segoe UI', system-ui, -apple-system, sans-serif"
FONT_STACK = _FALLBACK_STACK


@lru_cache(maxsize=1)
def _font_base64() -> str:
    if not _FONT_PATH.exists():
        logger.warning("Font file not found at %s — falling back to system fonts", _FONT_PATH)
        return ""
    return base64.b64encode(_FONT_PATH.read_bytes()).decode("ascii")


@lru_cache(maxsize=1)
def font_face_css() -> str:
    """
    Return a <style> block that declares the embedded @font-face.

    Uses font-display: block so the browser never paints with a fallback
    font before the real face is ready — combined with waiting on
    `document.fonts.ready` before the Playwright screenshot, this
    guarantees crisp, final typography with no flash-of-fallback-text.
    """
    b64 = _font_base64()
    if not b64:
        return "<style>/* Inter font unavailable — using system font stack */</style>"

    return f"""<style>
@font-face {{
    font-family: '{FONT_FAMILY}';
    font-style: normal;
    font-weight: 100 900;
    font-display: block;
    src: url(data:font/woff2;charset=utf-8;base64,{b64}) format('woff2-variations'), 
         url(data:font/woff2;charset=utf-8;base64,{b64}) format('woff2');
}}
</style>"""
