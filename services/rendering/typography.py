"""
Text-preparation helpers for card rendering (v12).

Rules enforced before any text reaches the HTML template:
  • Bullets     — max 5 words, max 2 lines, no ellipsis if avoidable
  • Titles      — max 2 lines (handled via CSS + autofit; we keep char cap)
  • Reasons     — max 2 lines (truncate at word boundary)
  • Paragraphs  — max 12 words

Text is REWRITTEN for conciseness, not just truncated:
  • Strip redundant adjectives (very, really, quite, extremely, …)
  • Strip filler phrases ("it is important to", "in order to", …)
  • Prefer active-voice, technical language

The autofit JS in layout.py handles any remaining overflow as a safety net.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

_WS = re.compile(r"\s+")

# --------------------------------------------------------------------------- #
# Redundant-word removal                                                        #
# --------------------------------------------------------------------------- #

_FILLER_PHRASES = re.compile(
    r"\b(very|really|quite|extremely|absolutely|basically|essentially|"
    r"certainly|definitely|simply|just|actually|literally|obviously|"
    r"in order to|it is important to|it should be noted that|"
    r"please note that|as you can see|as a result of|"
    r"due to the fact that|in the event that)\b",
    re.IGNORECASE,
)


def _compress(text: str) -> str:
    """Remove filler phrases and normalise whitespace."""
    clean = _FILLER_PHRASES.sub(" ", text.strip())
    return _WS.sub(" ", clean).strip()


# --------------------------------------------------------------------------- #
# Core helpers                                                                  #
# --------------------------------------------------------------------------- #

def _normalize(text: Optional[str]) -> str:
    if not text:
        return ""
    return _WS.sub(" ", str(text).strip())


def shorten_bullet(text: Optional[str], max_words: int = 5) -> str:
    """
    Compress a bullet to at most *max_words* words.

    Unlike the old implementation, compression runs BEFORE word-count
    so filler removal can rescue bullets that would otherwise be cut.
    Adds an ellipsis ONLY when words were dropped after compression.
    """
    clean = _compress(_normalize(text))
    if not clean:
        return ""
    words = clean.split()
    if len(words) <= max_words:
        return clean
    return " ".join(words[:max_words]) + "\u2026"


def clamp_words(text: Optional[str], max_words: int) -> str:
    """Alias — clamp any text to *max_words* words."""
    return shorten_bullet(text, max_words)


def compress_paragraph(text: Optional[str], max_words: int = 12) -> str:
    """Reduce a paragraph to *max_words* words (for captions / reasons)."""
    return shorten_bullet(text, max_words)


def truncate_chars(text: Optional[str], max_chars: int) -> str:
    """Last-resort character-based truncation at a word boundary."""
    clean = _normalize(text)
    if len(clean) <= max_chars:
        return clean
    cut = clean.rfind(" ", 0, max_chars)
    if cut <= 0:
        cut = max_chars
    return clean[:cut].rstrip() + "\u2026"


def prepare_bullets(
    points: Optional[List[str]],
    limit: int,
    max_words: int = 5,
) -> List[str]:
    """
    Compress and cap a bullet list for card display.

    Applies shorten_bullet (filler removal + word cap) to every point,
    then keeps the first *limit* non-empty results.
    """
    if not points:
        return []
    out: List[str] = []
    for p in points:
        s = shorten_bullet(p, max_words)
        if s:
            out.append(s)
        if len(out) >= limit:
            break
    return out


def parse_winner(
    winner_text: Optional[str],
    option_a: str = "",
    option_b: str = "",
) -> Tuple[str, str]:
    """
    Split a freeform winner string into (name, reason).

    Reason is compressed to max 12 words so it fits in 2 lines at 17 px.
    """
    clean = _normalize(winner_text)
    if not clean:
        return "", ""

    for delim in (" \u2014 ", " \u2013 ", " - ", ": ", " | "):
        if delim in clean:
            name, _, reason = clean.partition(delim)
            name = name.strip()
            reason = compress_paragraph(reason.strip(), 12)
            if name:
                return truncate_chars(name, 34), reason

    # No delimiter — try matching against the known option names
    for option in (option_a, option_b):
        option = _normalize(option)
        if option and clean.lower().startswith(option.lower()):
            tail = clean[len(option):].strip(" -:\u2014\u2013|")
            return truncate_chars(option, 34), compress_paragraph(tail, 12)

    return truncate_chars(clean, 34), ""


def wrap_two_lines(text: Optional[str], max_chars: int) -> Tuple[str, str]:
    """Split text into at most two lines at a word boundary."""
    clean = _normalize(text)
    if not clean:
        return "", ""
    if len(clean) <= max_chars:
        return clean, ""
    cut = clean.rfind(" ", 0, max_chars)
    if cut == -1:
        cut = max_chars
    return clean[:cut], clean[cut:].strip()
