"""
Text-preparation helpers for card rendering (v13).

Rules enforced before any text reaches the HTML template:
  • Bullets     — max 5 words, max 2 lines, no ellipsis if avoidable
  • Titles      — max 2 lines (handled via CSS + autofit; we keep char cap)
  • Reasons     — max 2 lines (truncate at word boundary)
  • Paragraphs  — max 12 words

Text is REWRITTEN for conciseness, not just truncated:
  • Strip redundant adjectives/adverbs and hedging language
  • Strip filler phrases ("it is important to", "in order to", …)
  • Prefer active-voice, technical language

A lightweight content classifier (`pick_layout_kind`) analyses the actual
shape of the post — not just its theme — so structurally different content
(a sequential flow vs. a flat set of facts vs. a head-to-head debate)
produces a visually different layout, per the adaptive layout engine.

The autofit JS in layout.py handles any remaining overflow as a safety net,
never the primary fitting mechanism.
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
    r"incredibly|highly|truly|fairly|rather|somewhat|generally|typically|"
    r"in order to|it is important to|it should be noted that|"
    r"please note that|as you can see|as a result of|"
    r"due to the fact that|in the event that|a lot of|a wide range of|"
    r"in today's world|when it comes to|at the end of the day)\b",
    re.IGNORECASE,
)

# Weak/redundant adjectives that add no technical signal — stripped so the
# noun/verb carries the meaning instead ("robust scalable system" → "system").
_WEAK_ADJECTIVES = re.compile(
    r"\b(great|good|nice|amazing|awesome|powerful|robust|excellent|"
    r"fantastic|incredible|superb|outstanding|remarkable|impressive|"
    r"cutting-edge|state-of-the-art|next-generation|revolutionary|"
    r"seamless|effortless|comprehensive)\s+",
    re.IGNORECASE,
)

# Sequential-flow signal words — presence pushes the classifier toward
# a pipeline/timeline layout instead of a flat grid.
_SEQUENCE_SIGNALS = re.compile(
    r"\b(first|then|next|finally|step \d|phase \d|before|after that|"
    r"followed by|afterwards|subsequently|request|response|pipeline|"
    r"flow|route[sd]?\b|gateway|deploy|build|release)\b",
    re.IGNORECASE,
)


def _compress(text: str) -> str:
    """Remove filler phrases + weak adjectives, normalise whitespace."""
    clean = _FILLER_PHRASES.sub(" ", text.strip())
    clean = _WEAK_ADJECTIVES.sub("", clean)
    clean = _WS.sub(" ", clean).strip()
    if clean:
        clean = clean[0].upper() + clean[1:]
    return clean


# Trailing words that read awkwardly right before an ellipsis (dangling
# prepositions/conjunctions/articles) — stripped so truncated bullets never
# end mid-thought on a word like "with…" or "and…".
_DANGLING_TAIL = re.compile(
    r"\s+(and|or|but|with|for|to|of|in|on|at|by|the|a|an|as|from|into)$",
    re.IGNORECASE,
)


def _drop_dangling_tail(words: List[str], min_words: int = 2) -> List[str]:
    """Repeatedly drop a trailing stopword so truncated text ends cleanly."""
    while len(words) > min_words:
        candidate = " " + words[-1]
        if _DANGLING_TAIL.fullmatch(candidate):
            words = words[:-1]
        else:
            break
    return words


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

    Compression (filler/weak-adjective removal) runs BEFORE word-count
    capping so it can rescue bullets that would otherwise be cut.
    Adds an ellipsis ONLY when words were dropped after compression.
    """
    clean = _compress(_normalize(text))
    if not clean:
        return ""
    words = clean.split()
    if len(words) <= max_words:
        return clean
    kept = _drop_dangling_tail(words[:max_words])
    return " ".join(kept) + "\u2026"


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
    kept = _drop_dangling_tail(clean[:cut].rstrip().split())
    return " ".join(kept) + "\u2026"


def prepare_bullets(
    points: Optional[List[str]],
    limit: int,
    max_words: int = 5,
) -> List[str]:
    """
    Compress and cap a bullet list for card display.

    Applies shorten_bullet (filler removal + word cap) to every point,
    then keeps the first *limit* non-empty, de-duplicated results.
    """
    if not points:
        return []
    out: List[str] = []
    seen = set()
    for p in points:
        s = shorten_bullet(p, max_words)
        key = s.lower()
        if s and key not in seen:
            out.append(s)
            seen.add(key)
        if len(out) >= limit:
            break
    return out


def fit_bullets_dynamic(
    points: Optional[List[str]],
    max_count: int,
    min_count: int = 2,
    max_words: int = 5,
) -> List[str]:
    """
    Density-aware bullet fitting: prefer *max_count* bullets, but if the
    source content is thin, fall back to fewer, slightly longer bullets
    rather than padding with filler — avoids the "crowded vs. empty"
    imbalance called out by the layout-awareness requirements.
    """
    clean_pool = [shorten_bullet(p, max_words) for p in (points or [])]
    clean_pool = [p for p in clean_pool if p]
    if len(clean_pool) >= max_count:
        return clean_pool[:max_count]
    if len(clean_pool) >= min_count:
        return clean_pool
    # Too few points — allow slightly longer bullets so panels don't look bare.
    richer = [shorten_bullet(p, max_words + 3) for p in (points or []) if p]
    seen, out = set(), []
    for p in richer:
        if p and p.lower() not in seen:
            out.append(p)
            seen.add(p.lower())
    return out[:max_count] if out else clean_pool


def parse_winner(
    winner_text: Optional[str],
    option_a: str = "",
    option_b: str = "",
) -> Tuple[str, str]:
    """
    Split a freeform winner string into (name, reason).

    Reason is compressed to max 12 words so it fits in 2 lines at 19 px.
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
                return truncate_chars(name, 28), reason

    # No delimiter — try matching against the known option names
    for option in (option_a, option_b):
        option = _normalize(option)
        if option and clean.lower().startswith(option.lower()):
            tail = clean[len(option):].strip(" -:\u2014\u2013|")
            return truncate_chars(option, 28), compress_paragraph(tail, 12)

    return truncate_chars(clean, 28), ""


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


# --------------------------------------------------------------------------- #
# Adaptive layout classifier                                                   #
# --------------------------------------------------------------------------- #

def pick_layout_kind(
    theme_value: str,
    is_debate: bool,
    points_a: Optional[List[str]] = None,
    points_b: Optional[List[str]] = None,
    winner: str = "",
) -> str:
    """
    Choose a layout kind by analysing the actual SHAPE of the content, not
    just the theme label — this is the core of the adaptive layout engine.

    Signals used:
      • is_debate (option_a/option_b both set)         → vs / before_after
      • sequence-signal words in the combined text      → pipeline
      • theme category                                  → grid / list bias
      • total bullet count                               → list vs grid density

    Falls back to the theme's default only when no stronger content signal
    is present, so posts of the same theme can still render differently.
    """
    pa = points_a or []
    pb = points_b or []
    combined = " ".join(pa + pb)
    seq_hits = len(_SEQUENCE_SIGNALS.findall(combined))
    total_points = len(pa) + len(pb)

    # Strong signal: this reads like an ordered flow/pipeline regardless of
    # theme (API request flow, deploy pipeline, step-by-step process).
    if seq_hits >= 2 and total_points <= 6:
        return "pipeline"

    if is_debate:
        # Two option names + opposing bullet sets — classic head-to-head.
        # If one side reads like a "before" state (fewer/negative framing)
        # and content is short, before/after reads cleaner than vs/vs.
        if theme_value in ("code_tips", "backend", "frontend") and total_points <= 6:
            return "before_after"
        return "vs"

    if theme_value in ("checklist", "tutorial"):
        return "list"

    if theme_value in ("architecture", "cloud", "system_design", "devops"):
        # Dense structural facts with no head-to-head → 2x2 decision grid.
        if total_points >= 3:
            return "grid"
        return "list"

    # Flat set of facts with no debate structure — default to grid when
    # there's enough content to fill four cells, else a scannable list.
    if total_points >= 4:
        return "grid"
    return "list"
