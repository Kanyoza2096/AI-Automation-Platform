"""
Response humanizer — adjusts bot output to feel natural and on-brand.
"""

import random
import re
import logging
from typing import Optional

logger = logging.getLogger("kanyoza.core.humanizer")

_FILLER_PHRASES = [
    "By the way,", "Worth noting:", "Fun fact:",
    "Here's the thing:", "To put it simply:", "In practice:", "The key insight is:",
]

_SIGN_OFFS = [
    "Let me know if you want to dive deeper!",
    "Happy to explore more if you're curious!",
    "Feel free to ask follow-up questions!",
    "Hope that helps — ask away if you want more!",
]

_ENCOURAGEMENTS = [
    "Great question!", "Nice one to ask about.",
    "Glad you asked!", "That's a good topic to explore.",
]

TECH_EMOJIS = ["💻", "🚀", "⚡", "🔧", "🛠️", "📊", "🎯", "🔥", "✨", "💡", "🧠", "📡"]
SUPPORT_EMOJIS = ["😊", "👍", "🙌", "💪", "✅", "🎉", "❤️"]


class Humanizer:
    def __init__(self, persona=None):
        self._persona = persona

    def humanize(self, text: str, context: str = "chat") -> str:
        if not text:
            return text
        text = self._normalize_whitespace(text)
        if context == "chat":
            text = self._apply_chat_touches(text)
        elif context == "post":
            text = self._apply_post_touches(text)
        text = self._trim_trailing(text)
        return text

    def _apply_chat_touches(self, text: str) -> str:
        mood = self._persona.current_mood if self._persona else "professional"
        if mood == "excited" and random.random() < 0.3:
            encouragement = random.choice(_ENCOURAGEMENTS)
            if not text.startswith(encouragement[:8]):
                text = encouragement + " " + text
        if random.random() < 0.25 and len(text) > 200:
            sign_off = random.choice(_SIGN_OFFS)
            if not text.endswith(("?", "!")):
                text = text.rstrip(".") + ". " + sign_off
            else:
                text = text + " " + sign_off
        if random.random() < 0.2:
            emoji = random.choice(TECH_EMOJIS if "code" in text.lower() else SUPPORT_EMOJIS)
            text = text + " " + emoji
        return text

    def _apply_post_touches(self, text: str) -> str:
        if not any(text.startswith(c) for c in "🚀💻⚡🔥🎯📊🧠"):
            emoji = random.choice(TECH_EMOJIS)
            text = emoji + " " + text
        return text

    def _normalize_whitespace(self, text: str) -> str:
        text = re.sub(r" +", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _trim_trailing(self, text: str) -> str:
        return text.rstrip()

    def pick_response_delay(self, message_length: int) -> float:
        base = min(1.5 + (message_length / 500), 4.0)
        jitter = random.uniform(-0.3, 0.5)
        return max(0.5, base + jitter)
