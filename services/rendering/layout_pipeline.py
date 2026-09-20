"""
Pipeline layout strategy — sequential flow / step-by-step card.

For workflows, processes, deployment pipelines, and ordered sequences.
Connected steps with directional arrows and numbering.

Structure:
  [Brand name]                    [Topic badge]
  ───────────────────────────────────────────
  [Content type badge]
  [Title]
  ┌── STEP 1 ───────────────────────────────┐
  │  │  Title                               │
  │  │  Description                         │
  └───┬─────────────────────────────────────┘
      ↓
  ┌── STEP 2 ───────────────────────────────┐
  │  │  Title                               │
  │  │  Description                         │
  └───┬─────────────────────────────────────┘
      ↓
  ┌── STEP 3 ───────────────────────────────┐
  │  │  Title                               │
  │  │  Description                         │
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
    export_png,
    normalize_hashtags,
)
from services.rendering.typography import truncate_chars, shorten_bullet

logger = logging.getLogger(__name__)

RULE_Y_TOP = 120 * SCALE
TOP_BAR_Y = 44 * SCALE
BADGE_Y = 36 * SCALE

PIPELINE_LABELS = ["STEP 1", "STEP 2", "STEP 3", "STEP 4"]


class PipelineLayout:
    """
    Sequential pipeline card for workflows and processes.

    Each step has a number, title, and description.
    Steps are connected by vertical connector lines with arrows.
    Auto-sizes steps to fit available vertical space.
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
        steps = post_dict.get("steps") or post_dict.get("items") or post_dict.get("points_a") or []
        hashtags = normalize_hashtags(
            post_dict.get("brand_hashtags") or post_dict.get("hashtags") or ""
        )
        topic = (
            post_dict.get("topic") or post_dict.get("theme", "pipeline")
        ).replace("_", " ").upper()[:28]
        brand_name = brand.brand_name.upper()

        # Normalize steps
        normalized_steps = []
        for i, step in enumerate(steps[:4]):
            if isinstance(step, str):
                normalized_steps.append({
                    "label": PIPELINE_LABELS[i] if i < len(PIPELINE_LABELS) else f"STEP {i+1}",
                    "text": shorten_bullet(step, 15),
                })
            elif isinstance(step, dict):
                normalized_steps.append({
                    "label": step.get("label", PIPELINE_LABELS[i] if i < len(PIPELINE_LABELS) else f"STEP {i+1}"),
                    "text": shorten_bullet(step.get("text", ""), 15),
                })

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
        ctype = post_dict.get("content_type", "PIPELINE").upper()[:20]
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
            y += 12 * S

        # ── 6. Pipeline steps ────────────────────────────────────
        footer_h = 116 * S
        ht_h = 60 * S
        available_h = H - y - footer_h - ht_h - 56 * S
        num_steps = max(len(normalized_steps), 1)

        # Connector area
        connector_w = 40 * S
        step_inner_w = TW - connector_w - 40 * S
        step_gap = 12 * S
        total_gaps = step_gap * (num_steps - 1)
        step_h = (available_h - total_gaps) // num_steps
        step_h = min(step_h, 180 * S)
        step_h = max(step_h, 80 * S)

        prev_bottom_center = None

        for i, step in enumerate(normalized_steps):
            step_y = y
            step_bottom = step_y + step_h

            # Step card background
            draw.rounded_rectangle(
                [PAD, step_y, W - PAD, step_bottom],
                radius=brand.scaled_radius(S),
                fill=brand.col_bg,
                outline=brand.rule_clr,
                width=1,
            )

            # Left accent bar
            bar_w = 6 * S
            draw.rectangle(
                [PAD, step_y + 16 * S, PAD + bar_w, step_bottom - 16 * S],
                fill=brand.primary,
            )

            # Step number circle
            num_cx = PAD + connector_w // 2 + 16 * S
            num_cy = step_y + step_h // 2
            num_r = 14 * S
            draw.ellipse(
                [
                    num_cx - num_r, num_cy - num_r,
                    num_cx + num_r, num_cy + num_r,
                ],
                fill=brand.primary,
            )
            font_num = self._font(fonts, "bold", 13)
            num_str = str(i + 1)
            nw = text_width(draw, num_str, font_num)
            nh = text_height(draw, num_str, font_num)
            draw.text(
                (num_cx - nw // 2, num_cy - nh // 2),
                num_str, font=font_num, fill=brand.header_text,
            )

            # Connector line from previous step
            if prev_bottom_center is not None:
                draw.line(
                    [
                        (num_cx, prev_bottom_center),
                        (num_cx, num_cy - num_r),
                    ],
                    fill=brand.rule_clr,
                    width=2,
                )
                # Arrow head
                arrow_y = num_cy - num_r
                draw.polygon(
                    [
                        (num_cx, arrow_y),
                        (num_cx - 6 * S, arrow_y - 10 * S),
                        (num_cx + 6 * S, arrow_y - 10 * S),
                    ],
                    fill=brand.primary,
                )

            prev_bottom_center = num_cy + num_r

            # Step label
            text_x = PAD + connector_w + 32 * S
            font_lbl = self._font(fonts, "bold", 12)
            draw.text(
                (text_x, step_y + 18 * S),
                step["label"],
                font=font_lbl,
                fill=brand.accent,
            )

            # Step text
            font_text = self._font(fonts, "regular", 15)
            text_lines = wrap_text(
                draw, step["text"], font_text, step_inner_w,
            )[:3]
            ty = step_y + 18 * S + text_height(draw, step["label"], font_lbl) + 10 * S
            for line in text_lines:
                if ty + text_height(draw, line, font_text) > step_bottom - 12 * S:
                    break
                draw.text(
                    (text_x, ty), line, font=font_text, fill=brand.txt_muted,
                )
                ty += text_height(draw, line, font_text) + 6 * S

            y = step_bottom + step_gap

        # ── 7. Hashtags ──────────────────────────────────────────
        if hashtags and brand.max_hashtags > 0 and y < H - 180 * S:
            font_ht = self._font(fonts, "regular", 14)
            ht_lines = wrap_text(draw, hashtags, font_ht, TW)[:2]
            ht_y = min(y + 8 * S, H - 100 * S - 80 * S)
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
