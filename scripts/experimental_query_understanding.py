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
