"""Google Gemini adapter for KDO's provider-neutral AI port.

Gemini is an interpretation provider here. This adapter has no ActionIntent
or execution methods and never calls the KDO Action Kernel.

Interface parity with CloudflareAIAdapter:
  - __init__(*, api_key, model, client, timeout, base_url)
  - close()
  - analyze(*, user_prompt, system_prompt, model, correlation_id, structured) -> AIReceipt

Same AIReceipt return shape. Same error semantics. Same status vocabulary.
"""
from __future__ import annotations

import json
from typing import Any, Optional
import httpx

from services.ai_provider import AIReceipt, AIUsage, request_fingerprint


class GeminiAIAdapter:
    provider = "gemini"
    DEFAULT_MODEL = "gemini-3.5-flash-lite"
    DEFAULT_TIMEOUT = 30.0
    DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        client: Optional[httpx.Client] = None,
        timeout: float = DEFAULT_TIMEOUT,
        base_url: Optional[str] = None,
    ):
        if not api_key or not api_key.strip():
            raise ValueError("Gemini API key is required")
        self._api_key = api_key.strip()
        self.model = model.strip() or self.DEFAULT_MODEL
        self.base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self._owns_client = client is None
        self.client = client or httpx.Client(timeout=timeout)

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _headers(self) -> dict[str, str]:
        # Gemini accepts the API key as a query param OR as an x-goog-api-key
        # header. The header is preferred: it does not leak into access logs
        # or URL-embedded error messages.
        return {
            "x-goog-api-key": self._api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    @staticmethod
    def _parse_content(payload: Any) -> tuple[str, Optional[dict[str, Any]], Optional[AIUsage]]:
        """Extract text, optional structured JSON, and usage from a Gemini response.

        Gemini response shape:
          {
            "candidates": [
              {"content": {"parts": [{"text": "..."}], "role": "model"}, ...}
            ],
            "usageMetadata": {"promptTokenCount": N, "candidatesTokenCount": M, "totalTokenCount": T}
          }
        """
        if not isinstance(payload, dict):
            return "", None, None

        candidates = payload.get("candidates")
        raw = ""
        if isinstance(candidates, list) and candidates:
            content = candidates[0].get("content") if isinstance(candidates[0], dict) else None
            parts = content.get("parts") if isinstance(content, dict) else None
            if isinstance(parts, list):
                for part in parts:
                    if isinstance(part, dict) and part.get("text"):
                        raw += str(part["text"])

        structured = None
        try:
            candidate = raw.strip()
            if candidate.startswith("```"):
                candidate = candidate.strip("`").strip()
                if candidate.startswith("json"):
                    candidate = candidate[4:].strip()
            parsed = json.loads(candidate) if candidate else None
            if isinstance(parsed, dict):
                structured = parsed
        except (TypeError, ValueError):
            structured = None

        usage_data = payload.get("usageMetadata")
        usage = None
        if isinstance(usage_data, dict):
            usage = AIUsage(
                prompt_tokens=usage_data.get("promptTokenCount"),
                completion_tokens=usage_data.get("candidatesTokenCount"),
                total_tokens=usage_data.get("totalTokenCount"),
                cost=None,  # Gemini does not return cost in the response body
            )
        return raw, structured, usage

    @staticmethod
    def _extract_finish_reason(payload: Any) -> Optional[str]:
        """Normalize Gemini candidate finish reasons into the shared receipt vocabulary."""
        if not isinstance(payload, dict):
            return None
        candidates = payload.get("candidates")
        if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
            return None
        reason = str(candidates[0].get("finishReason") or "").strip().upper()
        if reason in ("STOP", "FINISH_REASON_STOP"):
            return "stop"
        if reason in ("MAX_TOKENS", "FINISH_REASON_MAX_TOKENS"):
            return "length"
        if reason in ("SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT"):
            return "content_filter"
        return "other" if reason else None

    def analyze(
        self,
        *,
        user_prompt: str,
        system_prompt: str = "",
        model: Optional[str] = None,
        correlation_id: str,
        structured: bool = False,
        max_output_tokens: Optional[int] = None,
        response_schema: Optional[dict[str, Any]] = None,
    ) -> AIReceipt:
        selected_model = (model or self.model).strip()
        fingerprint = request_fingerprint(
            model=selected_model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

        # Gemini's generateContent API takes a structured request body.
        # System prompt goes in systemInstruction; user content goes in
        # contents[0].parts[0].text.
        contents: list[dict[str, Any]] = []
        content_text = user_prompt
        if structured:
            content_text += "\nReturn only a JSON object. Do not return markdown."
        contents.append({"role": "user", "parts": [{"text": content_text}]})

        body: dict[str, Any] = {
            "contents": contents,
        }
        generation_config: dict[str, Any] = {}
        if isinstance(max_output_tokens, int) and max_output_tokens > 0:
            generation_config["maxOutputTokens"] = max_output_tokens
        # Model-specific reasoning budgets keep the accuracy/throughput tiers
        # intentional. These hints do not replace downstream JSON validation.
        normalized_model = selected_model.lower()
        if structured:
            generation_config["responseMimeType"] = "application/json"
            if (
                normalized_model == "gemini-4-hydrogen"
                and isinstance(response_schema, dict)
                and response_schema
            ):
                # Gemini REST generateContent uses responseSchema (camelCase).
                # The caller owns the schema; never infer one from prose.
                generation_config["responseSchema"] = response_schema

        if normalized_model == "gemini-4-hydrogen":
            generation_config["thinkingConfig"] = {"thinkingLevel": "low"}
        elif normalized_model.startswith("gemma-4-"):
            generation_config["thinkingConfig"] = {"thinkingLevel": "minimal"}
        if generation_config:
            body["generationConfig"] = generation_config
        if system_prompt.strip():
            body["systemInstruction"] = {
                "role": "system",
                "parts": [{"text": system_prompt}],
            }

        url = f"{self.base_url}/models/{selected_model}:generateContent"

        try:
            response = self.client.post(
                url,
                headers=self._headers(),
                json=body,
                timeout=self.timeout,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            return AIReceipt(
                status="uncertain",
                provider=self.provider,
                model=selected_model,
                raw_output="",
                structured_output=None,
                usage=None,
                correlation_id=correlation_id,
                request_fingerprint=fingerprint,
                error=f"transport failure: {str(exc)[:300]}",
            )

        if response.status_code == 429:
            # Rate limited — treat as uncertain so the caller can fall back
            # to the next provider in the policy chain.
            return AIReceipt(
                status="uncertain",
                provider=self.provider,
                model=selected_model,
                raw_output="",
                structured_output=None,
                usage=None,
                correlation_id=correlation_id,
                request_fingerprint=fingerprint,
                error="Gemini rate limited (HTTP 429)",
            )

        if response.status_code >= 500:
            return AIReceipt(
                status="uncertain",
                provider=self.provider,
                model=selected_model,
                raw_output="",
                structured_output=None,
                usage=None,
                correlation_id=correlation_id,
                request_fingerprint=fingerprint,
                error=f"Gemini HTTP {response.status_code}",
            )

        if response.status_code >= 400:
            return AIReceipt(
                status="failed",
                provider=self.provider,
                model=selected_model,
                raw_output="",
                structured_output=None,
                usage=None,
                correlation_id=correlation_id,
                request_fingerprint=fingerprint,
                error=f"Gemini HTTP {response.status_code}",
            )

        try:
            payload = response.json()
            raw, structured_output, usage = self._parse_content(payload)
            finish_reason = self._extract_finish_reason(payload)
        except (ValueError, TypeError) as exc:
            return AIReceipt(
                status="failed",
                provider=self.provider,
                model=selected_model,
                raw_output="",
                structured_output=None,
                usage=None,
                correlation_id=correlation_id,
                request_fingerprint=fingerprint,
                error=f"invalid AI response: {str(exc)[:300]}",
                finish_reason=None,
            )

        if not raw.strip():
            # Gemini sometimes returns a candidate with no text parts
            # (safety filters, empty response). Treat as failed, not succeeded.
            return AIReceipt(
                status="failed",
                provider=self.provider,
                model=selected_model,
                raw_output="",
                structured_output=structured_output,
                usage=usage,
                correlation_id=correlation_id,
                request_fingerprint=fingerprint,
                error="Gemini returned empty output",
                finish_reason=finish_reason,
            )

        return AIReceipt(
            status="succeeded",
            provider=self.provider,
            model=selected_model,
            raw_output=raw,
            structured_output=structured_output,
            usage=usage,
            correlation_id=correlation_id,
            request_fingerprint=fingerprint,
            finish_reason=finish_reason,
        )
