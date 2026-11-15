"""Experimental, corpus-independent query understanding for U2 retrieval research.

This module is deliberately offline-only. It preserves the user query and adds
a small, reviewed general-language alias set; it does not inspect corpus text,
benchmark labels, gold IDs, or call an LLM.
"""
from __future__ import annotations

import re

STOP_WORDS = {
    "the", "is", "at", "which", "on", "and", "a", "an",
    "in", "to", "for", "of", "with", "by", "from", "as",
    "this", "that", "it", "or", "are", "was", "be", "been",
    "were", "has", "have", "had", "do", "does", "did",
    "will", "would", "could", "should", "may", "might",
    "can", "shall", "not", "no", "but", "if", "so", "we",
    "you", "he", "she", "they", "them", "their", "our",
    "its", "just", "also", "very", "too", "all", "some",
    "any", "each", "every", "both", "few", "more", "most",
    "other", "such", "only", "own", "same", "than",
    "about", "after", "before", "between", "during",
    "through", "above", "below", "up", "down", "out",
    "off", "over", "under", "again", "further", "then",
    "once", "here", "there", "when", "where", "why",
    "how", "what", "who", "whom",
}

# General-language relationships only; no vocabulary was extracted from KDO's
# corpus or benchmark. Keep this list conservative and version it with tests.
ALIASES = {
    "accurate": ("correct", "accuracy"),
    "correct": ("accurate", "accuracy"),
    "increase": ("rise", "growth", "higher"),
    "decrease": ("reduce", "drop", "lower"),
    "improve": ("better", "improvement"),
    "failure": ("error", "fault"),
    "error": ("failure", "fault"),
    "delay": ("latency", "response"),
    "latency": ("delay", "response"),
    "measure": ("evaluate", "assess"),
    "evaluate": ("measure", "assess"),
    "compare": ("comparison", "versus"),
    "comparison": ("compare",),
    "store": ("persist", "save"),
    "persist": ("store", "persistence"),
    "secure": ("security", "protect"),
    "security": ("secure", "protection"),
    "cost": ("expense", "spend"),
    "fast": ("quick", "low-latency"),
    "slow": ("sluggish", "high-latency"),
    "duplicate": ("repeated",),
    "reliable": ("reliability", "resilient"),
    "reliability": ("reliable", "resilience"),
}

INTENT_HINTS = {
    "how": "procedure",
    "why": "causal_explanation",
    "what": "definition",
    "when": "conditions",
    "should": "decision_guidance",
    "versus": "comparison",
    "compare": "comparison",
    "difference": "comparison",
    "design": "design_guidance",
    "debug": "diagnosis",
    "fix": "remediation",
    "prevent": "prevention",
}


def tokenize_query(query: str) -> list[str]:
    """Apply the same stopword policy as the current offline evaluator."""
    return [
        token for token in re.findall(r"\w+", query.lower())
        if token not in STOP_WORDS
    ]


def understand_query(query: str) -> dict:
    """Return an auditable query trace and a conservative expanded query."""
    terms = tokenize_query(query)
    expanded = list(terms)
    for term in terms:
        for alias in ALIASES.get(term, ()):
            if alias not in expanded:
                expanded.append(alias)

    first_intent_word = next(
        (word for word in re.findall(r"\w+", query.lower())
         if word in INTENT_HINTS),
        None,
    )
    return {
        "original_query": query,
        "intent": INTENT_HINTS.get(first_intent_word, "information_retrieval"),
        "concept_terms": terms,
        "expansion_terms": [term for term in expanded if term not in terms],
        "normalized_query": " ".join(expanded),
    }

RELATION_CONNECTORS = (
    ("even though", "contrast"),
    ("although", "contrast"),
    ("whereas", "contrast"),
    ("while", "contrast"),
    ("but", "contrast"),
    ("despite", "contrast"),
    ("versus", "comparison"),
    ("compared with", "comparison"),
    ("compared to", "comparison"),
    ("because", "causal"),
    ("since", "causal"),
    ("therefore", "causal"),
)


def decompose_query(query: str) -> dict:
    """Extract shallow clause/relationship structure without corpus access."""
    parts = [query]
    relations = []
    for connector, relation in RELATION_CONNECTORS:
        next_parts = []
        for part in parts:
            split_parts = re.split(
                r"\b" + re.escape(connector) + r"\b",
                part,
                flags=re.IGNORECASE,
            )
            if len(split_parts) > 1:
                relations.append({"connector": connector, "relation": relation})
                next_parts.extend(split_parts)
            else:
                next_parts.append(part)
        parts = next_parts

    clauses = [
        part.strip(" ,?.;:")
        for part in parts
        if len(tokenize_query(part)) >= 2
    ]
    trace = understand_query(query)
    words = re.findall(r"\w+", query.lower())
    if "why" in words:
        intent = "causal_explanation"
    elif "how" in words:
        intent = "procedure"
    elif any(item["relation"] == "comparison" for item in relations):
        intent = "comparison"
    else:
        intent = trace["intent"]
    return {
        **trace,
        "intent": intent,
        "relations": relations,
        "clauses": clauses,
    }


def reciprocal_rank_fusion(
    ranked_lists: list[list[str]],
    weights: list[float] | None = None,
    rrf_k: int = 10,
    top_k: int = 5,
) -> list[tuple[str, float]]:
    """Fuse ranked record IDs; weights are explicit and independently testable."""
    if rrf_k < 1 or top_k < 1:
        raise ValueError("rrf_k and top_k must be positive")
    if weights is None:
        weights = [1.0] * len(ranked_lists)
    if len(weights) != len(ranked_lists):
        raise ValueError("weights must match ranked_lists")
    if any(weight <= 0 for weight in weights):
        raise ValueError("all weights must be positive")

    scores = {}
    for ranked, weight in zip(ranked_lists, weights):
        for rank, record_id in enumerate(ranked, start=1):
            scores[record_id] = scores.get(record_id, 0.0) + (
                weight / (rrf_k + rank)
            )
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:top_k]
