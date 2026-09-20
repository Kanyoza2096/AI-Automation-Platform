"""
Pillow-specific text measurement and drawing helpers.

Stateless. Used by layout strategies during the measure phase
(before any pixels are drawn) and during the render phase.

All coordinates are in render-space (scaled) pixels: 2160×2160.
"""

import io
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Canvas constants
CARD_CSS_W = 1080
CARD_CSS_H = 1080
SCALE = 2
CARD_W = CARD_CSS_W * SCALE
CARD_H = CARD_CSS_H * SCALE


# ── Font loading ──────────────────────────────────────────────────────

def load_font(font_path: str, size: int):
    """Load a Pillow font with multi-level fallback."""
    from PIL import ImageFont

    # Try exact path
    try:
        return ImageFont.truetype(font_path, size)
    except Exception:
        pass

    # Try common alternatives
    alt_paths = [
        font_path,
        font_path.replace("/truetype/", "/"),
        font_path.replace("DejaVuSans-Bold", "Arial-Bold"),
        font_path.replace("DejaVuSans", "Arial"),
        font_path.replace("DejaVuSerif-Bold", "TimesNewRoman-Bold"),
        font_path.replace("DejaVuSerif", "TimesNewRoman"),
    ]

    for path in alt_paths:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue

    # Last resort: default bitmap font
    logger.warning("No font found for %s (size %d), using default", font_path, size)
    return ImageFont.load_default()


# ── Text measurement ──────────────────────────────────────────────────

def text_bbox(draw, text: str, font) -> tuple:
    """Return (width, height) of text using textbbox (most reliable)."""
    if not text:
        return (0, 0)
    bbox = draw.textbbox((0, 0), text, font=font)
    return (bbox[2] - bbox[0], bbox[3] - bbox[1])


def text_width(draw, text: str, font) -> int:
    """Return width of text in pixels."""
    return text_bbox(draw, text, font)[0]


def text_height(draw, text: str, font) -> int:
    """Return height of text in pixels."""
    return text_bbox(draw, text, font)[1]


def wrap_text(
    draw, text: str, font, max_width: int, max_lines: int = 99
) -> list:
    """
    Wrap text to fit within max_width pixels.

    Handles long words by breaking at character level.
    Returns list of lines. Never returns empty list.
    """
    if not text:
        return []

    words = text.split()
    lines = []
    current = []

    for word in words:
        candidate = " ".join(current + [word])
        w = text_width(draw, candidate, font)

        if w <= max_width:
            current.append(word)
        else:
            if current:
                lines.append(" ".join(current))
                current = []

            word_w = text_width(draw, word, font)
            if word_w <= max_width:
                current = [word]
            else:
                broken = _break_long_word(draw, word, font, max_width)
                lines.extend(broken[:-1])
                current = [broken[-1]] if broken else []

        if len(lines) >= max_lines:
            break

    if current and len(lines) < max_lines:
        lines.append(" ".join(current))

    return lines or [""]


def _break_long_word(draw, word: str, font, max_width: int) -> list:
    """Break a single long word across multiple lines."""
    lines = []
    current = ""
    for char in word:
        test = current + char
        if text_width(draw, test, font) <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = char
    if current:
        lines.append(current)
    return lines or [word]


def measure_text_block(
    draw,
    text: str,
    font,
    max_width: int,
    line_spacing: int = 12,
    max_lines: int = 99,
) -> dict:
    """
    Measure a text block — returns wrapped lines and total height.

    Used by all layout strategies for pre-render measurement.
    """
    lines = wrap_text(draw, text, font, max_width, max_lines)
    total_h = sum(
        text_height(draw, line, font) + line_spacing for line in lines
    )
    return {
        "lines": lines,
        "total_height": total_h,
        "line_count": len(lines),
    }


# ── Drawing helpers ───────────────────────────────────────────────────

def draw_horizontal_gradient(
    draw,
    y0: int,
    y1: int,
    col_left: tuple,
    col_right: tuple,
    x0: int = 0,
    x1: int = CARD_W,
):
    """Draw a horizontal gradient by iterating over pixels."""
    for xi in range(x0, x1):
        t = (xi - x0) / max(x1 - x0 - 1, 1)
        r = int(col_left[0] + (col_right[0] - col_left[0]) * t)
        g = int(col_left[1] + (col_right[1] - col_left[1]) * t)
        b = int(col_left[2] + (col_right[2] - col_left[2]) * t)
        draw.line([(xi, y0), (xi, y1)], fill=(r, g, b))


def draw_rounded_rect(
    draw,
    xy: list,
    radius: int,
    fill=None,
    outline=None,
    width: int = 1,
):
    """Draw rounded rectangle — Pillow 9.2+ compatible."""
    draw.rounded_rectangle(
        xy, radius=radius, fill=fill, outline=outline, width=width
    )


def export_png(img) -> bytes:
    """Export PIL Image to PNG bytes."""
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False, compress_level=1)
    return buf.getvalue()

def normalize_hashtags(hashtags: any) -> str:
    """
    Normalize hashtags from list, string, or None to a clean string.

    Accepts:
        "tag1 tag2"           → "#tag1 #tag2"
        "#tag1 #tag2"         → "#tag1 #tag2"
        ["tag1", "tag2"]      → "#tag1 #tag2"
        ["#tag1", "#tag2"]    → "#tag1 #tag2"
        None / ""             → ""
    """
    if not hashtags:
        return ""

    if isinstance(hashtags, list):
        hashtags = " ".join(
            f"#{h.lstrip('#')}" if h and not h.startswith("#") else (h or "")
            for h in hashtags
            if h
        )

    return str(hashtags).strip()
