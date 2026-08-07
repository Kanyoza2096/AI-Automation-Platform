"""
Comparison Layout Strategy v16.3
================================

Brand-aware two-column comparison card renderer.

This module is intentionally ONLY responsible for comparison-style cards.
It must not be used as the universal card renderer.

Architecture:

    AI content
        ↓
    Layout selection
        ↓
    ComparisonLayout
        ↓
    Pre-render measurement
        ↓
    Adaptive geometry
        ↓
    Pillow rendering
        ↓
    PNG

Design goals
------------
- Brand-driven visual identity
- Measurement before rendering
- Dynamic typography
- Dynamic column height
- Dynamic winner banner
- Safe hashtag/footer regions
- No duplicate topic badge
- No arbitrary global scaling
- No dependency on Cairo
- Graceful degradation when content is unusually long

Expected BrandStyle interface
-----------------------------
The renderer expects BrandStyle to expose the visual properties already
provided by the existing brand_style.py implementation, including:

    brand_name
    primary
    secondary
    accent
    col_bg
    txt_main
    txt_muted
    header_text
    rule_clr
    winner_label
    vs_label
    footer_label
    show_date
    max_hashtags
    scaled_padding()
    scaled_radius()

Expected render_utils interface
--------------------------------
    SCALE
    CARD_W
    CARD_H
    text_width()
    text_height()
    wrap_text()
    draw_horizontal_gradient()
    export_png()
    normalize_hashtags()
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Iterable, Optional, Sequence

from .brand_style import BrandStyle
from .render_utils import (
    SCALE,
    CARD_W,
    CARD_H,
    text_width,
    text_height,
    wrap_text,
    draw_horizontal_gradient,
    export_png,
    normalize_hashtags,
)
from services.rendering.typography import (
    parse_winner,
    shorten_bullet,
    truncate_chars,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Geometry
# ============================================================================

VS_DIAMETER = 100 * SCALE

COLUMN_INNER_PAD = 32 * SCALE
COLUMN_HEADER_H = 110 * SCALE

BULLET_DOT_RADIUS = max(5, 8 * SCALE)
BULLET_LINE_GAP = 4 * SCALE
BULLET_GAP = 8 * SCALE

RULE_Y_TOP = 120 * SCALE
TOP_BAR_Y = 44 * SCALE

BADGE_TOP_GAP = 28 * SCALE
BADGE_BOTTOM_GAP = 28 * SCALE

TITLE_TOP_GAP = 28 * SCALE
TITLE_LINE_GAP = 10 * SCALE
TITLE_BOTTOM_GAP = 20 * SCALE

COLUMN_TOP_GAP = 12 * SCALE
COLUMN_WINNER_GAP = 28 * SCALE

WINNER_MIN_H = 92 * SCALE
WINNER_MAX_H = 132 * SCALE
WINNER_TOP_PAD = 16 * SCALE
WINNER_BOTTOM_PAD = 14 * SCALE
WINNER_SIDE_PAD = 28 * SCALE

HASHTAG_TOP_GAP = 14 * SCALE
HASHTAG_LINE_GAP = 8 * SCALE

FOOTER_HEIGHT = 100 * SCALE
FOOTER_TEXT_TOP = 14 * SCALE

CONTENT_BOTTOM_MARGIN = 12 * SCALE


# ============================================================================
# Measurement dataclasses
# ============================================================================

class _TitleMeasurement:
    __slots__ = (
        "font_size",
        "font",
        "lines",
        "height",
    )

    def __init__(
        self,
        font_size: int = 22,
        font: Any = None,
        lines: Optional[list[str]] = None,
        height: int = 0,
    ):
        self.font_size = font_size
        self.font = font
        self.lines = lines or []
        self.height = height


class _ColumnMeasurement:
    __slots__ = (
        "font_size",
        "font",
        "bullets",
        "bullet_lines",
        "height",
        "fits",
    )

    def __init__(self):
        self.font_size = 14
        self.font = None
        self.bullets: list[str] = []
        self.bullet_lines: list[list[str]] = []
        self.height = 0
        self.fits = False


class _WinnerMeasurement:
    __slots__ = (
        "name",
        "reason",
        "name_font",
        "reason_font",
        "label_font",
        "name_text",
        "name_width",
        "reason_lines",
        "height",
        "same_line",
    )

    def __init__(self):
        self.name = ""
        self.reason = ""
        self.name_font = None
        self.reason_font = None
        self.label_font = None
        self.name_text = ""
        self.name_width = 0
        self.reason_lines: list[str] = []
        self.height = 0
        self.same_line = False


# ============================================================================
# Comparison Layout
# ============================================================================

class ComparisonLayout:
    """
    Renders a comparison-specific card.

    This strategy is selected by the higher-level rendering engine only when
    the content/layout resolver determines that a comparison composition is
    appropriate.

    The renderer performs measurement first and drawing second.
    """

    # ----------------------------------------------------------------------
    # Public API
    # ----------------------------------------------------------------------

    def render(
        self,
        post_dict: dict,
        brand: BrandStyle,
        fonts: dict,
        draw,
        img,
    ) -> bytes:
        """
        Measure and render a comparison card.

        Parameters
        ----------
        post_dict:
            Normalized post payload.

        brand:
            BrandStyle instance controlling visual identity.

        fonts:
            Pre-loaded fonts keyed by weight and scaled CSS size.

        draw:
            Pillow ImageDraw instance.

        img:
            Pillow Image instance.

        Returns
        -------
        bytes
            Final PNG bytes.
        """

        S = SCALE
        W = CARD_W
        H = CARD_H

        PAD = brand.scaled_padding(S)
        TW = max(1, W - PAD * 2)

        # ------------------------------------------------------------------
        # Content normalization
        # ------------------------------------------------------------------

        content = self._extract_content(post_dict, brand)

        title = content["title"]
        opt_a = content["option_a"]
        opt_b = content["option_b"]
        pts_a = content["points_a"]
        pts_b = content["points_b"]
        winner_raw = content["winner"]
        hashtags = content["hashtags"]
        brand_name = content["brand_name"]

        # ------------------------------------------------------------------
        # Fixed structural areas
        # ------------------------------------------------------------------

        footer_top = H - FOOTER_HEIGHT

        # Header
        self._draw_brand_header(
            draw=draw,
            fonts=fonts,
            brand=brand,
            brand_name=brand_name,
            pad=PAD,
            width=W,
        )

        # Rule
        draw.line(
            [(PAD, RULE_Y_TOP), (W - PAD, RULE_Y_TOP)],
            fill=brand.rule_clr,
            width=max(1, 2 * S),
        )

        # Content badge
        badge_bottom = self._draw_content_badge(
            draw=draw,
            fonts=fonts,
            brand=brand,
            pad=PAD,
        )

        # ------------------------------------------------------------------
        # Winner parsing
        # ------------------------------------------------------------------

        winner_measurement = self._measure_winner(
            draw=draw,
            fonts=fonts,
            winner_raw=winner_raw,
            option_a=opt_a,
            option_b=opt_b,
            brand=brand,
            available_width=TW,
        )

        # ------------------------------------------------------------------
        # Title measurement
        # ------------------------------------------------------------------

        # Reserve realistic space for lower sections before measuring title.
        reserved_lower = (
            footer_top
            - badge_bottom
            - TITLE_BOTTOM_GAP
            - COLUMN_TOP_GAP
            - COLUMN_WINNER_GAP
            - (winner_measurement.height if winner_measurement.name else 0)
            - (HASHTAG_TOP_GAP + self._estimate_hashtag_height(
                draw, fonts, hashtags, brand, TW
            ) if hashtags else 0)
            - CONTENT_BOTTOM_MARGIN
        )

        title_start_y = badge_bottom + TITLE_TOP_GAP

        title_measurement = self._measure_title(
            draw=draw,
            fonts=fonts,
            title=title,
            brand=brand,
            max_width=TW,
            max_height=max(
                100 * S,
                reserved_lower - title_start_y,
            ),
        )

        # Draw title.
        title_bottom = self._draw_title(
            draw=draw,
            measurement=title_measurement,
            brand=brand,
            pad=PAD,
            start_y=title_start_y,
        )

        # ------------------------------------------------------------------
        # Column geometry
        # ------------------------------------------------------------------

        column_top = title_bottom + COLUMN_TOP_GAP

        hashtag_height = (
            self._estimate_hashtag_height(
                draw, fonts, hashtags, brand, TW
            )
            if hashtags
            else 0
        )

        reserved_after_columns = (
            COLUMN_WINNER_GAP
            + (winner_measurement.height if winner_measurement.name else 0)
            + (
                HASHTAG_TOP_GAP + hashtag_height
                if hashtags
                else 0
            )
            + CONTENT_BOTTOM_MARGIN
        )

        available_column_height = (
            footer_top
            - column_top
            - reserved_after_columns
        )

        # Never create a column smaller than the minimum usable area.
        column_height = max(
            360 * S,
            available_column_height,
        )

        # If the title consumed too much space, tighten it before allowing
        # the column to push into the footer.
        if available_column_height < 360 * S:
            title_measurement = self._measure_title(
                draw=draw,
                fonts=fonts,
                title=title,
                brand=brand,
                max_width=TW,
                max_height=max(80 * S, title_measurement.height - 30 * S),
            )

            # Clear/re-rendering is intentionally avoided here because this
            # branch is expected only when the upstream content is unusually
            # dense. The higher-level renderer should normally provide a
            # clean canvas. We therefore log the condition for diagnostics.
            logger.debug(
                "Comparison card entered compressed title geometry: "
                "title_height=%d available_columns=%d",
                title_measurement.height,
                available_column_height,
            )

        # ------------------------------------------------------------------
        # Column measurements
        # ------------------------------------------------------------------

        column_measure_a = self._measure_column(
            draw=draw,
            fonts=fonts,
            bullets=pts_a,
            brand=brand,
            column_width=self._column_width(TW),
            column_height=column_height,
        )

        column_measure_b = self._measure_column(
            draw=draw,
            fonts=fonts,
            bullets=pts_b,
            brand=brand,
            column_width=self._column_width(TW),
            column_height=column_height,
        )

        # If one side needs smaller text, use the smaller of the two font
        # sizes so both columns remain typographically balanced.
        shared_bullet_font_size = min(
            column_measure_a.font_size,
            column_measure_b.font_size,
        )

        column_measure_a = self._measure_column(
            draw=draw,
            fonts=fonts,
            bullets=pts_a,
            brand=brand,
            column_width=self._column_width(TW),
            column_height=column_height,
            forced_font_size=shared_bullet_font_size,
        )

        column_measure_b = self._measure_column(
            draw=draw,
            fonts=fonts,
            bullets=pts_b,
            brand=brand,
            column_width=self._column_width(TW),
            column_height=column_height,
            forced_font_size=shared_bullet_font_size,
        )

        # ------------------------------------------------------------------
        # Draw columns
        # ------------------------------------------------------------------

        self._draw_columns(
            draw=draw,
            fonts=fonts,
            opt_a=opt_a,
            opt_b=opt_b,
            pts_a=pts_a,
            pts_b=pts_b,
            measure_a=column_measure_a,
            measure_b=column_measure_b,
            brand=brand,
            pad=PAD,
            width=W,
            total_width=TW,
            top=column_top,
            height=column_height,
        )

        # ------------------------------------------------------------------
        # Winner
        # ------------------------------------------------------------------

        winner_top = column_top + column_height + COLUMN_WINNER_GAP

        if winner_measurement.name:
            # Protect footer.
            winner_top = min(
                winner_top,
                footer_top
                - winner_measurement.height
                - (
                    HASHTAG_TOP_GAP + hashtag_height
                    if hashtags
                    else 0
                )
                - CONTENT_BOTTOM_MARGIN,
            )

            self._draw_winner(
                draw=draw,
                measurement=winner_measurement,
                brand=brand,
                pad=PAD,
                width=W,
                top=winner_top,
            )

        # ------------------------------------------------------------------
        # Hashtags
        # ------------------------------------------------------------------

        if hashtags and brand.max_hashtags > 0:
            hashtag_top = (
                winner_top
                + winner_measurement.height
                + HASHTAG_TOP_GAP
                if winner_measurement.name
                else column_top + column_height + HASHTAG_TOP_GAP
            )

            self._draw_hashtags(
                draw=draw,
                fonts=fonts,
                hashtags=hashtags,
                brand=brand,
                pad=PAD,
                width=TW,
                top=hashtag_top,
                bottom=footer_top,
            )

        # ------------------------------------------------------------------
        # Footer
        # ------------------------------------------------------------------

        self._draw_footer(
            draw=draw,
            fonts=fonts,
            brand=brand,
            pad=PAD,
            width=W,
            height=H,
        )

        return export_png(img)

    # ======================================================================
    # Content
    # ======================================================================

    def _extract_content(
        self,
        post_dict: dict,
        brand: BrandStyle,
    ) -> dict:
        """Normalize post content into renderer-safe primitives."""

        title = str(post_dict.get("title") or "").strip()

        option_a = str(post_dict.get("option_a") or "").strip()
        option_b = str(post_dict.get("option_b") or "").strip()

        points_a = self._normalize_bullets(
            post_dict.get("points_a") or []
        )
        points_b = self._normalize_bullets(
            post_dict.get("points_b") or []
        )

        winner = str(
            post_dict.get("winner")
            or post_dict.get("verdict")
            or ""
        ).strip()

        raw_hashtags = (
            post_dict.get("brand_hashtags")
            or post_dict.get("hashtags")
            or ""
        )

        hashtags = normalize_hashtags(raw_hashtags)

        brand_name = str(
            getattr(brand, "brand_name", None)
            or post_dict.get("brand_name")
            or "Kanyoza Systems"
        ).strip()

        # Clean option names for winner parsing only.
        # Display keeps the original name.
        return {
            "title": title,
            "option_a": option_a,
            "option_b": option_b,
            "points_a": points_a,
            "points_b": points_b,
            "winner": winner,
            "hashtags": hashtags,
            "brand_name": brand_name.upper(),
        }

    @staticmethod
    def _normalize_bullets(value: Any) -> list[str]:
        """Normalize arbitrary bullet input into clean strings."""

        if isinstance(value, str):
            values = [value]
        elif isinstance(value, Iterable):
            values = list(value)
        else:
            values = []

        result: list[str] = []

        for item in values:
            if item is None:
                continue

            text = str(item).strip()

            if not text:
                continue

            # Keep bullet semantics concise while avoiding hard loss of
            # content at the layout layer.
            try:
                text = shorten_bullet(text, 8)
            except Exception:
                pass

            if text:
                result.append(text)

        return result

    # ======================================================================
    # Header
    # ======================================================================

    def _draw_brand_header(
        self,
        draw,
        fonts,
        brand,
        brand_name: str,
        pad: int,
        width: int,
    ) -> None:
        """Draw brand identity only."""

        font_brand = self._font(
            fonts,
            "bold",
            17,
        )

        if not font_brand:
            return

        text = truncate_chars(brand_name, 34)

        draw.text(
            (pad, TOP_BAR_Y),
            text,
            font=font_brand,
            fill=brand.txt_muted,
        )

    # ======================================================================
    # Badge
    # ======================================================================

    def _draw_content_badge(
        self,
        draw,
        fonts,
        brand,
        pad: int,
    ) -> int:
        """Draw the single comparison content badge."""

        label = "COMPARISON"

        font = self._font(
            fonts,
            "bold",
            12,
        )

        if not font:
            return RULE_Y_TOP + BADGE_TOP_GAP

        bbox = draw.textbbox(
            (0, 0),
            label,
            font=font,
        )

        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        badge_w = text_w + 48 * SCALE
        badge_h = text_h + 24 * SCALE

        y = RULE_Y_TOP + BADGE_TOP_GAP

        draw.rounded_rectangle(
            [
                pad,
                y,
                pad + badge_w,
                y + badge_h,
            ],
            radius=8 * SCALE,
            fill=brand.primary,
        )

        draw.text(
            (
                pad + 24 * SCALE,
                y + 12 * SCALE,
            ),
            label,
            font=font,
            fill=brand.header_text,
        )

        return y + badge_h + BADGE_BOTTOM_GAP

    # ======================================================================
    # Fonts
    # ======================================================================

    def _font(
        self,
        fonts: dict,
        weight: str,
        size_css: int,
    ):
        """
        Retrieve a pre-loaded font.

        Supports both:
            fonts["bold"][scaled_size]
            fonts["regular"][scaled_size]
        """

        try:
            bucket = fonts.get(weight, {})
            scaled_key = int(size_css * SCALE)

            font = bucket.get(scaled_key)

            if font is not None:
                return font

            # Graceful fallback if the font loader uses CSS-sized keys.
            font = bucket.get(size_css)

            if font is not None:
                return font

        except Exception:
            logger.debug(
                "Font lookup failed: weight=%s size=%s",
                weight,
                size_css,
                exc_info=True,
            )

        return None

    # ======================================================================
    # Title measurement
    # ======================================================================

    def _measure_title(
        self,
        draw,
        fonts,
        title: str,
        brand,
        max_width: int,
        max_height: int,
    ) -> _TitleMeasurement:
        """Find the largest title font that fits width and height."""

        result = _TitleMeasurement()

        if not title:
            return result

        char_count = len(title)

        if char_count <= 32:
            sizes = (40, 38, 36, 34, 32, 30, 28, 26, 24, 22)
        elif char_count <= 55:
            sizes = (36, 34, 32, 30, 28, 26, 24, 22)
        elif char_count <= 80:
            sizes = (32, 30, 28, 26, 24, 22)
        else:
            sizes = (30, 28, 26, 24, 22)

        for size in sizes:
            font = self._font(
                fonts,
                "bold",
                size,
            )

            if not font:
                continue

            lines = wrap_text(
                draw,
                title,
                font,
                max_width,
            )

            # Comparison titles should stay visually dominant but should not
            # become paragraph blocks.
            if len(lines) > 2:
                continue

            height = self._multiline_height(
                draw,
                lines,
                font,
                TITLE_LINE_GAP,
            )

            if height <= max_height:
                result.font_size = size
                result.font = font
                result.lines = lines
                result.height = height
                return result

        # Final fallback.
        font = self._font(
            fonts,
            "bold",
            22,
        )

        if font:
            shortened = truncate_chars(title, 90)

            lines = wrap_text(
                draw,
                shortened,
                font,
                max_width,
            )[:2]

            result.font_size = 22
            result.font = font
            result.lines = lines
            result.height = self._multiline_height(
                draw,
                lines,
                font,
                TITLE_LINE_GAP,
            )

        return result

    def _draw_title(
        self,
        draw,
        measurement: _TitleMeasurement,
        brand,
        pad: int,
        start_y: int,
    ) -> int:
        """Draw a previously measured title."""

        if not measurement.font or not measurement.lines:
            return start_y

        y = start_y

        for line in measurement.lines:
            draw.text(
                (pad, y),
                line,
                font=measurement.font,
                fill=brand.txt_main,
            )

            y += (
                text_height(
                    draw,
                    line,
                    measurement.font,
                )
                + TITLE_LINE_GAP
            )

        return y + TITLE_BOTTOM_GAP

    # ======================================================================
    # Column geometry
    # ======================================================================

    @staticmethod
    def _column_width(total_width: int) -> int:
        gap = VS_DIAMETER
        return max(
            1,
            (total_width - gap) // 2,
        )

    # ======================================================================
    # Column measurement
    # ======================================================================

    def _measure_column(
        self,
        draw,
        fonts,
        bullets: Sequence[str],
        brand,
        column_width: int,
        column_height: int,
        forced_font_size: Optional[int] = None,
    ) -> _ColumnMeasurement:
        """
        Measure every bullet and choose a font that fits the entire column.

        Unlike simple line-count fitting, this measures total vertical
        consumption.
        """

        result = _ColumnMeasurement()

        inner_width = (
            column_width
            - COLUMN_INNER_PAD * 2
            - 36 * SCALE
        )

        available_height = (
            column_height
            - COLUMN_HEADER_H
            - COLUMN_INNER_PAD
        )

        if forced_font_size is not None:
            candidates = (forced_font_size,)
        else:
            count = len(bullets)

            if count <= 3:
                start = 18
            elif count <= 5:
                start = 17
            elif count <= 7:
                start = 15
            else:
                start = 14

            candidates = tuple(
                range(start, 10, -1)
            )

        for font_size in candidates:
            font = self._font(
                fonts,
                "regular",
                font_size,
            )

            if not font:
                continue

            bullet_lines: list[list[str]] = []
            total_height = 0
            fits = True

            for bullet in bullets:
                lines = wrap_text(
                    draw,
                    bullet[:160],
                    font,
                    inner_width,
                )

                # Avoid pathological paragraphs.
                if len(lines) > 3:
                    fits = False
                    break

                bullet_height = self._multiline_height(
                    draw,
                    lines,
                    font,
                    BULLET_LINE_GAP,
                )

                total_height += (
                    bullet_height
                    + BULLET_GAP
                )

                bullet_lines.append(lines)

            if total_height <= available_height:
                result.font_size = font_size
                result.font = font
                result.bullets = list(bullets)
                result.bullet_lines = bullet_lines
                result.height = total_height
                result.fits = fits
                return result

        # Last-resort measurement.
        fallback_size = 11

        font = self._font(
            fonts,
            "regular",
            fallback_size,
        )

        if font:
            bullet_lines = [
                wrap_text(
                    draw,
                    bullet[:160],
                    font,
                    inner_width,
                )[:3]
                for bullet in bullets
            ]

            total_height = sum(
                self._multiline_height(
                    draw,
                    lines,
                    font,
                    BULLET_LINE_GAP,
                )
                + BULLET_GAP
                for lines in bullet_lines
            )

            result.font_size = fallback_size
            result.font = font
            result.bullets = list(bullets)
            result.bullet_lines = bullet_lines
            result.height = total_height
            result.fits = False

        return result

    # ======================================================================
    # Columns
    # ======================================================================

    def _draw_columns(
        self,
        draw,
        fonts,
        opt_a: str,
        opt_b: str,
        pts_a: list[str],
        pts_b: list[str],
        measure_a: _ColumnMeasurement,
        measure_b: _ColumnMeasurement,
        brand,
        pad: int,
        width: int,
        total_width: int,
        top: int,
        height: int,
    ) -> None:
        """Draw both columns and their VS separator."""

        gap = VS_DIAMETER
        col_w = self._column_width(total_width)

        x_a = pad
        x_b = pad + col_w + gap

        self._draw_one_column(
            draw=draw,
            fonts=fonts,
            x=x_a,
            top=top,
            width=col_w,
            height=height,
            name=opt_a,
            measurement=measure_a,
            brand=brand,
            color=brand.primary,
            label="STRENGTHS",
        )

        self._draw_one_column(
            draw=draw,
            fonts=fonts,
            x=x_b,
            top=top,
            width=col_w,
            height=height,
            name=opt_b,
            measurement=measure_b,
            brand=brand,
            color=brand.secondary,
            label="STRENGTHS",
        )

        self._draw_vs(
            draw=draw,
            fonts=fonts,
            brand=brand,
            center_x=pad + col_w + gap // 2,
            center_y=top + height // 2,
        )

    def _draw_one_column(
        self,
        draw,
        fonts,
        x: int,
        top: int,
        width: int,
        height: int,
        name: str,
        measurement: _ColumnMeasurement,
        brand,
        color,
        label: str,
    ) -> None:
        """Draw one measured comparison column."""

        radius = brand.scaled_radius(SCALE)

        # Body.
        draw.rounded_rectangle(
            [
                x,
                top,
                x + width,
                top + height,
            ],
            radius=radius,
            fill=brand.col_bg,
            outline=color,
            width=4 * SCALE,
        )

        # Header.
        draw.rounded_rectangle(
            [
                x,
                top,
                x + width,
                top + COLUMN_HEADER_H,
            ],
            radius=radius,
            fill=color,
        )

        # Flatten bottom half of rounded header.
        draw.rectangle(
            [
                x,
                top + COLUMN_HEADER_H // 2,
                x + width,
                top + COLUMN_HEADER_H,
            ],
            fill=color,
        )

        # Strength label.
        font_label = self._font(
            fonts,
            "bold",
            10,
        )

        if font_label:
            draw.text(
                (
                    x + COLUMN_INNER_PAD,
                    top + 14 * SCALE,
                ),
                label,
                font=font_label,
                fill=brand.header_text,
            )

        # Option name.
        self._draw_option_name(
            draw=draw,
            fonts=fonts,
            name=name,
            brand=brand,
            x=x,
            top=top,
            width=width,
        )

        # Bullets.
        if measurement.font:
            self._draw_bullets(
                draw=draw,
                measurement=measurement,
                brand=brand,
                x=x,
                top=top,
                width=width,
                height=height,
                color=color,
            )

    # ======================================================================
    # Option name
    # ======================================================================

    def _draw_option_name(
        self,
        draw,
        fonts,
        name: str,
        brand,
        x: int,
        top: int,
        width: int,
    ) -> None:
        """Fit option name into the fixed column header."""

        if not name:
            name = "Option"

        max_width = (
            width
            - COLUMN_INNER_PAD * 2
        )

        # Leave enough room for the strength label.
        available_header_height = (
            COLUMN_HEADER_H
            - 42 * SCALE
        )

        candidates = (
            22,
            20,
            18,
            16,
            14,
        )

        for size in candidates:
            font = self._font(
                fonts,
                "bold",
                size,
            )

            if not font:
                continue

            lines = wrap_text(
                draw,
                name,
                font,
                max_width,
            )

            if len(lines) > 2:
                continue

            height = self._multiline_height(
                draw,
                lines,
                font,
                4 * SCALE,
            )

            if height <= available_header_height:
                start_y = (
                    top
                    + COLUMN_HEADER_H
                    - 14 * SCALE
                    - height
                )

                y = start_y

                for line in lines:
                    draw.text(
                        (
                            x + COLUMN_INNER_PAD,
                            y,
                        ),
                        line,
                        font=font,
                        fill=brand.header_text,
                    )

                    y += (
                        text_height(
                            draw,
                            line,
                            font,
                        )
                        + 4 * SCALE
                    )

                return

        # Last resort.
        font = self._font(
            fonts,
            "bold",
            14,
        )

        if font:
            fallback = truncate_chars(name, 24)

            draw.text(
                (
                    x + COLUMN_INNER_PAD,
                    top
                    + COLUMN_HEADER_H
                    - text_height(
                        draw,
                        fallback,
                        font,
                    )
                    - 14 * SCALE,
                ),
                fallback,
                font=font,
                fill=brand.header_text,
            )

    # ======================================================================
    # Bullets
    # ======================================================================

    def _draw_bullets(
        self,
        draw,
        measurement: _ColumnMeasurement,
        brand,
        x: int,
        top: int,
        width: int,
        height: int,
        color,
    ) -> None:
        """Draw bullets using previously measured line breaks."""

        inner_pad = COLUMN_INNER_PAD

        text_x = (
            x
            + inner_pad
            + 32 * SCALE
        )

        bullet_x = (
            x
            + inner_pad
            + 10
        )

        y = (
            top
            + COLUMN_HEADER_H
            + inner_pad
        )

        clamp_bottom = (
            top
            + height
            - inner_pad
        )

        for lines in measurement.bullet_lines:
            if not lines:
                continue

            bullet_height = self._multiline_height(
                draw,
                lines,
                measurement.font,
                BULLET_LINE_GAP,
            )

            if y + bullet_height > clamp_bottom:
                logger.warning(
                    "Comparison bullet skipped because it exceeded "
                    "column geometry."
                )
                break

            dot_y = y + bullet_height // 2

            draw.ellipse(
                [
                    bullet_x - BULLET_DOT_RADIUS,
                    dot_y - BULLET_DOT_RADIUS,
                    bullet_x + BULLET_DOT_RADIUS,
                    dot_y + BULLET_DOT_RADIUS,
                ],
                fill=color,
            )

            line_y = y

            for line in lines:
                draw.text(
                    (
                        text_x,
                        line_y,
                    ),
                    line,
                    font=measurement.font,
                    fill=brand.txt_main,
                )

                line_y += (
                    text_height(
                        draw,
                        line,
                        measurement.font,
                    )
                    + BULLET_LINE_GAP
                )

            y += (
                bullet_height
                + BULLET_GAP
            )

    # ======================================================================
    # VS
    # ======================================================================

    def _draw_vs(
        self,
        draw,
        fonts,
        brand,
        center_x: int,
        center_y: int,
    ) -> None:
        """Draw the VS separator."""

        radius = VS_DIAMETER // 2 - 2 * SCALE

        draw.ellipse(
            [
                center_x - radius,
                center_y - radius,
                center_x + radius,
                center_y + radius,
            ],
            fill=brand.col_bg,
            outline=brand.primary,
            width=3 * SCALE,
        )

        label = str(
            getattr(brand, "vs_label", None)
            or "VS"
        ).strip()

        if len(label) > 3:
            label = "VS"

        label = label.upper()

        font = self._font(
            fonts,
            "bold",
            14,
        )

        if not font:
            return

        tw = text_width(
            draw,
            label,
            font,
        )

        th = text_height(
            draw,
            label,
            font,
        )

        draw.text(
            (
                center_x - tw // 2,
                center_y - th // 2,
            ),
            label,
            font=font,
            fill=brand.accent,
        )

    # ======================================================================
    # Winner measurement
    # ======================================================================

    def _measure_winner(
        self,
        draw,
        fonts,
        winner_raw: str,
        option_a: str,
        option_b: str,
        brand,
        available_width: int,
    ) -> _WinnerMeasurement:
        """Measure winner name, reason and banner height."""

        result = _WinnerMeasurement()

        if not winner_raw:
            return result

        clean_a = (
            option_a.split(":", 1)[0].strip()
            if ":" in option_a
            else option_a
        )

        clean_b = (
            option_b.split(":", 1)[0].strip()
            if ":" in option_b
            else option_b
        )

        try:
            winner_name, reason = parse_winner(
                winner_raw,
                clean_a,
                clean_b,
            )
        except Exception:
            winner_name = ""
            reason = winner_raw

        winner_name = str(
            winner_name or ""
        ).strip()

        reason = str(
            reason or winner_raw
        ).strip()

        if not winner_name:
            return result

        result.name = winner_name
        result.reason = reason

        result.label_font = self._font(
            fonts,
            "bold",
            10,
        )

        result.name_font = self._font(
            fonts,
            "bold",
            22,
        )

        result.reason_font = self._font(
            fonts,
            "regular",
            14,
        )

        if not result.name_font:
            return result

        result.name_text = (
            "› "
            + truncate_chars(
                winner_name,
                30,
            )
        )

        result.name_width = text_width(
            draw,
            result.name_text,
            result.name_font,
        )

        side_space = (
            WINNER_SIDE_PAD * 2
            + 24 * SCALE
        )

        label_width = (
            text_width(
                draw,
                str(
                    getattr(
                        brand,
                        "winner_label",
                        "WINNER",
                    )
                ),
                result.label_font,
            )
            if result.label_font
            else 0
        )

        same_line_available = (
            available_width
            - side_space
            - label_width
            - 16 * SCALE
            - result.name_width
            - 20 * SCALE
        )

        if (
            result.reason_font
            and same_line_available > 160 * SCALE
        ):
            same_line = wrap_text(
                draw,
                reason,
                result.reason_font,
                same_line_available,
            )

            if len(same_line) == 1:
                result.same_line = True
                result.reason_lines = same_line
                result.height = WINNER_MIN_H
                return result

        # Two-line winner.
        reason_width = max(
            200 * SCALE,
            available_width
            - WINNER_SIDE_PAD * 2,
        )

        result.reason_lines = wrap_text(
            draw,
            reason,
            result.reason_font,
            reason_width,
        )[:2] if result.reason_font else []

        reason_lines_height = (
            self._multiline_height(
                draw,
                result.reason_lines,
                result.reason_font,
                4 * SCALE,
            )
            if result.reason_font and result.reason_lines
            else 0
        )

        result.same_line = False

        result.height = max(
            WINNER_MIN_H,
            min(
                WINNER_MAX_H,
                (
                    18 * SCALE
                    + max(
                        text_height(
                            draw,
                            result.name_text,
                            result.name_font,
                        ),
                        24 * SCALE,
                    )
                    + 12 * SCALE
                    + reason_lines_height
                    + WINNER_BOTTOM_PAD
                ),
            ),
        )

        return result

    # ======================================================================
    # Winner drawing
    # ======================================================================

    def _draw_winner(
        self,
        draw,
        measurement: _WinnerMeasurement,
        brand,
        pad: int,
        width: int,
        top: int,
    ) -> None:
        """Draw measured winner banner."""

        if not measurement.name:
            return

        bottom = top + measurement.height

        draw_horizontal_gradient(
            draw,
            top,
            bottom,
            brand.primary,
            brand.secondary,
            pad,
            width - pad,
        )

        draw.rounded_rectangle(
            [
                pad,
                top,
                width - pad,
                bottom,
            ],
            radius=14 * SCALE,
            outline=brand.primary,
            width=2 * SCALE,
        )

        label = str(
            getattr(
                brand,
                "winner_label",
                "WINNER",
            )
        ).upper()

        label_font = measurement.label_font

        if label_font:
            draw.text(
                (
                    pad + WINNER_SIDE_PAD,
                    top + 14 * SCALE,
                ),
                label,
                font=label_font,
                fill=brand.header_text,
            )

        name_x = (
            pad
            + WINNER_SIDE_PAD
        )

        name_y = top + 36 * SCALE

        # Same-line layout:
        # WINNER
        # › Winner — reason
        if measurement.same_line:
            label_width = (
                text_width(
                    draw,
                    label,
                    label_font,
                )
                if label_font
                else 0
            )

            name_x = (
                pad
                + WINNER_SIDE_PAD
                + label_width
                + 16 * SCALE
            )

            name_y = top + 14 * SCALE

            draw.text(
                (
                    name_x,
                    name_y,
                ),
                measurement.name_text,
                font=measurement.name_font,
                fill=brand.header_text,
            )

            reason_x = (
                name_x
                + measurement.name_width
                + 20 * SCALE
            )

            if measurement.reason_lines:
                draw.text(
                    (
                        reason_x,
                        top + 19 * SCALE,
                    ),
                    measurement.reason_lines[0],
                    font=measurement.reason_font,
                    fill=brand.header_text,
                )

            return

        # Two-line layout.
        draw.text(
            (
                name_x,
                name_y,
            ),
            measurement.name_text,
            font=measurement.name_font,
            fill=brand.header_text,
        )

        reason_y = (
            top
            + 68 * SCALE
        )

        for line in measurement.reason_lines[:2]:
            draw.text(
                (
                    pad + WINNER_SIDE_PAD,
                    reason_y,
                ),
                line,
                font=measurement.reason_font,
                fill=brand.header_text,
            )

            reason_y += (
                text_height(
                    draw,
                    line,
                    measurement.reason_font,
                )
                + 4 * SCALE
            )

    # ======================================================================
    # Hashtags
    # ======================================================================

    def _estimate_hashtag_height(
        self,
        draw,
        fonts,
        hashtags: str,
        brand,
        width: int,
    ) -> int:
        """Measure hashtag region before rendering."""

        if not hashtags:
            return 0

        font = self._font(
            fonts,
            "regular",
            13,
        )

        if not font:
            return 0

        lines = wrap_text(
            draw,
            hashtags,
            font,
            width,
        )[:2]

        if not lines:
            return 0

        return self._multiline_height(
            draw,
            lines,
            font,
            HASHTAG_LINE_GAP,
        )

    def _draw_hashtags(
        self,
        draw,
        fonts,
        hashtags: str,
        brand,
        pad: int,
        width: int,
        top: int,
        bottom: int,
    ) -> None:
        """Draw hashtags in their own reserved region."""

        font = self._font(
            fonts,
            "regular",
            13,
        )

        if not font:
            return

        lines = wrap_text(
            draw,
            hashtags,
            font,
            width,
        )[:2]

        y = top

        for line in lines:
            line_h = text_height(
                draw,
                line,
                font,
            )

            if y + line_h > bottom:
                break

            draw.text(
                (
                    pad,
                    y,
                ),
                line,
                font=font,
                fill=brand.txt_muted,
            )

            y += (
                line_h
                + HASHTAG_LINE_GAP
            )

    # ======================================================================
    # Footer
    # ======================================================================

    def _draw_footer(
        self,
        draw,
        fonts,
        brand,
        pad: int,
        width: int,
        height: int,
    ) -> None:
        """Draw footer completely independently from content."""

        footer_y = (
            height
            - FOOTER_HEIGHT
        )

        draw.line(
            [
                (pad, footer_y),
                (width - pad, footer_y),
            ],
            fill=brand.rule_clr,
            width=max(1, 2 * SCALE),
        )

        font = self._font(
            fonts,
            "regular",
            13,
        )

        if not font:
            return

        footer_label = str(
            getattr(
                brand,
                "footer_label",
                "Powered by Kanyoza AI",
            )
            or ""
        )

        draw.text(
            (
                pad,
                footer_y + FOOTER_TEXT_TOP,
            ),
            footer_label,
            font=font,
            fill=brand.txt_muted,
        )

        if getattr(
            brand,
            "show_date",
            True,
        ):
            date_str = datetime.now().strftime(
                "%b %d, %Y"
            )

            date_width = text_width(
                draw,
                date_str,
                font,
            )

            draw.text(
                (
                    width - pad - date_width,
                    footer_y + FOOTER_TEXT_TOP,
                ),
                date_str,
                font=font,
                fill=brand.txt_muted,
            )

    # ======================================================================
    # Utility
    # ======================================================================

    @staticmethod
    def _multiline_height(
        draw,
        lines: Sequence[str],
        font,
        line_gap: int,
    ) -> int:
        """Return exact measured height for multiple lines."""

        if not lines or not font:
            return 0

        total = 0

        for line in lines:
            total += text_height(
                draw,
                line,
                font,
            )

        total += line_gap * max(
            0,
            len(lines) - 1,
        )

        return total
