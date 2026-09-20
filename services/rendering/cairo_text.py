"""
Cairo-enhanced text rendering for Pillow cards.

Uses pycairo for superior typography:
- Sub-pixel anti-aliasing
- Proper kerning pairs
- Ligature substitution
- Better font hinting

Falls back gracefully to standard Pillow text if cairo is not installed.

Usage:
    from .cairo_text import render_text_block, is_cairo_available

    if is_cairo_available():
        text_img = render_text_block(lines, font_path, font_size, color, width)
        img.paste(text_img, (x, y), text_img)  # Alpha compositing
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── Cairo availability check ──────────────────────────────────────────

_cairo_available: Optional[bool] = None


def is_cairo_available() -> bool:
    """Check if pycairo is installed and working."""
    global _cairo_available
    if _cairo_available is not None:
        return _cairo_available

    try:
        import cairo
        _cairo_available = True
        logger.info("Cairo text rendering available — enhanced typography enabled")
        return True
    except ImportError:
        _cairo_available = False
        logger.info("Cairo not installed — using standard Pillow text")
        return False
    except Exception as exc:
        _cairo_available = False
        logger.warning("Cairo init failed: %s — using standard Pillow text", exc)
        return False


# ── Cairo text rendering ──────────────────────────────────────────────

def render_text_block(
    lines: list,
    font_path: str,
    font_size: int,
    color: tuple,
    width: int,
    line_spacing: int = 4,
    align: str = "left",
) -> Optional[object]:
    """
    Render multiple lines of text using Cairo for superior typography.

    Args:
        lines: List of text strings to render
        font_path: Path to .ttf or .otf font file
        font_size: Font size in render-space pixels
        color: (r, g, b) tuple (0-255)
        width: Max width in pixels
        line_spacing: Extra pixels between lines
        align: "left", "center", or "right"

    Returns:
        PIL Image with alpha channel, or None if Cairo unavailable
    """
    if not is_cairo_available():
        return None

    try:
        import cairo
        from PIL import Image

        # Calculate required height
        surface_temp = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, 100)
        ctx_temp = cairo.Context(surface_temp)

        ctx_temp.select_font_face(
            font_path, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL,
        )
        ctx_temp.set_font_size(font_size)

        total_height = 0
        line_heights = []
        for line in lines:
            extents = ctx_temp.font_extents()
            line_h = int(extents[2]) + line_spacing  # height + spacing
            line_heights.append(line_h)
            total_height += line_h

        if total_height == 0:
            return None

        # Create actual surface
        surface = cairo.ImageSurface(
            cairo.FORMAT_ARGB32, width, total_height,
        )
        ctx = cairo.Context(surface)

        # Font options for maximum quality
        font_options = cairo.FontOptions()
        font_options.set_antialias(cairo.ANTIALIAS_SUBPIXEL)
        font_options.set_hint_style(cairo.HINT_STYLE_FULL)
        font_options.set_hint_metrics(cairo.HINT_METRICS_ON)
        font_options.set_subpixel_order(cairo.SUBPIXEL_ORDER_RGB)
        ctx.set_font_options(font_options)

        # Select font face
        ctx.select_font_face(
            font_path, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL,
        )
        ctx.set_font_size(font_size)

        # Set color
        ctx.set_source_rgba(
            color[0] / 255.0,
            color[1] / 255.0,
            color[2] / 255.0,
            1.0,
        )

        # Draw each line
        y = 0
        extents = ctx.font_extents()
        ascent = extents[0]

        for i, line in enumerate(lines):
            if align == "center":
                x_bearing, _, text_w, _, _, _ = ctx.text_extents(line)
                x = (width - text_w) / 2 - x_bearing
            elif align == "right":
                x_bearing, _, text_w, _, _, _ = ctx.text_extents(line)
                x = width - text_w - x_bearing
            else:
                x = 0

            ctx.move_to(x, y + ascent)
            ctx.show_text(line)
            y += line_heights[i]

        # Convert to PIL Image
        buf = surface.write_to_png()
        img = Image.open(__import__('io').BytesIO(buf))

        return img

    except Exception as exc:
        logger.warning("Cairo text rendering failed: %s — falling back to Pillow", exc)
        return None


def render_single_line(
    text: str,
    font_path: str,
    font_size: int,
    color: tuple,
) -> Optional[object]:
    """
    Render a single line of text with Cairo.

    Returns PIL Image with alpha channel, or None.
    """
    if not text:
        return None

    try:
        import cairo

        # Measure first
        surface_temp = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
        ctx_temp = cairo.Context(surface_temp)
        ctx_temp.select_font_face(
            font_path, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL,
        )
        ctx_temp.set_font_size(font_size)
        x_bearing, _, text_w, _, _, _ = ctx_temp.text_extents(text)
        extents = ctx_temp.font_extents()

        width = int(text_w + 4)
        height = int(extents[2] + 4)

        if width <= 0 or height <= 0:
            return None

        return render_text_block(
            [text], font_path, font_size, color, width,
        )

    except Exception:
        return None
