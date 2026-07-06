"""
Dynamic persona engine for Kanyoza Systems Bot.
Manages mood, communication style, and persona consistency.
"""

import random
import logging
from datetime import datetime, timezone
from typing import Dict, Optional

logger = logging.getLogger("kanyoza.core.persona")

MOODS = ["professional", "excited", "curious", "supportive", "playful"]

MOOD_TRANSITIONS: Dict[str, Dict[str, float]] = {
    "professional": {"professional": 0.5, "curious": 0.25, "supportive": 0.15, "excited": 0.07, "playful": 0.03},
    "excited":      {"excited": 0.3, "professional": 0.3, "curious": 0.2, "supportive": 0.1, "playful": 0.1},
    "curious":      {"curious": 0.35, "professional": 0.25, "excited": 0.2, "supportive": 0.15, "playful": 0.05},
    "supportive":   {"supportive": 0.4, "professional": 0.3, "curious": 0.15, "excited": 0.1, "playful": 0.05},
    "playful":      {"playful": 0.25, "excited": 0.3, "curious": 0.25, "professional": 0.15, "supportive": 0.05},
}

PERSONA_PROMPTS: Dict[str, str] = {
    "professional": (
        "Respond in a clear, precise, professional tone. Use structured formatting "
        "where appropriate. Be concise but comprehensive."
    ),
    "excited": (
        "You're enthusiastic and energetic today! Share your genuine excitement about "
        "technology. Use varied sentence lengths and occasional emphasis."
    ),
    "curious": (
        "Approach topics with intellectual curiosity. Ask a thoughtful follow-up question "
        "when relevant. Show genuine interest in the user's perspective."
    ),
    "supportive": (
        "Be warm, encouraging, and patient. Help the user feel comfortable asking questions. "
        "Validate their curiosity and efforts."
    ),
    "playful": (
        "Be light-hearted and friendly while staying informative. Use analogies and "
        "relatable examples. Keep it fun but still helpful."
    ),
}


class BotPersona:
    def __init__(self, starting_mood: str = "professional"):
        self._mood = starting_mood if starting_mood in MOODS else "professional"
        self._mood_history = [self._mood]
        self._message_count = 0
        self._last_transition: Optional[datetime] = None

    @property
    def current_mood(self) -> str:
        return self._mood

    @property
    def prompt_suffix(self) -> str:
        return PERSONA_PROMPTS.get(self._mood, PERSONA_PROMPTS["professional"])

    @property
    def name(self) -> str:
        return "KanyozaBot"

    @property
    def tagline(self) -> str:
        return "Your AI tech companion from Malawi 🇲🇼"

    def maybe_transition(self) -> bool:
        """
        Possibly transition to a new mood based on probability matrix.
        Returns True if a transition occurred.
        """
        self._message_count += 1
        if self._message_count % 5 != 0:
            return False

        transitions = MOOD_TRANSITIONS.get(self._mood, {})
        moods = list(transitions.keys())
        weights = [transitions[m] for m in moods]
        new_mood = random.choices(moods, weights=weights, k=1)[0]

        if new_mood != self._mood:
            logger.debug("Persona mood: %s → %s", self._mood, new_mood)
            self._mood = new_mood
            self._mood_history.append(new_mood)
            if len(self._mood_history) > 20:
                self._mood_history = self._mood_history[-20:]
            self._last_transition = datetime.now(timezone.utc)
            return True
        return False

    def build_system_prompt(self, user_profile: Optional[Dict] = None, ai_profile: Optional[Any] = None) -> str:
        """
        Build the full Gemini system prompt for this persona state.
        Uses AIProfile if provided, otherwise falls back to a default.
        """
        if ai_profile is None:
            # Fallback to default if no profile is injected (e.g. legacy callers)
            from models.ai_profile import AIProfile
            ai_profile = AIProfile.create_default("default_ws")
            
        base_prompt = ai_profile.build_system_prompt()
        
        name = (user_profile or {}).get("name", "there")
        location = (user_profile or {}).get("location", "")
        profession = (user_profile or {}).get("profession", "")

        context_parts = []
        if name and name != "there":
            context_parts.append(f"The user's name is {name}.")
        if location:
            context_parts.append(f"They are from {location}.")
        if profession:
            context_parts.append(f"Their profession: {profession}.")
        user_context = " ".join(context_parts)

        return f"""{base_prompt}

Current Mood/Tone:
{self.prompt_suffix}

{user_context}

Guidelines:
- Never reveal your underlying AI model or training details
- For code: use proper markdown blocks with language identifiers
- Keep responses focused and actionable — avoid padding
- When unsure, say so clearly rather than guessing
"""

    def to_dict(self) -> Dict:
        return {
            "current_mood": self._mood,
            "message_count": self._message_count,
            "last_transition": self._last_transition.isoformat() if self._last_transition else None,
        }


# ---------------------------------------------------------------------------
# Module-level singleton — imported by ai_runtime and other callers
# ---------------------------------------------------------------------------
bot_persona = BotPersona()
