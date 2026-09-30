"""
Messenger / chat routing helpers.

Decides when a user message should use the tool-using agent (live web)
vs normal Gemini chat.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Optional

logger = logging.getLogger("kanyoza.chat_routing")

# Opt-out: CHAT_TOOL_AGENT=false
# Opt-in default on when not set? Prefer off unless enabled to avoid extra LLM+search cost.
# Use CHAT_TOOL_AGENT=true to enable.

_CURRENT_FACT_PATTERNS = [
    r"\b(what('?s| is| are)?\s+(happening|trending|new|latest))\b",
    r"\b(today|tonight|this week|this morning|right now|currently|as of)\b",
    r"\b(news about|latest news|breaking)\b",
    r"\b(who won|score|election result)\b",
    r"\b(price of|stock price|exchange rate)\b",
    r"\b(search (the )?(web|internet|online) for)\b",
    r"\b(look up|find out about|google)\b",
    r"\b(202[4-9]|2030)\b",  # recent years often need freshness
]

_COMPILED = [re.compile(p, re.I) for p in _CURRENT_FACT_PATTERNS]


def tool_agent_enabled() -> bool:
    return os.environ.get("CHAT_TOOL_AGENT", "true").lower() in ("1", "true", "yes")


def needs_live_knowledge(text: str) -> bool:
    """Heuristic: does this message likely need current web info?"""
    t = (text or "").strip()
    if len(t) < 8:
        return False
    # Explicit user request
    lower = t.lower()
    if any(x in lower for x in ("search the web", "look this up", "check online", "from the internet")):
        return True
    hits = sum(1 for c in _COMPILED if c.search(t))
    return hits >= 1


def answer_with_tools_if_needed(
    user_message: str,
    *,
    language: str = "english",
    fallback_fn=None,
) -> Optional[str]:
    """
    Sync helper for Messenger path.

    Returns tool-agent answer string, or None if caller should use normal chat.
    """
    if not tool_agent_enabled():
        return None
    if not needs_live_knowledge(user_message):
        return None
    try:
        from services.tool_agent import run_tool_agent_sync

        lang_note = ""
        if language and language != "english":
            lang_note = f" Reply in {language}."
        result = run_tool_agent_sync(
            user_message + lang_note,
            max_steps=3,
        )
        answer = (result or {}).get("answer") or ""
        tools = (result or {}).get("tools_used") or []
        if answer and len(answer.strip()) > 15:
            logger.info(
                "Chat routing: tool_agent used tools=%s preview=%s",
                tools,
                answer[:60],
            )
            return answer.strip()
    except Exception as exc:
        logger.warning("Chat tool_agent failed, falling back: %s", exc)
        if fallback_fn:
            try:
                return fallback_fn()
            except Exception:
                pass
    return None
