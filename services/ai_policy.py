"""KDO-owned AI provider routing policy.

Provider selection is policy, not application/provider coupling.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class AIModelRoute:
    provider: str
    model: str


DEFAULT_AI_POLICY: Mapping[str, AIModelRoute] = {
    "classification": AIModelRoute("cloudflare_ai", "@cf/google/gemma-4-26b-a4b-it"),
    "summarization": AIModelRoute("cloudflare_ai", "@cf/google/gemma-4-26b-a4b-it"),
    "interpretation": AIModelRoute("cloudflare_ai", "@cf/google/gemma-4-26b-a4b-it"),
    "content_generation": AIModelRoute("cloudflare_ai", "@cf/google/gemma-4-26b-a4b-it"),
    "structured_json": AIModelRoute("cloudflare_ai", "@cf/google/gemma-4-26b-a4b-it"),
    "chat": AIModelRoute("cloudflare_ai", "@cf/google/gemma-4-26b-a4b-it"),
}


def select_ai_route(task: str, policy: Mapping[str, AIModelRoute] = DEFAULT_AI_POLICY) -> AIModelRoute:
    try:
        return policy[task]
    except KeyError as exc:
        raise ValueError(f"no AI provider policy for task {task!r}") from exc
