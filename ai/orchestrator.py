from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from ai.provider import AIProvider, AIRequest, AIResponse, ProviderCapability
from core.events import ProviderFailedEvent
from core.event_bus import get_event_bus

logger = logging.getLogger(__name__)

_FAILOVER_COOLDOWN_S = 60


class AIOrchestrator:
    """Routes AI requests to the best available provider with automatic
    failover, retries, and cost-aware selection.

    The rest of the platform should call this instead of directly calling
    GeminiService or any other provider.
    """

    def __init__(self, providers: list[AIProvider]) -> None:
        self._providers = providers
        self._lock = threading.Lock()
        self._cooldowns: dict[str, float] = {}

    def register(self, provider: AIProvider) -> None:
        with self._lock:
            self._providers.append(provider)
        logger.info("AIOrchestrator: registered provider '%s'", provider.name)

    def _eligible(self, provider: AIProvider, capability: ProviderCapability) -> bool:
        if not provider.supports(capability):
            return False
        if not provider.is_available():
            return False
        cooldown_until = self._cooldowns.get(provider.name, 0)
        if time.monotonic() < cooldown_until:
            logger.debug(
                "AIOrchestrator: %s is in cooldown (%.0fs remaining)",
                provider.name,
                cooldown_until - time.monotonic(),
            )
            return False
        return True

    def _rank(self, providers: list[AIProvider]) -> list[AIProvider]:
        """Rank by cost ascending — cheapest first, then by name for stability."""
        return sorted(providers, key=lambda p: (p.cost_per_1k_tokens, p.name))

    def execute(self, request: AIRequest, max_retries: int = 2) -> AIResponse:
        with self._lock:
            candidates = [
                p for p in self._providers if self._eligible(p, request.capability)
            ]

        if not candidates:
            logger.error("AIOrchestrator: no eligible provider for %s", request.capability)
            return AIResponse(
                text="",
                provider="none",
                model="none",
                latency_ms=0,
            )

        ranked = self._rank(candidates)

        for provider in ranked:
            for attempt in range(1, max_retries + 1):
                try:
                    response = provider.execute(request)
                    if response.success:
                        logger.info(
                            "AIOrchestrator: %s answered %.0fms (attempt %d)",
                            provider.name,
                            response.latency_ms,
                            attempt,
                        )
                        return response
                    logger.warning(
                        "AIOrchestrator: %s returned empty (attempt %d/%d)",
                        provider.name,
                        attempt,
                        max_retries,
                    )
                except Exception as exc:
                    logger.error(
                        "AIOrchestrator: %s raised %s (attempt %d/%d)",
                        provider.name,
                        exc,
                        attempt,
                        max_retries,
                    )

            logger.warning(
                "AIOrchestrator: failing over from %s after %d attempts",
                provider.name,
                max_retries,
            )
            with self._lock:
                self._cooldowns[provider.name] = time.monotonic() + _FAILOVER_COOLDOWN_S
            get_event_bus().publish(
                ProviderFailedEvent(
                    provider=provider.name,
                    error="empty response or exception",
                    capability=request.capability.value,
                )
            )

        return AIResponse(text="", provider="exhausted", model="none", latency_ms=0)

    def chat(self, prompt: str, context: Optional[list] = None, **kw) -> str:
        req = AIRequest(
            prompt=prompt,
            capability=ProviderCapability.CHAT,
            context=context or [],
            **kw,
        )
        return self.execute(req).text

    def generate_structured(self, prompt: str, **kw) -> str:
        req = AIRequest(
            prompt=prompt,
            capability=ProviderCapability.STRUCTURED_JSON,
            thinking_budget=0,
            max_tokens=2048,
            **kw,
        )
        return self.execute(req).text

    def health(self) -> dict:
        with self._lock:
            providers = list(self._providers)
        return {
            p.name: {
                "available": p.is_available(),
                "capabilities": [c.value for c in p.capabilities],
                "cost_per_1k": p.cost_per_1k_tokens,
                "in_cooldown": time.monotonic() < self._cooldowns.get(p.name, 0),
            }
            for p in providers
        }
