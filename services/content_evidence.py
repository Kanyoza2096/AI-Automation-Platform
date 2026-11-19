"""Conservative evidence and editorial gates for scheduled social content.

The numeric gate is intentionally narrow: it does not claim to prove every
sentence semantically. It prevents empirical/quantitative claims from passing
unless a verified, traceable KB passage contains the stated values and relevant
subject terms. The editorial lint catches repeated generic transition templates.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Iterable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Metric / number vocabulary
# ---------------------------------------------------------------------------
# Unit token vocabulary is defined ONCE and reused by both the "metric language"
# detector and the numeric-fact extractor so the two can never drift apart.
_UNIT_ALIASES: dict[str, str] = {
    "percent": "%", "percentage": "%", "%": "%",
    "millisecond": "ms", "milliseconds": "ms", "ms": "ms",
    "second": "s", "seconds": "s", "sec": "s", "secs": "s", "s": "s",
    "minute": "min", "minutes": "min", "min": "min", "mins": "min",
    "hour": "h", "hours": "h", "hr": "h", "hrs": "h", "h": "h",
    "time": "x", "times": "x", "x": "x",
}

# Longest alternatives first so e.g. "milliseconds" is preferred over "ms".
_UNIT_PATTERN = "|".join(
    sorted((re.escape(u) for u in _UNIT_ALIASES), key=len, reverse=True)
)

_METRIC_LANGUAGE = re.compile(
    r"\b(?:reduc(?:e|es|ed|tion)|drop(?:ped)?|decreas(?:e|es|ed)|increas(?:e|es|ed)|"
    r"improv(?:e|es|ed|ement)|cut|save(?:d)?|faster|slower|latency|throughput|"
    r"response time|error rate|success rate|uptime|blast radius|traffic exposure|"
    r"detect(?:ion)? time|roll(?:back| back)|recovery time|failure rate|"
    r"percent(?:age)?|"
    r"\d+(?:\.\d+)?(?:\s*-\s*|\s*)(?:" + _UNIT_PATTERN + r")(?=\s|[.,;:!?)]|$))",
    re.IGNORECASE,
)

# Capture: (value, unit-or-empty). Unit alternation comes from the shared
# vocabulary, so bare "s"/"min"/"h" are now captured (previously dropped).
# A hyphenated unit ("2-second") is also accepted.
_NUMBER = re.compile(
    r"(?<![A-Za-z0-9/])"
    r"(\d+(?:\.\d+)?)"
    r"(?:\s*-\s*|\s*)"
    r"(?:(?P<unit>" + _UNIT_PATTERN + r"))?"
    r"(?![A-Za-z0-9])",
    re.IGNORECASE,
)

# Keep 2-letter domain terms (db, io, qa, ci, ai, ml, id, ui, ux, ttl, qps, …)
# because they are often the most relevant subject tokens in technical copy.
_WORD = re.compile(r"[a-z][a-z0-9-]{1,}", re.IGNORECASE)

_STOP_WORDS = {
    "the", "and", "for", "with", "from", "that", "this", "when", "where",
    "which", "while", "into", "than", "then", "they", "their", "there",
    "often", "more", "most", "some", "such", "can", "could", "would",
    "should", "will", "may", "might", "does", "using", "used", "use",
    "approach", "method", "system", "specific", "example", "next", "step",
    "key", "important", "effective", "particularly", "instance", "contrast",
}

_EDITORIAL_TEMPLATES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\ba specific example is\b", re.IGNORECASE),
    re.compile(r"\bthe key to (?:using|understanding|solving|building)\b", re.IGNORECASE),
    re.compile(r"\bin the context of\b", re.IGNORECASE),
    re.compile(r"\bthe next step is to consider\b", re.IGNORECASE),
    re.compile(r"\bthis approach is often more effective than\b", re.IGNORECASE),
    re.compile(r"\bfor instance\b", re.IGNORECASE),
    re.compile(r"\bin contrast\b", re.IGNORECASE),
)

# Threshold: number of *distinct* generic templates that, when present in one
# caption, indicates template-driven prose rather than a specific point.
_EDITORIAL_TEMPLATE_LIMIT = 3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", text or "") if part.strip()]


def _tokens(text: str) -> set[str]:
    return {
        token.lower()
        for token in _WORD.findall(text or "")
        if token.lower() not in _STOP_WORDS
    }


def _normalize_value(value: str) -> str:
    """Canonicalise a numeric literal so '1.0', '1', '1.00' all compare equal.

    Uses Decimal (not float) to avoid binary-precision artefacts and to handle
    arbitrarily large integers without loss.
    """
    from decimal import Decimal, InvalidOperation

    try:
        d = Decimal(value)
    except InvalidOperation:  # pragma: no cover - defensive; regex guarantees digits
        return value
    normalized = format(d.normalize(), "f")
    # Decimal.normalize() can yield "1E+2" for large ints; format("f") undoes that.
    return normalized


def _numeric_facts(text: str) -> set[str]:
    """Return the set of 'value:unit' facts present in *text*.

    Unit strings are canonicalised through the shared alias table so that
    '200 ms', '200ms', and '200 milliseconds' all collapse to '200:ms'.
    A unitless number is encoded as 'value:' (empty unit).
    """
    facts: set[str] = set()
    for match in _NUMBER.finditer(text or ""):
        value = _normalize_value(match.group(1))
        raw_unit = match.group("unit")
        unit = _UNIT_ALIASES.get(raw_unit.lower(), "") if raw_unit else ""
        facts.add(f"{value}:{unit}")
    return facts


def _verified_sources(evidence: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter to evidence items with a stable ID, text, and a truthy verified flag.

    The `verified` flag is treated leniently (True / 1 / "true" / "yes") because
    evidence may round-trip through JSON or a database driver that coerces
    booleans; requiring strict identity silently discards valid sources.
    """
    sources: list[dict[str, Any]] = []
    for item in evidence or []:
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata")
        if not isinstance(metadata, dict):
            continue
        source_id = str(item.get("source_id") or "").strip()
        source_text = str(item.get("text") or "").strip()
        verified = metadata.get("verified")
        is_verified = verified is True or verified in (1, "true", "yes", "1", "True")
        if is_verified and source_id and source_text:
            sources.append(item)
    return sources


def _sentence_matches_source(sentence: str, source_text: str) -> bool:
    """True iff *source_text* plausibly supports the empirical claim in *sentence*.

    A match requires:
      * every numeric fact in the sentence to appear in the source, AND
      * at least one shared non-stop subject token (Jaccard-style overlap
        scaled to sentence length so short metric sentences are not penalised).
    """
    numbers = _numeric_facts(sentence)
    if numbers and not numbers.issubset(_numeric_facts(source_text)):
        return False

    claim_tokens = _tokens(sentence)
    source_tokens = _tokens(source_text)
    overlap = claim_tokens & source_tokens
    if not claim_tokens:
        # No subject terms at all — nothing to ground the claim against.
        return False
    # For 1-token claims require that token; otherwise require >= 2.
    required = 1 if len(claim_tokens) <= 2 else 2
    return len(overlap) >= required


# ---------------------------------------------------------------------------
# Public validators
# ---------------------------------------------------------------------------
def validate_caption_evidence(
    caption: str,
    evidence: Iterable[dict[str, Any]] | None,
) -> tuple[bool, str, list[str]]:
    """Require verified source support for empirical/quantitative sentences."""
    if not caption or not caption.strip():
        return False, "caption is empty", []

    evidence_items = list(evidence or [])
    if not evidence_items:
        return False, "no retrieved knowledge evidence is attached to this post", []

    metric_sentences = [
        sentence for sentence in _sentences(caption)
        if _METRIC_LANGUAGE.search(sentence)
    ]
    if not metric_sentences:
        return True, "no empirical performance claim detected", []

    sources = _verified_sources(evidence_items)
    if not sources:
        logger.warning(
            "caption evidence gate: no verified sources (evidence_items=%d)",
            len(evidence_items),
        )
        return False, (
            "empirical or quantitative claim requires a verified KB source "
            "with a stable source ID"
        ), []

    matched_source_ids: list[str] = []
    for sentence in metric_sentences:
        matched = None
        for source in sources:
            if _sentence_matches_source(sentence, str(source.get("text") or "")):
                matched = str(source.get("source_id"))
                break
        if not matched:
            logger.warning(
                "caption evidence gate rejected sentence: %r (numbers=%s tokens=%s evidence_items=%d sources=%d)",
                sentence[:180],
                sorted(_numeric_facts(sentence)),
                sorted(_tokens(sentence)),
                len(evidence_items),
                len(sources),
            )
            return False, (
                "empirical claim has no single verified source containing "
                "its stated values and relevant subject terms: "
                + sentence[:180]
            ), matched_source_ids
        matched_source_ids.append(matched)

    return True, "all empirical claims matched verified evidence", sorted(set(matched_source_ids))


def validate_editorial_specificity(caption: str) -> tuple[bool, str]:
    """Reject captions dominated by generic essay/AI transition templates.

    Uses *distinct* template matches (not raw hit count) so a single legitimate
    "for instance" in a long piece is not penalised.
    """
    matches = [
        pattern.pattern
        for pattern in _EDITORIAL_TEMPLATES
        if pattern.search(caption or "")
    ]
    if len(matches) >= _EDITORIAL_TEMPLATE_LIMIT:
        return False, (
            "caption relies on repeated generic explanatory transitions; "
            "rewrite around one specific, evidence-led point"
        )
    return True, "editorial specificity check passed"
