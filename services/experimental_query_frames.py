"""Offline-only, deterministic query-frame extraction for U2 research.

The frame is an auditable interpretation of question structure, not a
replacement query and not a runtime retrieval component.
"""
from __future__ import annotations

import re

def _intent(query: str) -> str:
    """Classify the primary information need using conservative lexical cues."""
    if re.search(
        r"\\b(?:difference between|trade-offs? between|versus|compared with|compared to|differ(?: from)?)\\b",
        query, re.I,
    ):
        return "comparison"

    question = re.search(r"\\b(why|how|what|when|where|which)\\b", query, re.I)
    if not question:
        return "information_retrieval"

    word = question.group(1).lower()
    tail = query[question.end():]
    if word == "why":
        return "causal_explanation"
    if word == "how":
        if re.match(r"\\s+should\\b", tail, re.I):
            if re.search(r"\\b(?:build|design|architect|develop|create)\\w*\\b", tail, re.I):
                return "design_guidance"
            return "decision_guidance"
        if re.search(
            r"\\b(?:help|reduce|increase|worsen|protect|enable|allow|cause|lead)\\b",
            tail, re.I,
        ):
            return "causal_explanation"
        return "procedure"
    if word == "what":
        if re.match(r"\\s+should\\b", tail, re.I):
            return "decision_guidance"
        if re.search(
            r"\\b(?:method|structure|tool|approach|strategy)\\b",
            tail, re.I,
        ):
            return "selection"
        if re.search(r"\\b(?:contain|cover|include|need|require)\\w*\\b", tail, re.I):
            return "requirements_identification"
        return "definition_or_identification"
    return {
        "when": "conditions",
        "where": "location_or_scope",
        "which": "selection",
    }.get(word, "information_retrieval")


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
    left, right = match.groups()
    # Do not absorb a subordinate condition into the second comparison operand.
    right = re.split(r"\b(?:when|while|if|unless|without|because|after|before)\b", right, maxsplit=1, flags=re.I)[0]
    return [part.strip(" ,") for part in (left, right) if part.strip(" ,")]


def _relations(query: str) -> list[dict[str, str]]:
    found = []
    for connector, relation in _RELATIONS:
        if re.search(r"\b" + re.escape(connector) + r"\b", query, re.I):
            found.append({"connector": connector, "type": relation})
    return found


def _has_negation_or_exclusion(query: str) -> bool:
    """Detect exclusions without misclassifying numeric bounds or idioms."""
    candidate = _NUMERIC.sub(" ", query)
    candidate = re.sub(r"\bnot\s+(?:only|just)\b", " ", candidate, flags=re.I)
    return bool(re.search(
        r"\b(?:not|no|never|without|unless|avoid|prevent)\b",
        candidate,
        re.I,
    ))

def _secondary_intents(query: str) -> list[dict[str, str]]:
    """Capture additional explicit question clauses joined by and/semicolon."""
    found = []
    pattern = re.compile(
        r"\b(?:and|;)\s*(why|how|what|when|where|which)\b[^?;]*",
        re.I,
    )
    for match in pattern.finditer(query):
        clause = match.group(0)
        question = re.search(r"(why|how|what|when|where|which)\b", clause, re.I)
        if question:
            found.append({
                "question_word": question.group(1).lower(),
                "intent": _intent(clause[question.start():]),
                "clause": clause.strip(" ,"),
            })
    return found


def extract_query_frame(query: str) -> dict:
    """Return intent, concepts, explicit operands, relations and constraints."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")

    intent = _intent(query)

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
        "secondary_intents": _secondary_intents(query),
        "concept_terms": list(dict.fromkeys(terms)),
        "comparison_operands": _comparison_operands(query),
        "relations": _relations(query),
        "constraints": constraints,
        "has_negation_or_exclusion": _has_negation_or_exclusion(query),
        "frame_version": "u2-query-frame-v1",
    }
