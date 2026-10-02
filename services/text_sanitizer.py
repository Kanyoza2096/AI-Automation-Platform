"""Caption sanitization at the publish boundary.

Models occasionally emit Markdown despite prompts that forbid it. The
platforms we publish to (Facebook, Instagram, LinkedIn text posts)
render plain text and show Markdown control characters literally.
This module strips those characters as defense-in-depth.

Called at the publish boundary — not at generation — because generation
may target platforms that do render limited Markdown, and the raw form
should be preserved for those cases.
"""
from __future__ import annotations

import re

# Ordered rules; each is (pattern, replacement).
# Order matters: ** must be stripped before * so bold markers don't
# leave stray asterisks behind.
_MARKDOWN_RULES: tuple[tuple[re.Pattern, str], ...] = (
    # Headings: "## Heading" -> "Heading"
    (re.compile(r"^#{1,6}\s+", re.MULTILINE), ""),
    # Bold: **text** or __text__
    (re.compile(r"\*\*(.+?)\*\*", re.DOTALL), r"\1"),
    (re.compile(r"__(.+?)__", re.DOTALL), r"\1"),
    # Italic: *text* or _text_ (careful not to match inside ** that
    # already survived — the bold rules above should have consumed those)
    (re.compile(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)"), r"\1"),
    (re.compile(r"(?<![A-Za-z0-9_])_(?!\s)(.+?)(?<!\s)_(?![A-Za-z0-9_])"), r"\1"),
    # Inline code: `text` -> text
    (re.compile(r"`([^`]+)`"), r"\1"),
    # Code fences: ```lang\n ... \n``` -> the content
    (re.compile(r"```[a-zA-Z0-9]*\n?([\s\S]*?)```"), r"\1"),
    # Blockquote markers at line start: "> text" -> "text"
    (re.compile(r"^\s*>\s?", re.MULTILINE), ""),
    # List markers at line start: "- text" or "* text" or "+ text" -> "• text"
    (re.compile(r"^(\s*)[-*+]\s+", re.MULTILINE), r"\1• "),
    # Numbered list markers: "1. text" -> "• text" (optional — leave
    # numbered lists alone if you prefer. Uncomment to enable.)
    # (re.compile(r"^(\s*)\d+\.\s+", re.MULTILINE), r"\1• "),
)


def sanitize_caption_markdown(caption: str) -> str:
    """Strip Markdown control characters from a caption.

    Idempotent: safe to call on already-clean text. Preserves line
    breaks and whitespace outside the replaced patterns.

    Returns the caption unchanged if it is empty or None.
    """
    if not caption:
        return caption
    out = caption
    for pattern, repl in _MARKDOWN_RULES:
        out = pattern.sub(repl, out)
    # Collapse runs of 3+ blank lines that can result from removed blocks
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out


__all__ = ["sanitize_caption_markdown"]
