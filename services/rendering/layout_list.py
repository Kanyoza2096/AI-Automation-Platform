"""
List layout strategy — numbered or bulleted list card.

For tips, steps, recommendations, and structured information.
Clean numbered items with optional header per item.

Structure:
  [Brand name]                    [Topic badge]
  ───────────────────────────────────────────
  [Content type badge]
  [Title]
  ┌── 1 ────────────────────────────────────┐
  │  Item title                             │
  │  Item description                       │
  └─────────────────────────────────────────┘
  ┌── 2 ────────────────────────────────────┐
  │  ...                                    │
  └─────────────────────────────────────────┘
  #hashtags
  ───────────────────────────────────────────
  Footer                          Date
"""

import logging
from datetime import datetime

from .brand_style import BrandStyle
from .render_utils import (
    SCALE, CARD_W, CARD_H,
    text_width, text_height, wrap_text,
    draw_horizontal_gradient, export_png,
    normalize_hashtags,
)
from services.rendering.typography import truncate_chars, shorten_bullet

logger = logging.getLogger(__name__)

RULE_Y_TOP = 120 * SCALE
TOP_BAR_Y = 44 * SCALE
BADGE_Y = 36 * SCALE


class ListLayout:
    """
    Numbered/bulleted list card for structured content.

    Each item gets a card with number, title, and description.
    Items are compressed to fit available space.
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
        title = (post_dict.get("title") or "").strip()
        items = post_dict.get("items") or post_dict.get("points_a") or []
        hashtags = normalize_hashtags(
            post_dict.get("brand_hashtags") or post_dict.get("hashtags") or ""
        )
        topic = (
            post_dict.get("topic") or post_dict.get("theme", "list")
        ).replace("_", " ").upper()[:28]
        brand_name = brand.brand_name.upper()

        # Normalize items to list of dicts
        normalized_items = []
        for item in items:
            if isinstance(item, str):
                normalized_items.append({"text": item, "label": ""})
            elif isinstance(item, dict):
                normalized_items.append({
                    "text": item.get("text") or item.get("title") or "",
                    "label": item.get("label") or "",
                })

        # Compress text
        for item in normalized_items:
            item["text"] = shorten_bullet(item["text"], 12)
            if item["label"]:
                item["label"] = truncate_chars(item["label"], 22)

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
        ctype = post_dict.get("content_type", "LIST").upper()[:20]
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
        y = cb_y + ph + 28 * S

        # ── 5. Title ─────────────────────────────────────────────
        if title:
            font_title = self._font(fonts, "bold", 40)
            t_lines = wrap_text(draw, title, font_title, TW)[:2]
            for line in t_lines:
                draw.text((PAD, y), line, font=font_title, fill=brand.txt_main)
                y += text_height(draw, line, font_title) + 12 * S
            y += 16 * S

        # ── 6. Items ─────────────────────────────────────────────
        available_h = H - y - 200 * S - 60 * S - 100 * S

        # Fit items into available space
        max_items = min(len(normalized_items), 6)
        item_h = available_h // max(max_items, 1)
        item_h = min(item_h, 160 * S)  # Cap item height
        item_h = max(item_h, 90 * S)   # Minimum item height

        item_pad = 24 * S
        item_inner_w = TW - item_pad * 2 - 60 * S  # Space for number

        for i, item in enumerate(normalized_items[:max_items]):
            item_y = y
            item_bottom = item_y + item_h

            # Item background
            draw.rounded_rectangle(
                [PAD, item_y, W - PAD, item_bottom],
                radius=brand.scaled_radius(S),
                fill=brand.col_bg,
                outline=brand.rule_clr,
                width=1,
            )

            # Number circle
            num_cx = PAD + item_pad + 18 * S
            num_cy = item_y + item_h // 2
            draw.ellipse(
                [
                    num_cx - 16 * S, num_cy - 16 * S,
                    num_cx + 16 * S, num_cy + 16 * S,
                ],
                fill=brand.primary,
            )
            font_num = self._font(fonts, "bold", 16)
            num_str = str(i + 1)
            nw = text_width(draw, num_str, font_num)
            nh = text_height(draw, num_str, font_num)
            draw.text(
                (num_cx - nw // 2, num_cy - nh // 2),
                num_str, font=font_num, fill=brand.header_text,
            )

            # Item label (optional)
            text_x = PAD + item_pad + 48 * S
            ty = item_y + item_pad

            if item["label"]:
                font_lbl = self._font(fonts, "bold", 16)
                draw.text(
                    (text_x, ty),
                    item["label"].upper(),
                    font=font_lbl, fill=brand.txt_main,
                )
                ty += text_height(draw, item["label"], font_lbl) + 8 * S

            # Item text
            font_item = self._font(fonts, "regular", 15)
            item_lines = wrap_text(
                draw, item["text"], font_item, item_inner_w,
            )[:3]
            for line in item_lines:
                if ty + text_height(draw, line, font_item) > item_bottom - item_pad:
                    break
                draw.text(
                    (text_x, ty), line, font=font_item, fill=brand.txt_muted,
                )
                ty += text_height(draw, line, font_item) + 6 * S

            y = item_bottom + 12 * S

        # ── 7. Hashtags ──────────────────────────────────────────
        if hashtags and brand.max_hashtags > 0 and y < H - 180 * S:
            font_ht = self._font(fonts, "regular", 14)
            ht_lines = wrap_text(draw, hashtags, font_ht, TW)[:2]
            ht_y = min(y + 16 * S, H - 100 * S - 80 * S)
            for hl in ht_lines:
                if ht_y + 36 * S > H - 110 * S:
                    break
                draw.text((PAD, ht_y), hl, font=font_ht, fill=brand.accent)
                ht_y += 40 * S

        # ── 8. Footer ────────────────────────────────────────────
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
