"""
Text processing utilities — pure functions, no side effects.
"""

from __future__ import annotations

import html
import re
from typing import List


def truncate(text: str, max_length: int, suffix: str = "…") -> str:
    """
    Truncate *text* to at most *max_length* characters.
    Appends *suffix* when truncation occurs.
    """
    if not text or len(text) <= max_length:
        return text
    cut = max_length - len(suffix)
    if cut <= 0:
        return suffix[:max_length]
    return text[:cut].rstrip() + suffix


def sanitize_html(text: str) -> str:
    """
    Escape HTML special characters to prevent injection.
    Use before inserting user content into any HTML context.
    """
    return html.escape(text, quote=True)


def extract_hashtags(text: str) -> List[str]:
    """
    Extract all hashtags from *text*, returned without the leading #.
    Preserves order; de-duplicates while keeping first occurrence.
    """
    found = re.findall(r"#(\w+)", text)
    seen: set = set()
    result: List[str] = []
    for tag in found:
        if tag.lower() not in seen:
            seen.add(tag.lower())
            result.append(tag)
    return result


def word_count(text: str) -> int:
    """Return the number of whitespace-delimited words in *text*."""
    return len(text.split()) if text.strip() else 0


def normalize_whitespace(text: str) -> str:
    """Collapse multiple whitespace characters into a single space."""
    return re.sub(r"\s+", " ", text).strip()


def split_sentences(text: str, max_chars: int = 2000) -> List[str]:
    """
    Split *text* into chunks of at most *max_chars* characters,
    breaking at sentence boundaries when possible.
    """
    if len(text) <= max_chars:
        return [text]

    chunks: List[str] = []
    sentences = re.split(r"(?<=[.!?])\s+", text)
    current = ""

    for sentence in sentences:
        if len(current) + len(sentence) + 1 <= max_chars:
            current = (current + " " + sentence).strip()
        else:
            if current:
                chunks.append(current)
            # Handle a single sentence longer than max_chars
            while len(sentence) > max_chars:
                chunks.append(sentence[:max_chars])
                sentence = sentence[max_chars:]
            current = sentence

    if current:
        chunks.append(current)

    return chunks


def camel_to_snake(name: str) -> str:
    """Convert CamelCase to snake_case."""
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()
