"""KDO-owned AI provider routing policy.

Provider selection is policy, not application/provider coupling.
Each task declares an ordered chain of routes. The AI fabric tries them
in order; the first that succeeds wins.

Rationale for the ordering:
  - Cloudflare Workers AI is the default primary provider across all
    tasks. In production, it has been the more operationally reliable
    provider (no rate-limit failures observed; a different quota pool
    from Google; self-hosted open-weight models).
  - Gemini 2.5 Flash is the fallback. It was originally intended as
    primary for its quality, but the observed rate limits, occasional
    5xx, and the recent credential exposure make it a fallback rather
    than a primary.
  - Small tasks (classification, chat) use Cloudflare's 8B.
  - Large tasks (content generation, structured JSON) use Cloudflare's
    70B, with Gemini as the fallback.

If Cloudflare becomes unavailable, an operator can flip the ordering
by editing this file — the gateways read the policy at call time.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class AIModelRoute:
    provider: str
    model: str


# Ordered fallback chains per task. First entry is the preferred provider;
# subsequent entries are fallbacks. Every chain must include at least one
# route. Providers must be registered in services/connectors/ai_registry.py.
DEFAULT_AI_POLICY: Mapping[str, tuple[AIModelRoute, ...]] = {
    "classification": (
        AIModelRoute("cloudflare_ai", "@cf/meta/llama-3.1-8b-instruct"),
        AIModelRoute("gemini", "gemini-2.5-flash"),
    ),
    "summarization": (
        AIModelRoute("cloudflare_ai", "@cf/meta/llama-3.1-8b-instruct"),
        AIModelRoute("gemini", "gemini-2.5-flash"),
    ),
    "interpretation": (
        AIModelRoute("cloudflare_ai", "@cf/meta/llama-3.1-8b-instruct"),
        AIModelRoute("gemini", "gemini-2.5-flash"),
    ),
    "content_generation": (
        AIModelRoute("cloudflare_ai", "@cf/meta/llama-3.3-70b-instruct-fp8-fast"),
        AIModelRoute("gemini", "gemini-2.5-flash"),
    ),
    "structured_json": (
        AIModelRoute("cloudflare_ai", "@cf/meta/llama-3.3-70b-instruct-fp8-fast"),
        AIModelRoute("gemini", "gemini-2.5-flash"),
    ),
    "chat": (
        AIModelRoute("cloudflare_ai", "@cf/meta/llama-3.1-8b-instruct"),
        AIModelRoute("gemini", "gemini-2.5-flash"),
    ),
}


def select_ai_route(task: str) -> tuple[AIModelRoute, ...]:
    """Return the ordered chain of routes for a task.

    Raises ValueError if the task is not in the policy. Callers should
    decide whether an unknown task is a hard error or a fallback to a
    default chain.
    """
    try:
        return DEFAULT_AI_POLICY[task]
    except KeyError as exc:
        raise ValueError(f"no AI provider policy for task {task!r}") from exc
