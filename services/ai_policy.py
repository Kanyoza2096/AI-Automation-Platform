"""KDO-owned AI provider routing policy.

Provider selection is policy, not application/provider coupling.
Each task declares an ordered chain of routes. The AI fabric tries them
in order; the first that succeeds wins.

Rationale for the ordering (v2 — Free-tier optimized):

  v2 change (2026-10-24) — FREE-TIER MODEL SWAP:
    Swapped in stronger models that remain viable on the Workers Free
    plan's 10,000 neurons/day budget. Two constraints drove the choices:

    - @cf/openai/gpt-oss-120b is PAID-ONLY. Free-plan requests return
      403 / code 5035 [Cloudflare changelog 2026-07-27]. Including it
      would waste a route attempt on every call. Excluded.

    - @cf/deepseek-ai/deepseek-r1-distill-qwen-32b costs 443,756
      neurons per million OUTPUT tokens [Cloudflare pricing]. A single
      reasoning response (~2,250 output tokens) would exhaust the entire
      daily free budget. Excluded.

    What remains on free tier:
      - llama-3.3-70b-instruct-fp8-fast: 204,805 neurons/M output.
        For ~5,000 output tokens/day (10 posts × ~500), ~1,024 neurons
        = ~10% of daily budget. Context window is 24,000 tokens
        (confirmed, not the advertised larger window) — long prompts
        will truncate. Used for content_generation only.

      - mistral-small-3.1-24b-instruct: 50,488 neurons/M output.
        For ~5,000 output tokens/day, ~252 neurons = ~2.5% of daily
        budget. 128,000-token context window. Used for chat,
        summarization, and interpretation — quality upgrade over 8B
        without budget pressure.

  Preserved rationale from v1:
    - Cloudflare Workers AI is the default primary provider for CHAT and
      classification tasks: it has been operationally reliable and has a
      separate quota pool from Google.
    - Gemini 2.5 Flash is primary for structured_json. Cloudflare's
      Llama 3.3 70B (FP8) has failed the JSON schema contract twice in
      production with structured_parse_failed — HTTP 200 with content
      that parse_structured_json cannot extract. Gemini has been
      reliable on the same prompts. Cloudflare's 8B FP8 remains as
      fallback for cost/speed, but is not assumed to succeed where the
      70B failed.
    - The real defense against Cloudflare's structured failures is the
      parser-tolerance layer in models/content.py, not the route ordering.

If Cloudflare becomes unavailable for chat, or Gemini becomes
unreliable for structured output, flip the affected ordering by
editing this file — the gateways read the policy at call time.
Note: "call time" here means process lifetime; changes require a
redeploy (or autoreload in dev), not a live hot-swap.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class AIModelRoute:
    provider: str
    model: str

    def __str__(self) -> str:
        return f"{self.provider}:{self.model}"


# Ordered fallback chains per task. First entry is the preferred provider;
# subsequent entries are fallbacks. Every chain must include at least one
# route. Providers must be registered in services/connectors/ai_registry.py.
#
# Model ID notes:
#  - Base unquantized model IDs (e.g. "@cf/meta/llama-3.1-8b-instruct" without
#    a quant suffix) have been deprecated upstream and return HTTP 410 Gone.
#    Active workloads use the "-fp8-fast" quantized variants, which are on
#    Cloudflare's active catalog and are the recommended replacements.
#  - "-fp8" alone (without "-fast") is NOT a valid model ID on Workers AI.
#    The "-fast" suffix is required. Confirmed against the live catalog.
#
# v2 model notes:
#  - llama-3.3-70b-instruct-fp8-fast: context window is 24,000 tokens.
#    Prompts longer than that will be truncated by the provider, not by us.
#    Do not route long-context tasks (e.g. full-document summarization)
#    to this model without a pre-truncation step.
#  - mistral-small-3.1-24b-instruct: context window is 128,000 tokens.
#    Safe for long-context use. Also supports vision and function calling.
DEFAULT_AI_POLICY: Mapping[str, tuple[AIModelRoute, ...]] = MappingProxyType({
    "classification": (
        AIModelRoute("cloudflare_ai", "@cf/meta/llama-3.1-8b-instruct-fp8-fast"),
        AIModelRoute("gemini", "gemini-2.5-flash"),
    ),
    "summarization": (
        AIModelRoute("cloudflare_ai", "@cf/mistralai/mistral-small-3.1-24b-instruct"),
        AIModelRoute("gemini", "gemini-2.5-flash"),
    ),
    "interpretation": (
        AIModelRoute("cloudflare_ai", "@cf/mistralai/mistral-small-3.1-24b-instruct"),
        AIModelRoute("gemini", "gemini-2.5-flash"),
    ),
    "content_generation": (
        AIModelRoute("cloudflare_ai", "@cf/meta/llama-3.3-70b-instruct-fp8-fast"),
        AIModelRoute("gemini", "gemini-2.5-flash"),
    ),
    # structured_json: Gemini primary. Cloudflare's Llama 3.3 70B FP8 has
    # failed the schema contract twice in production with
    # structured_parse_failed. Gemini 2.5 Flash has been reliable on the
    # same prompts. Cloudflare's 8B FP8 is the fallback: cheaper and
    # faster, but not assumed to be more schema-reliable than the 70B.
    # The real defense against Cloudflare's structured failures is the
    # parser-tolerance layer in models/content.py, not the route ordering.
    "structured_json": (
        AIModelRoute("gemini", "gemini-2.5-flash"),
        AIModelRoute("cloudflare_ai", "@cf/meta/llama-3.1-8b-instruct-fp8-fast"),
    ),
    "chat": (
        AIModelRoute("cloudflare_ai", "@cf/mistralai/mistral-small-3.1-24b-instruct"),
        AIModelRoute("gemini", "gemini-2.5-flash"),
    ),
})


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


def validate_policy() -> None:
    """Validate the policy at import time. Raises ValueError on any issue.

    Catches typos, empty chains, and invalid route shapes before the first
    request hits the AI fabric. Called by bootstrap / application startup.
    """
    if not DEFAULT_AI_POLICY:
        raise ValueError("AI policy is empty")
    for task, chain in DEFAULT_AI_POLICY.items():
        if not chain:
            raise ValueError(f"empty route chain for task {task!r}")
        for route in chain:
            if not route.provider or not route.model:
                raise ValueError(
                    f"invalid route in chain for {task!r}: {route!r}"
                )
