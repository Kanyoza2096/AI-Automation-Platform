"""
Card rendering support package.

Split out of the monolithic card renderer so typography, layout tokens,
and font-loading concerns can be tested and reused independently:

  fonts.py       — self-hosted, base64-embedded webfont + @font-face CSS
  typography.py  — text preparation helpers (wrapping, shortening, clamping)
  layout.py      — design tokens (spacing, palette, type scale) + JS autofit
"""
