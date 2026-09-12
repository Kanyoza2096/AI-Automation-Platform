"""
KANYOZA AI ORCHESTRATOR v2.1 — Multi-Provider Failover Router.

v2.1:
  - DeepSeek as primary provider (free tier, generous limits)
  - Gemini as fallback
  - Groq + Mistral as additional fallbacks
  - Correct imports: services.gemini → ai.gemini_provider
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import partial
from typing import Optional

from ai.provider import AIProvider, AIRequest, AIResponse, ProviderCapability
from core.events import ProviderFailedEvent
from core.event_bus import get_event_bus

logger = logging.getLogger(__name__)

_FAILOVER_COOLDOWN_S = 60
_DEFAULT_REQUEST_TIMEOUT_S = 30.0


@dataclass
class DecisionEvent:
    request_id: str
    capability: str
    chosen_provider: str
    cost_estimate: float
    latency_ms: int
    attempt_count: int
    success: bool
    model: str = ""
    error: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class NoProviderAvailableError(Exception):
    def __init__(self, capability: str) -> None:
        self.capability = capability
        super().__init__(f"No eligible AI provider for capability: {capability}")


class AllProvidersExhaustedError(Exception):
    def __init__(self, capability: str, attempts: int) -> None:
        self.capability = capability
        self.attempts = attempts
        super().__init__(f"All providers exhausted for {capability} after {attempts} attempts")


class AIOrchestrator:
    """Routes AI requests with automatic failover, retries, and cost-aware selection."""

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
            return False
        return True

    def _rank(self, providers: list[AIProvider]) -> list[AIProvider]:
        return sorted(
            providers,
            key=lambda p: (getattr(p, 'cost_per_1k_tokens', float('inf')), p.name),
        )

    def execute(
        self,
        request: AIRequest,
        max_retries: int = 2,
        timeout_s: float = _DEFAULT_REQUEST_TIMEOUT_S,
    ) -> AIResponse:
        with self._lock:
            candidates = [p for p in self._providers if self._eligible(p, request.capability)]

        if not candidates:
            self._emit_decision(
                request_id=request.id, capability=request.capability.value,
                chosen_provider="none", cost_estimate=0, latency_ms=0,
                attempt_count=0, success=False, error="No eligible providers",
            )
            raise NoProviderAvailableError(request.capability)

        ranked = self._rank(candidates)

        for provider in ranked:
            for attempt in range(1, max_retries + 1):
                try:
                    start = time.monotonic()
                    response = provider.execute(request)
                    elapsed_ms = int((time.monotonic() - start) * 1000)

                    if response.success:
                        self._emit_decision(
                            request_id=request.id, capability=request.capability.value,
                            chosen_provider=provider.name,
                            cost_estimate=getattr(provider, 'cost_per_1k_tokens', 0),
                            latency_ms=elapsed_ms, attempt_count=attempt,
                            success=True, model=response.model,
                        )
                        return response
                except Exception as exc:
                    logger.error(
                        "AIOrchestrator: %s raised %s (attempt %d/%d)",
                        provider.name, type(exc).__name__, attempt, max_retries,
                    )

            with self._lock:
                self._cooldowns[provider.name] = time.monotonic() + _FAILOVER_COOLDOWN_S

            self._emit_decision(
                request_id=request.id, capability=request.capability.value,
                chosen_provider=provider.name,
                cost_estimate=getattr(provider, 'cost_per_1k_tokens', 0),
                latency_ms=0, attempt_count=max_retries,
                success=False, error="Provider exhausted",
            )

            try:
                get_event_bus().publish_sync(
                    ProviderFailedEvent(
                        provider=provider.name,
                        error="exhausted after retries",
                        capability=request.capability.value,
                    )
                )
            except Exception:
                pass

        raise AllProvidersExhaustedError(request.capability, max_retries * len(ranked))

    async def execute_async(
        self, request: AIRequest, max_retries: int = 2,
        timeout_s: float = _DEFAULT_REQUEST_TIMEOUT_S,
    ) -> AIResponse:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, partial(self.execute, request, max_retries, timeout_s)
        )

    def chat(self, prompt: str, context: Optional[list] = None, **kw) -> str:
        req = AIRequest(prompt=prompt, capability=ProviderCapability.CHAT, context=context or [], **kw)
        return self.execute(req).text

    async def chat_async(self, prompt: str, context: Optional[list] = None, **kw) -> str:
        req = AIRequest(prompt=prompt, capability=ProviderCapability.CHAT, context=context or [], **kw)
        response = await self.execute_async(req)
        return response.text

    def generate_structured(self, prompt: str, **kw) -> str:
        req = AIRequest(
            prompt=prompt, capability=ProviderCapability.STRUCTURED_JSON,
            thinking_budget=0, max_tokens=2048, **kw,
        )
        return self.execute(req).text

    async def generate_structured_async(self, prompt: str, **kw) -> str:
        req = AIRequest(
            prompt=prompt, capability=ProviderCapability.STRUCTURED_JSON,
            thinking_budget=0, max_tokens=2048, **kw,
        )
        response = await self.execute_async(req)
        return response.text

    def health(self) -> dict:
        with self._lock:
            providers = list(self._providers)
        return {
            p.name: {
                "available": p.is_available(),
                "capabilities": [c.value for c in p.capabilities],
                "cost_per_1k": getattr(p, 'cost_per_1k_tokens', None),
                "in_cooldown": time.monotonic() < self._cooldowns.get(p.name, 0),
            }
            for p in providers
        }

    def _emit_decision(
        self, request_id: str, capability: str, chosen_provider: str,
        cost_estimate: float, latency_ms: int, attempt_count: int,
        success: bool, model: str = "", error: Optional[str] = None,
    ) -> None:
        try:
            event = DecisionEvent(
                request_id=request_id, capability=capability,
                chosen_provider=chosen_provider, cost_estimate=cost_estimate,
                latency_ms=latency_ms, attempt_count=attempt_count,
                success=success, model=model, error=error,
            )
            bus = get_event_bus()
            # get_event_bus may be async in some builds — never leave coroutine unawaited
            import inspect
            if inspect.iscoroutine(bus):
                return  # best-effort; avoid RuntimeWarning in sync path
            if hasattr(bus, "publish_sync"):
                bus.publish_sync("ai.decision", event)
            elif hasattr(bus, "publish"):
                result = bus.publish("ai.decision", event)
                if inspect.iscoroutine(result):
                    try:
                        import asyncio
                        loop = asyncio.get_running_loop()
                        loop.create_task(result)
                    except RuntimeError:
                        pass
        except Exception:
            pass


# ---------------------------------------------------------------------------
# FACTORY
# ---------------------------------------------------------------------------

def create_default_orchestrator() -> AIOrchestrator:
    """Create AIOrchestrator with Gemini primary, then DeepSeek / Groq / Mistral."""
    providers: list[AIProvider] = []

    # 1. Gemini (primary — production default)
    try:
        from services.gemini import gemini
        from ai.gemini_provider import GeminiProvider
        gemini_provider = GeminiProvider(gemini_service=gemini)
        if gemini_provider.is_available():
            providers.append(gemini_provider)
            logger.info("AIOrchestrator: registered Gemini (primary)")
    except Exception as exc:
        logger.warning("AIOrchestrator: Gemini registration failed: %s", exc)

    # 2. DeepSeek (fallback — only if balanced/configured)
    try:
        from ai.deepseek_provider import DeepSeekProvider
        deepseek = DeepSeekProvider()
        if deepseek.is_available():
            providers.append(deepseek)
            logger.info("AIOrchestrator: registered DeepSeek (fallback)")
    except Exception as exc:
        logger.warning("AIOrchestrator: DeepSeek registration failed: %s", exc)

    # 3. Groq
    try:
        from ai.providers.groq_provider import GroqProvider
        groq = GroqProvider()
        if groq.is_available():
            providers.append(groq)
            logger.info("AIOrchestrator: registered LLaMA (Groq)")
    except Exception as exc:
        logger.debug("AIOrchestrator: Groq not found: %s", exc)

    # 4. Mistral
    try:
        from ai.providers.mistral_provider import MistralProvider
        mistral = MistralProvider()
        if mistral.is_available():
            providers.append(mistral)
            logger.info("AIOrchestrator: registered Mistral (OpenRouter)")
    except Exception as exc:
        logger.debug("AIOrchestrator: Mistral not found: %s", exc)

    if not providers:
        logger.warning("AIOrchestrator: NO providers registered!")
    else:
        logger.info("AIOrchestrator: %d providers ready", len(providers))

    return AIOrchestrator(providers)


__all__ = [
    "AIOrchestrator",
    "DecisionEvent",
    "NoProviderAvailableError",
    "AllProvidersExhaustedError",
    "create_default_orchestrator",
]
