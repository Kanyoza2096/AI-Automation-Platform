from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ai.provider import AIProvider, AIRequest, AIResponse, ProviderCapability

if TYPE_CHECKING:
    from services.gemini import GeminiService

logger = logging.getLogger(__name__)


class GeminiProvider(AIProvider):
    """Thin adapter that wraps the existing GeminiService behind the AIProvider
    interface. The underlying service is injected so tests can mock it easily."""

    def __init__(self, gemini_service: "GeminiService") -> None:
        self._svc = gemini_service

    @property
    def name(self) -> str:
        return "gemini"

    @property
    def model(self) -> str:
        return "gemini-2.5-flash"

    @property
    def capabilities(self) -> set[ProviderCapability]:
        return {
            ProviderCapability.CHAT,
            ProviderCapability.STRUCTURED_JSON,
            ProviderCapability.CODE_ANALYSIS,
            ProviderCapability.SENTIMENT,
        }

    @property
    def cost_per_1k_tokens(self) -> float:
        return 0.00015

    def is_available(self) -> bool:
        try:
            return bool(self._svc and self._svc.api_key)
        except Exception:
            return False

    def _execute(self, request: AIRequest) -> str:
        try:
            return self._svc._call(
                prompt=request.prompt,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                thinking_budget=request.thinking_budget,
            )
        except Exception as exc:
            logger.warning("GeminiProvider._execute failed: %s", exc)
            return ""
