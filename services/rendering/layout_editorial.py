"""
Editorial layout strategy — single-column narrative card.

For teaching, educational, and long-form content. No VS badge,
no competition framing. Clean typography with optional callout box.

Structure:
  [Brand name]                    [Topic badge]
  ───────────────────────────────────────────
  [Content type badge]
  [Title — large, font-fitted, up to 3 lines]
  [Body text — multi-paragraph, font-fitted]
  ┌─────────────────────────────────────────┐
  │  Optional callout / key insight         │
  └─────────────────────────────────────────┘
  #hashtags (optional)
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
from services.rendering.typography import truncate_chars, compress_paragraph

logger = logging.getLogger(__name__)

# Layout constants
RULE_Y_TOP = 120 * SCALE
TOP_BAR_Y = 44 * SCALE
BADGE_Y = 36 * SCALE
CONTENT_X_PAD = 80 * SCALE  # Wider padding for editorial feel


class EditorialLayout:
    """
    Single-column editorial card for narrative/educational content.

    Designed for church teachings, school lessons, NGO reports,
    and any content meant to be read rather than compared.
    """

    def render(
        self,
        post_dict: dict,
        brand: BrandStyle,
        fonts: dict,
        draw,
        img,
    ) -> bytes:
        """Measure and render an editorial card. Returns PNG bytes."""
        S = SCALE
        W = CARD_W
        H = CARD_H
        PAD = brand.scaled_padding(S)
        TW = W - PAD * 2
        CONTENT_W = TW - CONTENT_X_PAD  # Narrower column for readability

        # ── Extract content ──────────────────────────────────────
        title = (post_dict.get("title") or "").strip()
        caption = (post_dict.get("caption") or "").strip()
        body = (post_dict.get("body") or post_dict.get("content") or caption).strip()
        callout = (post_dict.get("callout") or post_dict.get("verdict") or "").strip()
        hashtags = normalize_hashtags(
            post_dict.get("brand_hashtags") or post_dict.get("hashtags") or ""
        )
        topic = (
            post_dict.get("topic") or post_dict.get("theme", "editorial")
        ).replace("_", " ").upper()[:28]
        brand_name = brand.brand_name.upper()

        # ── 1. Brand name ────────────────────────────────────────
        font_brand = self._font(fonts, "bold", 17)
        draw.text(
            (PAD, TOP_BAR_Y), brand_name, font=font_brand, fill=brand.txt_muted,
        )

        # ── 2. Topic badge ───────────────────────────────────────
        self._draw_topic_badge(draw, fonts, topic, brand, S, W, PAD)

        # ── 3. Rule ──────────────────────────────────────────────
        draw.line(
            [(PAD, RULE_Y_TOP), (W - PAD, RULE_Y_TOP)],
            fill=brand.rule_clr, width=2,
        )

        # ── 4. Content type badge ────────────────────────────────
        ctype = post_dict.get("content_type", "TEACHING").upper()[:20]
        y = self._draw_content_badge(draw, fonts, ctype, brand, S, PAD)

        # ── 5. Title (up to 3 lines for editorial) ───────────────
        y = self._draw_title(draw, fonts, title, brand, TW, PAD, y, max_lines=3)

        # ── 6. Divider ───────────────────────────────────────────
        y += 12 * S
        div_y = y
        draw.line(
            [(PAD + CONTENT_X_PAD, div_y), (W - PAD - CONTENT_X_PAD, div_y)],
            fill=brand.rule_clr, width=1,
        )
        y += 24 * S

        # ── 7. Body text ─────────────────────────────────────────
        if body:
            y = self._draw_body(draw, fonts, body, brand, CONTENT_W, PAD, y, H)

        # ── 8. Callout box (optional) ────────────────────────────
        if callout and y < H - 380 * S:
            y = self._draw_callout(draw, fonts, callout, brand, S, CONTENT_W, PAD, y, H)

        # ── 9. Hashtags ──────────────────────────────────────────
        if hashtags and brand.max_hashtags > 0 and y < H - 220 * S:
            self._draw_hashtags(draw, fonts, hashtags, brand, S, TW, PAD, H, y)

        # ── 10. Footer ───────────────────────────────────────────
        self._draw_footer(draw, fonts, brand, S, W, H, PAD)

        return export_png(img)

    # ── Helpers ───────────────────────────────────────────────────

    def _font(self, fonts: dict, weight: str, size_css: int):
        key = size_css * SCALE
        return fonts[weight].get(key)

    def _draw_topic_badge(self, draw, fonts, topic, brand, S, W, PAD):
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

    def _draw_content_badge(self, draw, fonts, label, brand, S, PAD):
        font_cb = self._font(fonts, "bold", 12)
        bb = draw.textbbox((0, 0), label, font=font_cb)
        pw = bb[2] - bb[0] + 48 * S
        ph = bb[3] - bb[1] + 24 * S
        cb_y = RULE_Y_TOP + 28 * S
        draw.rounded_rectangle(
            [PAD, cb_y, PAD + pw, cb_y + ph],
            radius=8 * S, fill=brand.primary,
        )
        draw.text(
            (PAD + 24 * S, cb_y + 12 * S),
            label, font=font_cb, fill=brand.header_text,
        )
        return cb_y + ph + 28 * S

    def _draw_title(self, draw, fonts, title, brand, TW, PAD, y, max_lines=3):
        """Draw title — editorial gets up to 3 lines."""
        if not title:
            return y

        for font_size in (54, 47, 40, 35, 32):
            font = self._font(fonts, "bold", font_size)
            lines = wrap_text(draw, title, font, TW)
            if len(lines) <= max_lines:
                for line in lines:
                    draw.text(
                        (PAD, y), line, font=font, fill=brand.txt_main,
                    )
                    y += text_height(draw, line, font) + 14 * SCALE
                return y

        # Fallback
        font = self._font(fonts, "bold", 32)
        lines = wrap_text(draw, truncate_chars(title, 120), font, TW)[:max_lines]
        for line in lines:
            draw.text((PAD, y), line, font=font, fill=brand.txt_main)
            y += text_height(draw, line, font) + 14 * SCALE
        return y

    def _draw_body(self, draw, fonts, body, brand, CONTENT_W, PAD, y, H):
        """Draw body text with font fitting. Stops if near bottom."""
        max_body_y = H - 300 * SCALE

        for font_size in (22, 20, 18, 16):
            font = self._font(fonts, "regular", font_size)
            lines = wrap_text(draw, body, font, CONTENT_W, max_lines=20)

            # Check if all lines fit
            total_h = sum(
                text_height(draw, l, font) + 12 * SCALE for l in lines
            )
            if y + total_h <= max_body_y and len(lines) <= 18:
                for line in lines:
                    if y + text_height(draw, line, font) > max_body_y:
                        break
                    draw.text(
                        (PAD + CONTENT_X_PAD, y),
                        line, font=font, fill=brand.txt_muted,
                    )
                    y += text_height(draw, line, font) + 12 * SCALE
                return y

        # Fallback: smallest font, truncated
        font = self._font(fonts, "regular", 16)
        lines = wrap_text(draw, body[:500], font, CONTENT_W, max_lines=18)
        for line in lines:
            if y + text_height(draw, line, font) > max_body_y:
                break
            draw.text(
                (PAD + CONTENT_X_PAD, y),
                line, font=font, fill=brand.txt_muted,
            )
            y += text_height(draw, line, font) + 12 * SCALE
        return y

    def _draw_callout(self, draw, fonts, callout, brand, S, CONTENT_W, PAD, y, H):
        """Draw a styled callout/insight box."""
        font_callout = self._font(fonts, "regular", 16)
        lines = wrap_text(draw, callout, font_callout, CONTENT_W - 80 * S)[:4]

        callout_h = (
            sum(text_height(draw, l, font_callout) + 10 * S for l in lines)
            + 60 * S
        )

        # Only draw if it fits
        if y + callout_h > H - 200 * S:
            return y

        box_x = PAD + CONTENT_X_PAD - 20 * S
        box_w = CONTENT_W + 40 * S

        # Background
        draw.rounded_rectangle(
            [box_x, y, box_x + box_w, y + callout_h],
            radius=brand.scaled_radius(S),
            fill=brand.col_bg,
            outline=brand.accent,
            width=2,
        )

        # Accent bar on left
        draw.rectangle(
            [box_x, y + 12 * S, box_x + 4 * S, y + callout_h - 12 * S],
            fill=brand.accent,
        )

        ty = y + 24 * S
        for line in lines:
            draw.text(
                (box_x + 32 * S, ty),
                line, font=font_callout, fill=brand.txt_main,
            )
            ty += text_height(draw, line, font_callout) + 10 * S

        return y + callout_h + 24 * S

    def _draw_hashtags(self, draw, fonts, hashtags, brand, S, TW, PAD, H, y):
        font_ht = self._font(fonts, "regular", 15)
        ht_lines = wrap_text(draw, hashtags, font_ht, TW)[:2]
        ht_y = min(y + 16 * S, H - 100 * S - 110 * S)

        for hl in ht_lines:
            if ht_y + 40 * S > H - 110 * S:
                break
            draw.text((PAD, ht_y), hl, font=font_ht, fill=brand.accent)
            ht_y += 44 * S

    def _draw_footer(self, draw, fonts, brand, S, W, H, PAD):
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
