"""
Kanyoza Systems AI Platform v10 — Runtime AI Configuration Store.

Holds mutable AI personality / model settings that can be changed at runtime
via the AI Personality Matrix panel without restarting the server.

All writes are in-process only — they do NOT persist to environment variables
and are reset when the server restarts.  Use environment variables for
permanent configuration.

Thread-safe via a module-level lock.
"""
from __future__ import annotations

import threading
from typing import Optional

_lock = threading.Lock()

# Default values — overridden at runtime via update()
_state: dict = {
    # Model routing
    "system_prompt_override": None,   # str | None — replaces the persona prompt when set
    # Personality sliders (0.0 – 1.0)
    "tone_assertiveness": 0.7,
    "tone_humor": 0.2,
    "tone_formality": 0.6,
    # Active mood (must be one of VALID_MOODS)
    "persona_mood": "professional",
}

VALID_MOODS = {"professional", "excited", "curious", "supportive", "playful"}
_FLOAT_SLIDERS = {"tone_assertiveness", "tone_humor", "tone_formality"}


def get() -> dict:
    """Return a shallow copy of the current runtime AI config."""
    with _lock:
        return dict(_state)


def update(**kwargs) -> dict:
    """
    Update one or more runtime AI config fields.

    Raises ValueError for unknown keys or out-of-range values.
    Returns the updated config dict.
    """
    allowed = {"system_prompt_override", "persona_mood"} | _FLOAT_SLIDERS
    unknown = set(kwargs) - allowed
    if unknown:
        raise ValueError(f"Unknown AI config keys: {sorted(unknown)}")

    with _lock:
        for key, val in kwargs.items():
            if key == "persona_mood":
                if val not in VALID_MOODS:
                    raise ValueError(f"persona_mood must be one of {sorted(VALID_MOODS)}")
                _state[key] = val
                # Sync with BotPersona singleton if available
                try:
                    from core.persona import bot_persona
                    bot_persona._mood = val  # type: ignore[attr-defined]
                except Exception:
                    pass
            elif key == "system_prompt_override":
                _state[key] = str(val) if val is not None else None
            elif key in _FLOAT_SLIDERS:
                fval = float(val)
                if not 0.0 <= fval <= 1.0:
                    raise ValueError(f"{key} must be in 0.0–1.0")
                _state[key] = fval

    return get()


def get_effective_system_prompt(base_prompt: str) -> str:
    """
    Return the system prompt to use for AI calls.

    If a runtime override is set, it replaces the base prompt entirely.
    Otherwise the base persona prompt is returned unchanged.
    """
    with _lock:
        override = _state.get("system_prompt_override")
    return override if override else base_prompt
