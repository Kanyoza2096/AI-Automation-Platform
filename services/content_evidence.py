"""Conservative evidence and editorial gates for scheduled social content.

The numeric gate is intentionally narrow: it does not claim to prove every
sentence semantically. It prevents empirical/quantitative claims from passing
unless a verified, traceable KB passage contains the stated values and relevant
subject terms. The editorial lint catches repeated generic transition templates.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

_METRIC_LANGUAGE = re.compile(
    r"\b(?:reduc(?:e|es|ed|tion)|drop(?:ped)?|decreas(?:e|es|ed)|increas(?:e|es|ed)|"
    r"improv(?:e|es|ed|ement)|cut|save(?:d)?|faster|slower|latency|throughput|"
    r"response time|error rate|success rate|uptime|blast radius|traffic exposure|"
    r"detect(?:ion)? time|roll(?:back| back)|recovery time|failure rate|"
    r"percent(?:age)?|\d+(?:\.\d+)?\s?(?:%|milliseconds?|ms|seconds?|minutes?|hours?|x)\b)",
    re.IGNORECASE,
)
_NUMBER = re.compile(
    r"(?<![A-Za-z0-9/])(\d+(?:\.\d+)?)(?:\s*-\s*|\s*)"
    r"(%|percent(?:age)?|ms|milliseconds?|seconds?|minutes?|hours?|x|times?)?"
    r"(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_WORD = re.compile(r"[a-z][a-z0-9-]{2,}", re.IGNORECASE)
_STOP_WORDS = {
    "the", "and", "for", "with", "from", "that", "this", "when", "where",
    "which", "while", "into", "than", "then", "they", "their", "there",
    "often", "more", "most", "some", "such", "can", "could", "would",
    "should", "will", "may", "might", "does", "using", "used", "use",
    "approach", "method", "system", "specific", "example", "next", "step",
    "key", "important", "effective", "particularly", "instance", "contrast",
}
_EDITORIAL_TEMPLATES = (
    re.compile(r"\ba specific example is\b", re.IGNORECASE),
    re.compile(r"\bthe key to (?:using|understanding|solving|building)\b", re.IGNORECASE),
    re.compile(r"\bin the context of\b", re.IGNORECASE),
    re.compile(r"\bthe next step is to consider\b", re.IGNORECASE),
    re.compile(r"\bthis approach is often more effective than\b", re.IGNORECASE),
    re.compile(r"\bfor instance\b", re.IGNORECASE),
    re.compile(r"\bin contrast\b", re.IGNORECASE),
)


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", text or "") if part.strip()]


def _tokens(text: str) -> set[str]:
    return {
        token.lower()
        for token in _WORD.findall(text or "")
        if token.lower() not in _STOP_WORDS
    }


def _numeric_facts(text: str) -> set[str]:
    unit_aliases = {
        "percent": "%", "percentage": "%", "%": "%",
        "millisecond": "ms", "milliseconds": "ms", "ms": "ms",
        "second": "s", "seconds": "s", "s": "s",
        "minute": "min", "minutes": "min", "min": "min",
        "hour": "h", "hours": "h", "h": "h",
        "time": "x", "times": "x", "x": "x",
    }
    facts = set()
    for value, unit in _NUMBER.findall(text or ""):
        normalized_value = str(float(value)).rstrip("0").rstrip(".") if "." in value else str(int(value))
        normalized_unit = unit_aliases.get(unit.lower(), unit.lower()) if unit else ""
        facts.add(f"{normalized_value}:{normalized_unit}")
    return facts


def _verified_sources(evidence: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for item in evidence or []:
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata")
        if not isinstance(metadata, dict):
            continue
        source_id = str(item.get("source_id") or "").strip()
        source_text = str(item.get("text") or "").strip()
        if metadata.get("verified") is True and source_id and source_text:
            sources.append(item)
    return sources


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
        return False, (
            "empirical or quantitative claim requires a verified KB source "
            "with a stable source ID"
        ), []

    matched_source_ids: list[str] = []
    for sentence in metric_sentences:
        numbers = _numeric_facts(sentence)
        claim_tokens = _tokens(sentence)
        matched = None
        for source in sources:
            source_text = str(source.get("text") or "")
            source_numbers = _numeric_facts(source_text)
            source_tokens = _tokens(source_text)
            overlap = claim_tokens & source_tokens
            if numbers.issubset(source_numbers) and len(overlap) >= 2:
                matched = str(source.get("source_id"))
                break
        if not matched:
            return False, (
                "empirical claim has no single verified source containing "
                "its stated values and relevant subject terms: "
                + sentence[:180]
            ), matched_source_ids
        matched_source_ids.append(matched)

    return True, "all empirical claims matched verified evidence", sorted(set(matched_source_ids))


def validate_editorial_specificity(caption: str) -> tuple[bool, str]:
    """Reject captions dominated by generic essay/AI transition templates."""
    matches = [
        pattern.pattern
        for pattern in _EDITORIAL_TEMPLATES
        if pattern.search(caption or "")
    ]
    if len(matches) >= 3:
        return False, (
            "caption relies on repeated generic explanatory transitions; "
            "rewrite around one specific, evidence-led point"
        )
    return True, "editorial specificity check passed"
