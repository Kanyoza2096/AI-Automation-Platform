"""KDO-owned AI provider routing policy.

Provider selection is policy, not application/provider coupling.
Each task declares an ordered chain of routes. The AI fabric tries them
in order; the first that succeeds wins.

Rationale for the ordering:
  - Cloudflare Workers AI is the default primary provider for CHAT and
    classification tasks: it has been operationally reliable and has a
    separate quota pool from Google.
  - Gemini 2.5 Flash is primary for structured_json. Cloudflare's
    Llama 3.3 70B (FP8) has failed the JSON schema contract twice in
    production with structured_parse_failed — HTTP 200 with content
    that parse_structured_json cannot extract. Gemini has been
    reliable on the same prompts. Cloudflare's 8B remains as fallback
    for cost/speed, but is not assumed to succeed where the 70B failed.
  - Small tasks (classification, chat) use Cloudflare's 8B.
  - Large tasks (content generation) use Cloudflare's 70B, with Gemini
    as the fallback.

If Cloudflare becomes unavailable for chat, or Gemini becomes
unreliable for structured output, flip the affected ordering by
editing this file — the gateways read the policy at call time.
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
    # structured_json: Gemini primary. Cloudflare's Llama 3.3 70B has
    # failed the schema contract twice in production with
    # structured_parse_failed. Gemini 2.5 Flash has been reliable on
    # the same prompts. Cloudflare's 8B is the fallback: cheaper and
    # faster, but not assumed to be more schema-reliable than the 70B.
    # The real defense against Cloudflare's structured failures is the
    # parser-tolerance layer in models/content.py, not the route
    # ordering.
    "structured_json": (
        AIModelRoute("gemini", "gemini-2.5-flash"),
        AIModelRoute("cloudflare_ai", "@cf/meta/llama-3.1-8b-instruct"),
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
