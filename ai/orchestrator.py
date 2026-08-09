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


# ---------------------------------------------------------------------------
# Structured decision telemetry — emitted on every provider selection/failure
# ---------------------------------------------------------------------------


@dataclass
class DecisionEvent:
    """Emitted every time a provider is selected, succeeds, or fails.

    Gives the Director, HealthMonitor, and analytics systems structured
    data to make autonomous decisions about provider routing.
    """
    request_id: str
    capability: str
    chosen_provider: str
    cost_estimate: float
    latency_ms: int
    attempt_count: int
    success: bool
    model: str = ""
    error: Optional[str] = None
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ---------------------------------------------------------------------------
# Explicit exception — so upstream code can't mistake failure for success
# ---------------------------------------------------------------------------


class NoProviderAvailableError(Exception):
    """Raised when no AI provider can handle the request.

    Upstream services MUST catch this and either:
    - Enqueue the job for retry via JobService
    - Escalate via EventBus
    - Mark the workflow as failed
    """
    def __init__(self, capability: str) -> None:
        self.capability = capability
        super().__init__(
            f"No eligible AI provider for capability: {capability}"
        )


class AllProvidersExhaustedError(Exception):
    """Raised when all eligible providers have been tried and failed."""
    def __init__(self, capability: str, attempts: int) -> None:
        self.capability = capability
        self.attempts = attempts
        super().__init__(
            f"All providers exhausted for {capability} after {attempts} attempts"
        )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class AIOrchestrator:
    """Routes AI requests to the best available provider with automatic
    failover, retries, and cost-aware selection.

    The rest of the platform should call this instead of directly calling
    GeminiService or any other provider.

    Two execution paths:
    - execute()       — synchronous, for background workers / task queue
    - execute_async() — non-blocking, for async FastAPI handlers
    """

    def __init__(self, providers: list[AIProvider]) -> None:
        self._providers = providers
        self._lock = threading.Lock()
        self._cooldowns: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Provider management
    # ------------------------------------------------------------------

    def register(self, provider: AIProvider) -> None:
        with self._lock:
            self._providers.append(provider)
        logger.info(
            "AIOrchestrator: registered provider '%s'", provider.name
        )

    # ------------------------------------------------------------------
    # Eligibility & ranking
    # ------------------------------------------------------------------

    def _eligible(
        self, provider: AIProvider, capability: ProviderCapability
    ) -> bool:
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
        return sorted(
            providers,
            key=lambda p: (
                getattr(p, 'cost_per_1k_tokens', float('inf')),
                p.name,
            ),
        )

    # ------------------------------------------------------------------
    # Synchronous execution — for background workers / task queue
    # ------------------------------------------------------------------

    def execute(
        self,
        request: AIRequest,
        max_retries: int = 2,
        timeout_s: float = _DEFAULT_REQUEST_TIMEOUT_S,
    ) -> AIResponse:
        """Execute a request synchronously with failover.

        Raises:
            NoProviderAvailableError: if no providers are eligible.
            AllProvidersExhaustedError: if all eligible providers failed.
        """
        with self._lock:
            candidates = [
                p
                for p in self._providers
                if self._eligible(p, request.capability)
            ]

        if not candidates:
            logger.error(
                "AIOrchestrator: no eligible provider for %s",
                request.capability,
            )
            self._emit_decision(
                request_id=request.id,
                capability=request.capability.value,
                chosen_provider="none",
                cost_estimate=0,
                latency_ms=0,
                attempt_count=0,
                success=False,
                error="No eligible providers",
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
                        logger.info(
                            "AIOrchestrator: %s answered in %dms (attempt %d)",
                            provider.name,
                            elapsed_ms,
                            attempt,
                        )
                        self._emit_decision(
                            request_id=request.id,
                            capability=request.capability.value,
                            chosen_provider=provider.name,
                            cost_estimate=getattr(
                                provider, 'cost_per_1k_tokens', 0
                            ),
                            latency_ms=elapsed_ms,
                            attempt_count=attempt,
                            success=True,
                            model=response.model,
                        )
                        return response

                    logger.warning(
                        "AIOrchestrator: %s returned unsuccessful "
                        "(attempt %d/%d)",
                        provider.name,
                        attempt,
                        max_retries,
                    )
                except Exception as exc:
                    logger.error(
                        "AIOrchestrator: %s raised %s (attempt %d/%d)",
                        provider.name,
                        type(exc).__name__,
                        attempt,
                        max_retries,
                    )

            # Provider exhausted — apply cooldown and publish failure event
            logger.warning(
                "AIOrchestrator: failing over from %s after %d attempts",
                provider.name,
                max_retries,
            )
            with self._lock:
                self._cooldowns[provider.name] = (
                    time.monotonic() + _FAILOVER_COOLDOWN_S
                )

            self._emit_decision(
                request_id=request.id,
                capability=request.capability.value,
                chosen_provider=provider.name,
                cost_estimate=getattr(provider, 'cost_per_1k_tokens', 0),
                latency_ms=0,
                attempt_count=max_retries,
                success=False,
                error="Provider exhausted after retries",
            )

            try:
                get_event_bus().publish(
                    ProviderFailedEvent(
                        provider=provider.name,
                        error="exhausted after retries",
                        capability=request.capability.value,
                    )
                )
            except Exception as exc:
                logger.debug(
                    "Failed to publish ProviderFailedEvent: %s", exc
                )

        raise AllProvidersExhaustedError(
            request.capability, max_retries * len(ranked)
        )

    # ------------------------------------------------------------------
    # Async execution — for FastAPI handlers (non-blocking)
    # ------------------------------------------------------------------

    async def execute_async(
        self,
        request: AIRequest,
        max_retries: int = 2,
        timeout_s: float = _DEFAULT_REQUEST_TIMEOUT_S,
    ) -> AIResponse:
        """Non-blocking wrapper — offloads sync provider calls to thread pool.

        Use this from async FastAPI route handlers and async services.
        Background workers should use execute() directly.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            partial(self.execute, request, max_retries, timeout_s),
        )

    # ------------------------------------------------------------------
    # Convenience methods
    # ------------------------------------------------------------------

    def chat(
        self, prompt: str, context: Optional[list] = None, **kw
    ) -> str:
        """Synchronous chat — returns text only. For background workers."""
        req = AIRequest(
            prompt=prompt,
            capability=ProviderCapability.CHAT,
            context=context or [],
            **kw,
        )
        return self.execute(req).text

    async def chat_async(
        self, prompt: str, context: Optional[list] = None, **kw
    ) -> str:
        """Async chat — returns text only. For FastAPI handlers."""
        req = AIRequest(
            prompt=prompt,
            capability=ProviderCapability.CHAT,
            context=context or [],
            **kw,
        )
        response = await self.execute_async(req)
        return response.text

    def generate_structured(self, prompt: str, **kw) -> str:
        """Synchronous structured generation. For background workers."""
        req = AIRequest(
            prompt=prompt,
            capability=ProviderCapability.STRUCTURED_JSON,
            thinking_budget=0,
            max_tokens=2048,
            **kw,
        )
        return self.execute(req).text

    async def generate_structured_async(self, prompt: str, **kw) -> str:
        """Async structured generation. For FastAPI handlers."""
        req = AIRequest(
            prompt=prompt,
            capability=ProviderCapability.STRUCTURED_JSON,
            thinking_budget=0,
            max_tokens=2048,
            **kw,
        )
        response = await self.execute_async(req)
        return response.text

    # ------------------------------------------------------------------
    # Health & diagnostics
    # ------------------------------------------------------------------

    def health(self) -> dict:
        with self._lock:
            providers = list(self._providers)
        return {
            p.name: {
                "available": p.is_available(),
                "capabilities": [c.value for c in p.capabilities],
                "cost_per_1k": getattr(p, 'cost_per_1k_tokens', None),
                "in_cooldown": time.monotonic()
                < self._cooldowns.get(p.name, 0),
            }
            for p in providers
        }

    # ------------------------------------------------------------------
    # Decision telemetry
    # ------------------------------------------------------------------

    def _emit_decision(
        self,
        request_id: str,
        capability: str,
        chosen_provider: str,
        cost_estimate: float,
        latency_ms: int,
        attempt_count: int,
        success: bool,
        model: str = "",
        error: Optional[str] = None,
    ) -> None:
        """Publish a structured DecisionEvent to the EventBus.

        This gives the Director, HealthMonitor, and analytics systems
        the data they need to make autonomous routing decisions.
        """
        try:
            event = DecisionEvent(
                request_id=request_id,
                capability=capability,
                chosen_provider=chosen_provider,
                cost_estimate=cost_estimate,
                latency_ms=latency_ms,
                attempt_count=attempt_count,
                success=success,
                model=model,
                error=error,
            )
            get_event_bus().publish("ai.decision", event)
        except Exception as exc:
            logger.debug("Failed to publish DecisionEvent: %s", exc)
