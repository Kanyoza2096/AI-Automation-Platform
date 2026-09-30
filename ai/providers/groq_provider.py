"""
LLaMA Provider via Groq — FREE, FASTEST (6000 tokens/sec).
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

from ai.provider import AIProvider, AIRequest, AIResponse, ProviderCapability  # ✅ CORRECT

logger = logging.getLogger("kanyoza.ai.groq")


class GroqProvider(AIProvider):
    """LLaMA 3.1 via Groq — FREE, fastest inference."""

    name = "groq"
    cost_per_1k_tokens = 0.0

    def __init__(self):
        self.api_key = os.environ.get("GROQ_API_KEY", "")
        self.base_url = "https://api.groq.com/openai/v1"
        self._capabilities = {
            ProviderCapability.CHAT,
            ProviderCapability.STRUCTURED_JSON,
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
                text="", provider=self.name, model="llama-3.1-8b-instant",
                error="GROQ_API_KEY not configured",
            )

        try:
            url = f"{self.base_url}/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": "llama-3.1-8b-instant",
                "messages": [
                    {"role": "system", "content": "You are Kanyoza AI, a helpful assistant for content generation."},
                    {"role": "user", "content": request.prompt},
                ],
                "max_tokens": getattr(request, 'max_tokens', 1000),
                "temperature": getattr(request, 'temperature', 0.7),
            }

            start = time.monotonic()
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            elapsed_ms = int((time.monotonic() - start) * 1000)

            if response.status_code == 200:
                data = response.json()
                text = data["choices"][0]["message"]["content"].strip()
                return AIResponse(text=text, model="llama-3.1-8b-instant",
                    latency_ms=elapsed_ms,
                )
            else:
                return AIResponse(text="", model="llama-3.1-8b-instant",
                    error=f"HTTP {response.status_code}",
                    latency_ms=elapsed_ms,
                )

        except Exception as exc:
            logger.error("Groq request failed: %s", exc)
            return AIResponse(
                text="", provider=self.name, model="llama-3.1-8b-instant", error=str(exc),
            )
