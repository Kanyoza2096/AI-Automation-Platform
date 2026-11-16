"""KDO-owned AI provider routing policy.

Provider selection is policy, not application/provider coupling.
Each task declares an ordered chain of routes. The AI fabric tries them
in order. The first response that passes the capability's validation wins.

Rationale for the ordering:
  - Cloudflare Workers AI remains the primary provider for chat and
    classification workloads where its optimized models are appropriate.
  - Gemini 3.5 Flash-Lite is the preferred Google model for structured JSON
    generation. Google AI Studio's current model catalogue lists
    gemini-3.5-flash-lite and marks gemini-2.5-flash deprecated.
  - Cloudflare Llama 3.1 8B FP8 remains the final structured-output fallback.
    Every structured response must still pass KDO's JSON/schema validation.
  - Gemini 2.5 Flash is no longer selected by this policy. Gemini 2.5
    Flash-Lite remains available to some existing API users, but is not
    selected for new routing because Google recommends newer models.

Gemma note:
  - Do not confuse Google's Gemma family with Gemini 4. Google's current
    AI Studio model catalogue lists Gemini 4 Carbon, Argon and Hydrogen,
    while Gemma is a separate model family.
  - A second Google-hosted Gemma route is intentionally not activated until
    its exact API endpoint/model identifier and structured-output support
    are confirmed. The string "gemma-4" alone is not enough to establish
    compatibility with the Gemini generateContent adapter.

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
    # Structured planning uses Google's current Flash-Lite model first.
    # The final Cloudflare fallback is accepted only when the returned
    # content passes the same strict JSON/schema checks as every provider.
    # A separate Google Gemma route remains disabled pending confirmation
    # of its exact endpoint and structured-output contract.
    "structured_json": (
        AIModelRoute("gemini", "gemini-3.5-flash-lite"),
        AIModelRoute("cloudflare_ai", "@cf/meta/llama-3.1-8b-instruct-fp8-fast"),
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
