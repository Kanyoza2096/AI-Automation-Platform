"""KDO-owned AI provider routing policy.

Provider selection is policy, not application/provider coupling.
Each task declares an ordered chain of routes. The AI fabric tries them
in order. The first response that passes the capability's validation wins.

Rationale for the ordering:
  - Cloudflare Workers AI remains the primary provider for chat and
    classification workloads where its optimized models are appropriate.
  - Gemini 4 Hydrogen is the primary structured JSON route for accuracy and
    schema-oriented generation; the adapter applies a low thinking level.
  - Gemma 4 31B IT is the second Google route for higher-volume overflow;
    the adapter requests minimal thinking and KDO still validates every result.
  - Cloudflare Llama 3.1 8B FP8 remains the final structured-output fallback.
  - Gemini 3.5 Flash-Lite remains available for other capabilities and as a
    known-compatible model, but is no longer the structured_json primary.
  - Gemini 2.5 Flash is not selected by this policy.

Gemma and Gemini are separate model families. The configured Gemma model ID
is sent through the Google Generative Language API adapter; provider-side
availability and quota are account-dependent and are not assumed by policy.

If a provider becomes unavailable, the AI fabric advances through the
configured chain. The gateway retains provider/model provenance.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class AIModelRoute:
    provider: str
    model: str


# Ordered fallback chains per task. First entry is the preferred provider;
# subsequent entries are fallbacks. Providers must be registered in
# services/connectors/ai_registry.py.
#
# Cloudflare Workers AI model IDs must use the active quantized variants.
DEFAULT_AI_POLICY: Mapping[str, tuple[AIModelRoute, ...]] = {
    "classification": (
        AIModelRoute("cloudflare_ai", "@cf/meta/llama-3.1-8b-instruct-fp8-fast"),
        AIModelRoute("gemini", "gemini-3.5-flash-lite"),
    ),
    "summarization": (
        AIModelRoute("cloudflare_ai", "@cf/meta/llama-3.1-8b-instruct-fp8-fast"),
        AIModelRoute("gemini", "gemini-3.5-flash-lite"),
    ),
    "interpretation": (
        AIModelRoute("cloudflare_ai", "@cf/meta/llama-3.1-8b-instruct-fp8-fast"),
        AIModelRoute("gemini", "gemini-3.5-flash-lite"),
    ),
    "content_generation": (
        AIModelRoute("cloudflare_ai", "@cf/meta/llama-3.3-70b-instruct-fp8-fast"),
        AIModelRoute("gemini", "gemini-3.5-flash-lite"),
    ),
    # Accuracy-first Google model, high-volume Google fallback, then
    # independent Cloudflare fallback. All outputs remain subject to the
    # same downstream parse and required-key/schema validation.
    "structured_json": (
        AIModelRoute("gemini", "gemini-4-hydrogen"),
        AIModelRoute("gemini", "gemma-4-31b-it"),
        AIModelRoute("cloudflare_ai", "@cf/meta/llama-3.3-70b-instruct-fp8-fast"),
    ),
    "chat": (
        AIModelRoute("cloudflare_ai", "@cf/meta/llama-3.1-8b-instruct-fp8-fast"),
        AIModelRoute("gemini", "gemini-3.5-flash-lite"),
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
