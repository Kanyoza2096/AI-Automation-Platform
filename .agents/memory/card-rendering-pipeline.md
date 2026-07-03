---
name: Card rendering pipeline (v11)
description: Design decisions behind the SVG/Jinja2/Playwright card renderer — text-in-HTML rule, font embedding, autofit safety net, layout unification
---

## Text belongs in HTML, never in SVG
All titles, bullets, column headers, and the winner banner are real HTML/CSS text rendered by Chromium — SVG (`services/svg_renderer.py`) is decorative-only (gradient glows, dot grid). Rasterizing text inside SVG made it blurry and impossible to autofit/wrap reliably.

**Why:** the pre-refactor version drew all card text as SVG `<text>` elements sized in Python with manual character-count truncation — this produced fuzzy text and frequent clipping since Python string-length heuristics don't match real font metrics.

**How to apply:** any new card content should be added as HTML in `templates/cards/card.html`, not generated as SVG markup.

## Font loading must be self-contained
Fonts are embedded as base64 data URIs directly in the HTML (`services/rendering/fonts.py`), not linked via URL or `@font-face src: url(file...)`. This guarantees Playwright never race-conditions on font loading regardless of network/filesystem access at screenshot time. Combined with `page.evaluate("document.fonts.ready...")` before screenshot.

**Why:** Playwright screenshots taken before a linked webfont finishes loading fall back to system fonts, causing an inconsistent look across renders.

## Autofit is a safety net, not the primary fitting mechanism
Primary fitting is done in Python before rendering (bullet word-count capping via `typography.prepare_bullets`, character truncation for titles/winner names). The in-browser JS (`layout.AUTOFIT_JS`) only shrinks `[data-autofit]` elements that still overflow after that — it's a last-resort guarantee, not the main text-fitting strategy.

**Why:** relying solely on JS-based shrinking after the fact produced inconsistent results across the four layout kinds (columns, grid, list) since box heights vary; capping content length up front is more predictable and keeps typography consistent card-to-card.

**How to apply:** when a card element could overflow (long option names, long winner strings, long single bullets), give it a fixed pixel `max-height` matching its font-size ceiling + `-webkit-line-clamp`, AND flag it `data-autofit` with a `data-autofit-min` floor so JS can shrink it further if still too tall/wide.

## One unified template, not one-per-theme
`templates/cards/card.html` handles all four layout kinds (vs / before_after / grid / list) via a `layout_kind` context variable, replacing four near-duplicate template files. `services/card_renderer.THEME_LAYOUT_MAP` maps content theme → layout kind.
