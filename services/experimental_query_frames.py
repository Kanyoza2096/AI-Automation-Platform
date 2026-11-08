"""Offline-only, deterministic query-frame extraction for U2 research.

The frame is an auditable interpretation of question structure, not a
replacement query and not a runtime retrieval component.
"""
from __future__ import annotations

import re

_INTENTS = (
    (r"^\s*why\b", "causal_explanation"),
    (r"^\s*how\b", "procedure"),
    (r"^\s*what\b", "definition_or_identification"),
    (r"^\s*when\b", "conditions"),
    (r"^\s*where\b", "location_or_scope"),
    (r"^\s*which\b", "selection"),
    (r"^\s*(?:is|are|can|could|does|do|should|would)\b", "verification_or_guidance"),
    (r"\b(?:difference|versus|compared with|compared to)\b", "comparison"),
)

_RELATIONS = (
    ("even though", "contrast"),
    ("although", "contrast"),
    ("whereas", "contrast"),
    ("while", "contrast_or_temporal"),
    ("but", "contrast"),
    ("despite", "contrast"),
    ("because", "cause"),
    ("therefore", "effect"),
    ("so that", "purpose"),
    ("if", "condition"),
    ("unless", "negative_condition"),
    ("when", "temporal_or_condition"),
    ("after", "sequence"),
    ("before", "sequence"),
    ("without", "exclusion"),
    ("beyond", "scope_extension"),
    ("instead of", "substitution"),
    ("between", "comparison_or_relation"),
    ("versus", "comparison"),
    ("compared with", "comparison"),
    ("compared to", "comparison"),
)

_STOP = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on",
    "for", "with", "by", "from", "as", "is", "are", "was", "were",
    "be", "been", "being", "do", "does", "did", "can", "could",
    "should", "would", "will", "may", "might", "what", "why", "how",
    "when", "where", "which", "who", "that", "this", "it", "we",
    "our", "my", "your", "their", "its", "after", "before", "while",
}

_COMPLEXITY = re.compile(
    r"\b(?:constant|logarithmic|linear|linearithmic|quadratic|"
    r"cubic|exponential)\s+time\b", re.I
)
_NUMERIC = re.compile(
    r"\b(?:at least|at most|no more than|no less than|"
    r"exactly|up to|under|within)\s+\d+(?:\.\d+)?\b", re.I
)
_QUALIFIER = re.compile(
    r"\b(?:without|beyond|instead of|rather than|while|when|"
    r"if|unless|after|before)\b[^,;?.]*", re.I
)


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-zA-Z0-9_+#.-]+", text.lower())
            if t not in _STOP]


def _comparison_operands(query: str) -> list[str]:
    """Extract explicit operands around 'between X and Y' only."""
    match = re.search(
        r"\bbetween\s+(.+?)\s+and\s+(.+?)(?:[?.;,]|$)",
        query, re.I,
    )
    if not match:
        return []
    return [part.strip(" ,") for part in match.groups() if part.strip(" ,")]


def _relations(query: str) -> list[dict[str, str]]:
    found = []
    for connector, relation in _RELATIONS:
        if re.search(r"\b" + re.escape(connector) + r"\b", query, re.I):
            found.append({"connector": connector, "type": relation})
    return found


def extract_query_frame(query: str) -> dict:
    """Return intent, concepts, explicit operands, relations and constraints."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")

    # Explicit comparison structure takes precedence over a generic opener.
    if re.search(r"\\b(?:difference between|trade-offs? between|versus|compared with|compared to)\\b", query, re.I):
        intent = "comparison"
    else:
        intent = "information_retrieval"
        for pattern, label in _INTENTS:
            if re.search(pattern, query, re.I):
                intent = label
                break

    complexity = [m.group(0) for m in _COMPLEXITY.finditer(query)]
    numeric = [m.group(0) for m in _NUMERIC.finditer(query)]
    qualifiers = [m.group(0).strip(" ,") for m in _QUALIFIER.finditer(query)]
    constraints = []
    constraints.extend({"kind": "complexity", "text": x} for x in complexity)
    constraints.extend({"kind": "numeric_bound", "text": x} for x in numeric)
    constraints.extend({"kind": "qualifier", "text": x} for x in qualifiers)

    # Preserve the complete question; target_terms are descriptive metadata,
    # never used to overwrite or silently narrow the original query.
    terms = _tokens(query)
    return {
        "original_query": query,
        "intent": intent,
        "concept_terms": list(dict.fromkeys(terms)),
        "comparison_operands": _comparison_operands(query),
        "relations": _relations(query),
        "constraints": constraints,
        "has_negation_or_exclusion": bool(
            re.search(r"\b(?:not|no|never|without|unless|avoid|prevent)\b",
                      query, re.I)
        ),
        "frame_version": "u2-query-frame-v1",
    }
