"""
Quote layout strategy — hero typography card for scripture, testimony, quotes.

Large quotation mark, generous whitespace, optional attribution.
Designed for church verses, inspirational quotes, personal insights.

Structure:
  [Brand name]                    [Topic badge]
  ───────────────────────────────────────────
  [Large decorative quote mark]
  [Quote text — large, centered, serif-friendly]
  [Attribution line]
  [Optional reflection / context]
  ───────────────────────────────────────────
  Footer                          Date
"""

import logging
from datetime import datetime
from typing import Optional

from .brand_style import BrandStyle
from .render_utils import (
    SCALE, CARD_W, CARD_H,
    text_width, text_height, wrap_text,
    export_png,
    normalize_hashtags,
)
from services.rendering.typography import truncate_chars

logger = logging.getLogger(__name__)

RULE_Y_TOP = 120 * SCALE
TOP_BAR_Y = 44 * SCALE
BADGE_Y = 36 * SCALE


class QuoteLayout:
    """
    Hero typography card for quotes, scripture, and inspiration.

    Uses larger fonts, centered layout, and decorative elements.
    Ministry brands get serif-friendly rendering.
    """

    def render(
        self,
        post_dict: dict,
        brand: BrandStyle,
        fonts: dict,
        draw,
        img,
    ) -> bytes:
        S = SCALE
        W = CARD_W
        H = CARD_H
        PAD = brand.scaled_padding(S)
        TW = W - PAD * 2

        # ── Extract content ──────────────────────────────────────
        quote_text = (post_dict.get("title") or post_dict.get("quote") or "").strip()
        attribution = (post_dict.get("caption") or post_dict.get("attribution") or "").strip()
        context = (post_dict.get("body") or post_dict.get("context") or "").strip()
        hashtags = normalize_hashtags(
            post_dict.get("brand_hashtags") or post_dict.get("hashtags") or ""
        )
        topic = (
            post_dict.get("topic") or post_dict.get("theme", "quote")
        ).replace("_", " ").upper()[:28]
        brand_name = brand.brand_name.upper()

        # ── 1. Brand name ────────────────────────────────────────
        font_brand = self._font(fonts, "bold", 17)
        draw.text(
            (PAD, TOP_BAR_Y), brand_name, font=font_brand, fill=brand.txt_muted,
        )

        # ── 2. Topic badge ───────────────────────────────────────
        font_badge = self._font(fonts, "bold", 13)
        bb = draw.textbbox((0, 0), topic, font=font_badge)
        bw = bb[2] - bb[0] + 56 * S
        bh = bb[3] - bb[1] + 28 * S
        badge_x = W - PAD - bw
        draw.rounded_rectangle(
            [badge_x, BADGE_Y, badge_x + bw, BADGE_Y + bh],
            radius=10 * S, fill=brand.badge_fill,
        )
        draw.text(
            (badge_x + 28 * S, BADGE_Y + 14 * S),
            topic, font=font_badge, fill=brand.badge_text,
        )

        # ── 3. Rule ──────────────────────────────────────────────
        draw.line(
            [(PAD, RULE_Y_TOP), (W - PAD, RULE_Y_TOP)],
            fill=brand.rule_clr, width=2,
        )

        # ── 4. Content type badge ────────────────────────────────
        ctype = post_dict.get("content_type", "QUOTE").upper()[:20]
        font_cb = self._font(fonts, "bold", 12)
        bb = draw.textbbox((0, 0), ctype, font=font_cb)
        pw = bb[2] - bb[0] + 48 * S
        ph = bb[3] - bb[1] + 24 * S
        cb_y = RULE_Y_TOP + 28 * S
        draw.rounded_rectangle(
            [PAD, cb_y, PAD + pw, cb_y + ph],
            radius=8 * S, fill=brand.primary,
        )
        draw.text(
            (PAD + 24 * S, cb_y + 12 * S),
            ctype, font=font_cb, fill=brand.header_text,
        )

        # ── 5. Decorative quote mark ─────────────────────────────
        y = cb_y + ph + 32 * S
        quote_mark = "\u201c"  # Left double quotation mark
        font_mark = self._font(fonts, "bold", 80)
        mark_w = text_width(draw, quote_mark, font_mark)
        mark_x = (W - mark_w) // 2

        # Semi-transparent effect (draw at lower opacity by using muted color)
        draw.text(
            (mark_x, y - 20 * S),
            quote_mark,
            font=font_mark,
            fill=brand.txt_dim,
        )
        y += 40 * S

        # ── 6. Quote text ────────────────────────────────────────
        quote_w = TW - 120 * S  # Narrower for centered feel
        quote_x = PAD + 60 * S
        y = self._draw_quote_text(
            draw, fonts, quote_text, brand, quote_w, quote_x, y, H,
        )

        # ── 7. Attribution ───────────────────────────────────────
        if attribution:
            y += 16 * S
            font_attr = self._font(fonts, "bold", 18)
            draw.text(
                (quote_x, y),
                "\u2014 " + truncate_chars(attribution, 60),
                font=font_attr,
                fill=brand.accent,
            )
            y += text_height(draw, attribution, font_attr) + 8 * S

        # ── 8. Context/reflection ────────────────────────────────
        if context and y < H - 320 * S:
            y += 16 * S
            font_ctx = self._font(fonts, "regular", 14)
            ctx_lines = wrap_text(draw, truncate_chars(context, 200), font_ctx, quote_w)[:3]
            for line in ctx_lines:
                if y + text_height(draw, line, font_ctx) > H - 250 * S:
                    break
                draw.text(
                    (quote_x, y), line, font=font_ctx, fill=brand.txt_muted,
                )
                y += text_height(draw, line, font_ctx) + 10 * S

        # ── 9. Hashtags ──────────────────────────────────────────
        if hashtags and brand.max_hashtags > 0 and y < H - 200 * S:
            font_ht = self._font(fonts, "regular", 14)
            ht_lines = wrap_text(draw, hashtags, font_ht, TW)[:2]
            ht_y = min(y + 16 * S, H - 100 * S - 80 * S)
            for hl in ht_lines:
                if ht_y + 36 * S > H - 110 * S:
                    break
                draw.text((PAD, ht_y), hl, font=font_ht, fill=brand.accent)
                ht_y += 40 * S

        # ── 10. Footer ───────────────────────────────────────────
        footer_y = H - 100 * S
        draw.line(
            [(PAD, footer_y), (W - PAD, footer_y)],
            fill=brand.rule_clr, width=2,
        )
        font_footer = self._font(fonts, "regular", 14)
        draw.text(
            (PAD, footer_y + 14 * S),
            brand.footer_label,
            font=font_footer, fill=brand.txt_muted,
        )
        if brand.show_date:
            date_str = datetime.now().strftime("%b %d, %Y")
            dw = text_width(draw, date_str, font_footer)
            draw.text(
                (W - PAD - dw, footer_y + 14 * S),
                date_str, font=font_footer, fill=brand.txt_muted,
            )

        return export_png(img)

    def _font(self, fonts: dict, weight: str, size_css: int):
        key = size_css * SCALE
        return fonts[weight].get(key)

    def _draw_quote_text(self, draw, fonts, text, brand, max_w, x, y, H):
        """Draw quote text with generous sizing — finds largest fitting font."""
        max_quote_y = H - 300 * SCALE

        for font_size in (36, 32, 28, 24, 20):
            font = self._font(fonts, "bold", font_size)
            lines = wrap_text(draw, text, font, max_w, max_lines=8)
            total_h = sum(
                text_height(draw, l, font) + 16 * SCALE for l in lines
            )

            if y + total_h <= max_quote_y and len(lines) <= 8:
                for line in lines:
                    draw.text(
                        (x, y), line, font=font, fill=brand.txt_main,
                    )
                    y += text_height(draw, line, font) + 16 * SCALE
                return y

        # Fallback: smallest font
        font = self._font(fonts, "bold", 20)
        lines = wrap_text(draw, truncate_chars(text, 300), font, max_w)[:8]
        for line in lines:
            if y + text_height(draw, line, font) > max_quote_y:
                break
            draw.text((x, y), line, font=font, fill=brand.txt_main)
            y += text_height(draw, line, font) + 16 * SCALE
        return y
