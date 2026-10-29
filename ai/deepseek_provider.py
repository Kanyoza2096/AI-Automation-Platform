"""DeepSeek LLM Provider — primary provider for Kanyoza."""

from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

from ai.provider import AIProvider, AIRequest, ProviderCapability

logger = logging.getLogger("kanyoza.ai.deepseek")


class DeepSeekProvider(AIProvider):
    """DeepSeek provider — OpenAI-compatible API. Implements AIProvider ABC."""

    def __init__(self) -> None:
        self._api_key = os.getenv("DEEPSEEK_API_KEY")
        self._base_url = "https://api.deepseek.com/v1"
        self._model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
        self._cost = 0.001

    @property
    def name(self) -> str:
        return "deepseek"

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ProviderCapability]:
        return {ProviderCapability.CHAT, ProviderCapability.STRUCTURED_JSON}

    @property
    def cost_per_1k_tokens(self) -> float:
        return self._cost

    def is_available(self) -> bool:
        return bool(self._api_key)

    def _execute(self, request: AIRequest) -> str:
        if not self._api_key:
            return ""
        try:
            with httpx.Client(timeout=45.0) as client:
                response = client.post(
                    f"{self._base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self._model,
                        "messages": [
                            {"role": "system", "content": "You are a helpful AI assistant."},
                            {"role": "user", "content": request.prompt},
                        ],
                        "max_tokens": request.max_tokens or 1024,
                        "temperature": request.temperature if request.temperature is not None else 0.7,
                    },
                )
                if response.status_code != 200:
                    logger.warning("DeepSeek HTTP %s: %s", response.status_code, response.text[:200])
                    return ""
                data = response.json()
                return (
                    data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                    or ""
                )
        except Exception as exc:
            logger.warning("DeepSeek call failed: %s", exc)
            return ""
