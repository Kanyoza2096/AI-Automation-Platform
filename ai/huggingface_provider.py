from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ai.provider import AIProvider, AIRequest, ProviderCapability

if TYPE_CHECKING:
    from services.huggingface import HuggingFaceService

logger = logging.getLogger(__name__)


class HuggingFaceProvider(AIProvider):
    """Adapter that wraps HuggingFaceService as a fallback AI provider."""

    def __init__(self, hf_service: "HuggingFaceService") -> None:
        self._svc = hf_service

    @property
    def name(self) -> str:
        return "huggingface"

    @property
    def model(self) -> str:
        return "mistralai/Mistral-7B-Instruct-v0.3"

    @property
    def capabilities(self) -> set[ProviderCapability]:
        return {
            ProviderCapability.CHAT,
            ProviderCapability.SENTIMENT,
            ProviderCapability.IMAGE_GENERATION,
        }

    @property
    def cost_per_1k_tokens(self) -> float:
        return 0.0

    def is_available(self) -> bool:
        try:
            from config.settings import get_settings
            s = get_settings()
            return bool(getattr(s, "huggingface_api_key", None))
        except Exception:
            return False

    def _execute(self, request: AIRequest) -> str:
        try:
            return self._svc.chat(request.prompt)
        except Exception as exc:
            logger.warning("HuggingFaceProvider._execute failed: %s", exc)
            return ""
