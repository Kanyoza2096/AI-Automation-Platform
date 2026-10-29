"""
Mistral Provider via OpenRouter — FREE tier.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

from ai.provider import AIProvider, AIRequest, AIResponse, ProviderCapability  # ✅ CORRECT

logger = logging.getLogger("kanyoza.ai.mistral")


class MistralProvider(AIProvider):
    """Mistral 7B via OpenRouter — FREE tier."""

    name = "mistral"
    cost_per_1k_tokens = 0.0

    def __init__(self):
        self.api_key = os.environ.get("OPENROUTER_API_KEY", "")
        self.base_url = "https://openrouter.ai/api/v1"
        self._capabilities = {
            ProviderCapability.CHAT,
        }

    @property
    def capabilities(self):
        return self._capabilities

    def supports(self, capability: ProviderCapability) -> bool:
        return capability in self._capabilities

    def is_available(self) -> bool:
        return bool(self.api_key)

    def execute(self, request: AIRequest) -> AIResponse:
        import requests

        if not self.api_key:
            return AIResponse(
                text="", provider=self.name, model="mistralai/mistral-7b-instruct:free",
                error="OPENROUTER_API_KEY not configured",
            )

        try:
            url = f"{self.base_url}/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://omnexa-autonomy.onrender.com",
                "X-Title": "Kanyoza AI",
            }
            payload = {
                "model": "mistralai/mistral-7b-instruct:free",
                "messages": [{"role": "user", "content": request.prompt}],
                "max_tokens": getattr(request, 'max_tokens', 1000),
            }

            start = time.monotonic()
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            elapsed_ms = int((time.monotonic() - start) * 1000)

            if response.status_code == 200:
                data = response.json()
                text = data["choices"][0]["message"]["content"].strip()
                return AIResponse(text=text, model="mistralai/mistral-7b-instruct:free",
                    latency_ms=elapsed_ms,
                )
            else:
                return AIResponse(text="", model="mistralai/mistral-7b-instruct:free",
                    error=f"HTTP {response.status_code}",
                    latency_ms=elapsed_ms,
                )

        except Exception as exc:
            logger.error("Mistral request failed: %s", exc)
            return AIResponse(
                text="", provider=self.name, model="mistralai/mistral-7b-instruct:free",
                error=str(exc),
            )
