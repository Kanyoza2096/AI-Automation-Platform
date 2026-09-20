"""
Hero layout strategy — bold announcement/promotional card.

For announcements, promotions, events, and attention-grabbing content.
Large typography, bold gradient accent bar, minimal structure.

Structure:
  ┌─────────────────────────────────────────┐
  │  [Large accent gradient band]           │
  │                                         │
  │  ANNOUNCEMENT                           │
  │                                         │
  │  [Title — very large, centered]         │
  │  [Subtitle / caption]                   │
  │                                         │
  │  ┌─────────────────────────────────┐    │
  │  │  Key message / CTA              │    │
  │  └─────────────────────────────────┘    │
  │                                         │
  │  #hashtags                              │
  └─────────────────────────────────────────┘
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
from services.rendering.typography import truncate_chars

logger = logging.getLogger(__name__)

TOP_BAR_Y = 44 * SCALE
BADGE_Y = 36 * SCALE


class HeroLayout:
    """
    Bold hero card for announcements and promotions.

    Minimal structure with maximum visual impact.
    Large accent gradient, centered typography, optional CTA box.
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
        subtitle = (post_dict.get("caption") or post_dict.get("subtitle") or "").strip()
        cta = (post_dict.get("callout") or post_dict.get("cta") or "").strip()
        hashtags = normalize_hashtags(
            post_dict.get("brand_hashtags") or post_dict.get("hashtags") or ""
        )
        topic = (
            post_dict.get("topic") or post_dict.get("theme", "announcement")
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

        # ── 3. Accent gradient band ──────────────────────────────
        band_y = 160 * S
        band_h = 12 * S
        draw_horizontal_gradient(
            draw, band_y, band_y + band_h,
            brand.primary, brand.secondary,
            PAD, W - PAD,
        )

        # ── 4. Content type label ────────────────────────────────
        ctype = post_dict.get("content_type", "ANNOUNCEMENT").upper()[:24]
        font_ct = self._font(fonts, "bold", 12)
        ct_w = text_width(draw, ctype, font_ct)
        ct_x = (W - ct_w) // 2
        ct_y = band_y + band_h + 40 * S
        draw.text(
            (ct_x, ct_y),
            ctype,
            font=font_ct,
            fill=brand.accent,
        )

        # ── 5. Title (centered, very large) ──────────────────────
        y = ct_y + text_height(draw, ctype, font_ct) + 32 * S
        title_w = TW - 120 * S  # Narrower for centered feel
        title_x = PAD + 60 * S

        for font_size in (58, 50, 42, 36, 30):
            font_title = self._font(fonts, "bold", font_size)
            t_lines = wrap_text(draw, title, font_title, title_w)[:3]
            total_h = sum(
                text_height(draw, l, font_title) + 18 * S for l in t_lines
            )
            if y + total_h < H - 300 * S and len(t_lines) <= 3:
                for line in t_lines:
                    lw = text_width(draw, line, font_title)
                    lx = (W - lw) // 2
                    draw.text(
                        (lx, y), line, font=font_title, fill=brand.txt_main,
                    )
                    y += text_height(draw, line, font_title) + 18 * S
                break
        else:
            font_title = self._font(fonts, "bold", 30)
            t_lines = wrap_text(
                draw, truncate_chars(title, 100), font_title, title_w,
            )[:3]
            for line in t_lines:
                lw = text_width(draw, line, font_title)
                lx = (W - lw) // 2
                draw.text((lx, y), line, font=font_title, fill=brand.txt_main)
                y += text_height(draw, line, font_title) + 18 * S

        # ── 6. Subtitle ──────────────────────────────────────────
        if subtitle and y < H - 260 * S:
            y += 8 * S
            font_sub = self._font(fonts, "regular", 20)
            sub_lines = wrap_text(
                draw, truncate_chars(subtitle, 120), font_sub, title_w,
            )[:2]
            for line in sub_lines:
                lw = text_width(draw, line, font_sub)
                lx = (W - lw) // 2
                draw.text(
                    (lx, y), line, font=font_sub, fill=brand.txt_muted,
                )
                y += text_height(draw, line, font_sub) + 12 * S

        # ── 7. CTA box ───────────────────────────────────────────
        if cta and y < H - 220 * S:
            y += 16 * S
            font_cta = self._font(fonts, "bold", 18)
            cta_w = min(text_width(draw, cta, font_cta) + 80 * S, TW - 80 * S)
            cta_lines = wrap_text(draw, cta, font_cta, cta_w - 60 * S)[:2]
            cta_h = len(cta_lines) * 32 * S + 48 * S
            cta_x = (W - cta_w) // 2

            if y + cta_h < H - 160 * S:
                draw.rounded_rectangle(
                    [cta_x, y, cta_x + cta_w, y + cta_h],
                    radius=brand.scaled_radius(S),
                    fill=brand.primary,
                )
                ty = y + 24 * S
                for line in cta_lines:
                    lw = text_width(draw, line, font_cta)
                    lx = cta_x + (cta_w - lw) // 2
                    draw.text(
                        (lx, ty), line, font=font_cta, fill=brand.header_text,
                    )
                    ty += 32 * S
                y += cta_h + 16 * S

        # ── 8. Hashtags ──────────────────────────────────────────
        if hashtags and brand.max_hashtags > 0 and y < H - 180 * S:
            font_ht = self._font(fonts, "regular", 14)
            ht_lines = wrap_text(draw, hashtags, font_ht, TW)[:2]
            ht_y = min(y + 16 * S, H - 100 * S - 80 * S)
            for hl in ht_lines:
                if ht_y + 36 * S > H - 110 * S:
                    break
                # Center hashtags
                hw = text_width(draw, hl, font_ht)
                hx = (W - hw) // 2
                draw.text((hx, ht_y), hl, font=font_ht, fill=brand.accent)
                ht_y += 40 * S

        # ── 9. Footer ────────────────────────────────────────────
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
