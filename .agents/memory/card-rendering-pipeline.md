---
name: Card rendering pipeline (v13)
description: Design decisions behind the SVG/Jinja2/Playwright card renderer — text-in-HTML rule, font embedding, autofit safety net, layout unification, content-aware layout classifier, background pattern engine
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
`templates/cards/card.html` handles all layout kinds (vs / before_after / grid / pipeline / list) via a `layout_kind` context variable, replacing per-layout template files.

## Layout is chosen from content shape, not just theme label
`typography.pick_layout_kind()` inspects bullet counts, debate structure (option_a/b set), and a sequence-signal-word regex (first/then/step/request/pipeline/etc.) to choose vs/before_after/grid/pipeline/list — replacing a flat theme→layout lookup table. Same theme can render different layouts depending on actual content shape.

**Why:** a static theme→layout map produced repetitive-looking cards for one theme and awkward layouts when content didn't match the theme's "typical" shape (e.g. a security post that's actually a step-by-step flow).

## Text compression must avoid dangling truncation
`typography.shorten_bullet`/`truncate_chars` recapitalize the first letter after stripping a leading weak adjective (e.g. "Great" → dropped → next word capitalized), and repeatedly strip trailing stopwords (and/or/with/for/the/...) before appending an ellipsis, so truncated bullets never end on a dangling preposition/conjunction.

**Why:** naive word-count capping produced bullets like "for statistical analysis with…" or "...MLOps, and…" which read as broken sentences — very noticeable quality regression even though the layout itself was correct.

## Flex layouts must not cluster content — use space-evenly, not center
Multi-item flex containers (`.vs-bullets`, `.ba-bullets`, `.list-layout`) use `justify-content: space-evenly` instead of `center`. With `center`, a short list of items clumps together in the middle of a tall card leaving large dead zones above/below — looks sparse/unfinished. `space-evenly` distributes items across the full available height regardless of count.

**Why:** at 1080×1080 with content-driven (not fixed) item counts, `center` looked fine with many items but badly empty with 2-3 — `space-evenly` is robust across counts.

## Structured comparison schema bypasses the content-aware classifier
Comparison-theme posts can arrive in two schemas: legacy (option_a/option_b/winner/points_a/points_b) or the newer balanced 6-section schema (left_title/left_content/right_title/right_content/tradeoff/best_for/verdict/recommendation_title/recommendation). `TechPost.is_structured_comparison` (true when tradeoff+best_for+verdict are all present) forces `layout_kind = "comparison_matrix"` directly in `card_renderer._build_context`, skipping `pick_layout_kind()` entirely — the two schemas render via completely different template branches (`comparison_matrix` vs vs/grid/etc), and `CaptionBuilder.build()` branches the same way for the Facebook caption text.

**Why:** the new schema has no free-form `caption` key (only structured fields) and needs all 6 sections rendered distinctly (no bullet compression) to preserve the "balanced both sides, no repeated info" rule from the generation prompt — cramming it into the old vs/grid bullet layouts would lose section identity.

**How to apply:** `from_gemini_response` detects schema by presence of `"tradeoff"` or `"left_content"` in the raw dict; `option_a`/`option_b` are reused as left_title/right_title for backward compat with `is_debate`.

## Background pattern varies per theme, chosen via palette
`services/svg_renderer.py` builds one of 6 text-free pattern generators (dots/grid/hex/nodes/mesh/topology) selected by `THEME_PALETTES[theme]["pattern"]` in `rendering/layout.py` — e.g. AI→mesh, database/backend→nodes, cloud/devops→topology, security→hex, architecture→grid. Kept at very low opacity (~0.02-0.05) so it never competes with foreground content.
