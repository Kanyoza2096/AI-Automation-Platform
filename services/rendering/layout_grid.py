"""
Grid layout strategy — 2×2 or 3×2 decision matrix card.

For structured comparisons, feature matrices, and multi-factor analysis.
Four labeled cells arranged in a grid with optional recommendation footer.

Structure:
  [Brand name]                    [Topic badge]
  ───────────────────────────────────────────
  [Content type badge]
  [Title]
  ┌──────────────┬──────────────┐
  │  APPROACH    │  TRADE-OFF   │
  │  text...     │  text...     │
  ├──────────────┼──────────────┤
  │  BEST FOR    │  VERDICT     │
  │  text...     │  text...     │
  └──────────────┴──────────────┘
  ┌─────────────────────────────────────────┐
  │  RECOMMENDATION: text...                │
  └─────────────────────────────────────────┘
  #hashtags
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
    draw_horizontal_gradient, export_png,
    normalize_hashtags,
)
from services.rendering.typography import truncate_chars, shorten_bullet

logger = logging.getLogger(__name__)

RULE_Y_TOP = 120 * SCALE
TOP_BAR_Y = 44 * SCALE
BADGE_Y = 36 * SCALE

GRID_LABELS = ["APPROACH", "TRADE-OFF", "BEST FOR", "VERDICT"]


class GridLayout:
    """
    2×2 grid card for structured comparisons and decision matrices.

    Four cells with labels. Optional recommendation banner at bottom.
    Cells auto-size to fit content with font fitting.
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
        cells = post_dict.get("cells") or []
        recommendation = (
            post_dict.get("recommendation")
            or post_dict.get("verdict")
            or post_dict.get("callout")
            or ""
        ).strip()
        rec_label = (
            post_dict.get("recommendation_title")
            or brand.winner_label
        ).strip().upper()
        hashtags = normalize_hashtags(
            post_dict.get("brand_hashtags") or post_dict.get("hashtags") or ""
        )
        topic = (
            post_dict.get("topic") or post_dict.get("theme", "grid")
        ).replace("_", " ").upper()[:28]
        brand_name = brand.brand_name.upper()

        # Normalize cells — accept list of dicts or list of strings
        normalized_cells = []
        for i, cell in enumerate(cells[:4]):
            if isinstance(cell, str):
                normalized_cells.append({
                    "label": GRID_LABELS[i] if i < len(GRID_LABELS) else "",
                    "text": shorten_bullet(cell, 10),
                })
            elif isinstance(cell, dict):
                normalized_cells.append({
                    "label": cell.get("label", GRID_LABELS[i] if i < len(GRID_LABELS) else ""),
                    "text": shorten_bullet(cell.get("text", ""), 10),
                })

        # Pad to 4 cells if needed
        while len(normalized_cells) < 4:
            normalized_cells.append({"label": "", "text": ""})

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
        ctype = post_dict.get("content_type", "GRID").upper()[:20]
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

        # ── 6. Grid cells ────────────────────────────────────────
        grid_gap = 16 * S
        rec_est = 140 * S if recommendation else 0
        footer_h = 116 * S
        ht_h = 60 * S

        grid_top = y
        grid_bottom = H - rec_est - footer_h - ht_h - 56 * S
        grid_h = max(300 * S, grid_bottom - grid_top)

        cell_w = (TW - grid_gap) // 2
        cell_h = (grid_h - grid_gap) // 2
        cell_pad = 24 * S

        for idx, cell in enumerate(normalized_cells):
            col = idx % 2
            row = idx // 2
            cx = PAD + col * (cell_w + grid_gap)
            cy = grid_top + row * (cell_h + grid_gap)

            # Cell background
            draw.rounded_rectangle(
                [cx, cy, cx + cell_w, cy + cell_h],
                radius=brand.scaled_radius(S),
                fill=brand.col_bg,
                outline=brand.rule_clr,
                width=1,
            )

            # Cell label
            if cell["label"]:
                font_lbl = self._font(fonts, "bold", 10)
                lbl_w = text_width(draw, cell["label"], font_lbl)
                lbl_h = text_height(draw, cell["label"], font_lbl)
                lbl_bg_w = lbl_w + 24 * S
                lbl_bg_h = lbl_h + 14 * S

                # Label background pill
                draw.rounded_rectangle(
                    [
                        cx + cell_pad,
                        cy + cell_pad,
                        cx + cell_pad + lbl_bg_w,
                        cy + cell_pad + lbl_bg_h,
                    ],
                    radius=6 * S,
                    fill=brand.primary,
                )
                draw.text(
                    (cx + cell_pad + 12 * S, cy + cell_pad + 7 * S),
                    cell["label"],
                    font=font_lbl,
                    fill=brand.header_text,
                )
                text_start_y = cy + cell_pad + lbl_bg_h + 12 * S
            else:
                text_start_y = cy + cell_pad

            # Cell text
            if cell["text"]:
                inner_w = cell_w - cell_pad * 2 - 16 * S
                font_cell = self._font(fonts, "regular", 15)
                cell_lines = wrap_text(
                    draw, cell["text"], font_cell, inner_w,
                )[:4]

                ty = text_start_y
                max_y = cy + cell_h - cell_pad
                for line in cell_lines:
                    if ty + text_height(draw, line, font_cell) > max_y:
                        break
                    draw.text(
                        (cx + cell_pad + 8 * S, ty),
                        line,
                        font=font_cell,
                        fill=brand.txt_muted,
                    )
                    ty += text_height(draw, line, font_cell) + 8 * S

        y = grid_top + grid_h + 24 * S

        # ── 7. Recommendation ────────────────────────────────────
        if recommendation and y < H - 220 * S:
            rec_font = self._font(fonts, "regular", 15)
            rec_lines = wrap_text(
                draw, truncate_chars(recommendation, 200),
                rec_font, TW - 80 * S,
            )[:2]

            rec_h = len(rec_lines) * 36 * S + 56 * S

            if y + rec_h < H - 180 * S:
                # Gradient background
                draw_horizontal_gradient(
                    draw, y, y + rec_h,
                    brand.primary, brand.secondary,
                    PAD, W - PAD,
                )
                draw.rounded_rectangle(
                    [PAD, y, W - PAD, y + rec_h],
                    radius=brand.scaled_radius(S),
                    outline=brand.bg,
                    width=4 * S,
                )

                # Label
                font_rec_lbl = self._font(fonts, "bold", 11)
                draw.text(
                    (PAD + 32 * S, y + 16 * S),
                    rec_label,
                    font=font_rec_lbl,
                    fill=brand.header_text,
                )

                ty = y + 40 * S
                for line in rec_lines:
                    draw.text(
                        (PAD + 32 * S, ty),
                        line,
                        font=rec_font,
                        fill=brand.header_text,
                    )
                    ty += 36 * S

                y += rec_h + 16 * S

        # ── 8. Hashtags ──────────────────────────────────────────
        if hashtags and brand.max_hashtags > 0 and y < H - 180 * S:
            font_ht = self._font(fonts, "regular", 14)
            ht_lines = wrap_text(draw, hashtags, font_ht, TW)[:2]
            ht_y = min(y + 8 * S, H - 100 * S - 80 * S)
            for hl in ht_lines:
                if ht_y + 36 * S > H - 110 * S:
                    break
                draw.text((PAD, ht_y), hl, font=font_ht, fill=brand.accent)
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
