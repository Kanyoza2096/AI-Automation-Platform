"""
Text-preparation helpers for card rendering.

These operate purely on display strings at render time — they never
touch stored business data (TechPost content). Their job is to make
sure whatever text lands on a 1080x1080 image reads as a short, punchy
label instead of a wrapped paragraph, and to provide a graceful,
last-resort truncation path for anything that still doesn't fit.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

_WS_RE = re.compile(r"\s+")


def _normalize(text: Optional[str]) -> str:
    if not text:
        return ""
    return _WS_RE.sub(" ", str(text).strip())


def shorten_bullet(text: Optional[str], max_words: int = 6) -> str:
    """
    Reduce a bullet point to a concise phrase (default: 6 words max).

    Bullets on a card are labels, not sentences — long explanations
    belong in the caption. Word-boundary safe; adds an ellipsis only
    when words were actually dropped.
    """
    clean = _normalize(text)
    if not clean:
        return ""
    words = clean.split(" ")
    if len(words) <= max_words:
        return clean
    return " ".join(words[:max_words]) + "\u2026"


def clamp_words(text: Optional[str], max_words: int) -> str:
    """Alias kept explicit for call sites that aren't strictly 'bullets'."""
    return shorten_bullet(text, max_words)


def truncate_chars(text: Optional[str], max_chars: int) -> str:
    """Last-resort, character-based graceful truncation at a word boundary."""
    clean = _normalize(text)
    if len(clean) <= max_chars:
        return clean
    cut = clean.rfind(" ", 0, max_chars)
    if cut <= 0:
        cut = max_chars
    return clean[:cut].rstrip() + "\u2026"


def prepare_bullets(points: Optional[List[str]], limit: int, max_words: int = 6) -> List[str]:
    """Shorten and cap a list of bullet points for card display."""
    if not points:
        return []
    return [shorten_bullet(p, max_words) for p in points[:limit] if p]


def parse_winner(winner_text: Optional[str], option_a: str = "", option_b: str = "") -> Tuple[str, str]:
    """
    Split a freeform winner string into (name, reason).

    Content generation only produces a single "winner" string (e.g.
    "PostgreSQL — better for complex relational queries"). The card's
    winner banner needs a short name plus an optional one-line reason,
    so we split on the first common delimiter without altering the
    underlying content.
    """
    clean = _normalize(winner_text)
    if not clean:
        return "", ""

    for delim in (" — ", " – ", " - ", ": ", " | "):
        if delim in clean:
            name, _, reason = clean.partition(delim)
            name = name.strip()
            reason = reason.strip()
            if name:
                return truncate_chars(name, 34), truncate_chars(reason, 70)

    # No delimiter — try matching against the known option names so at
    # least the name portion is exact when possible.
    for option in (option_a, option_b):
        option = _normalize(option)
        if option and clean.lower().startswith(option.lower()):
            reason = clean[len(option):].strip(" -:—–|")
            return truncate_chars(option, 34), truncate_chars(reason, 70)

    return truncate_chars(clean, 34), ""


def wrap_two_lines(text: Optional[str], max_chars: int) -> Tuple[str, str]:
    """Split text into at most two lines at a word boundary (SVG/decorative use only)."""
    clean = _normalize(text)
    if not clean:
        return "", ""
    if len(clean) <= max_chars:
        return clean, ""
    cut = clean.rfind(" ", 0, max_chars)
    if cut == -1:
        cut = max_chars
    return clean[:cut], clean[cut:].strip()
