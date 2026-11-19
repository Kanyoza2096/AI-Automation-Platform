"""Cloudflare Workers AI adapter for KDO's provider-neutral AI port.

Workers AI is an interpretation provider here. This adapter has no ActionIntent
or execution methods and never calls the KDO Action Kernel.

v1.2 (2026-11-19) — ACCEPTS temperature/max_tokens FROM GATEWAY:
  - analyze() now accepts `temperature` and `max_output_tokens` kwargs
    forwarded by LLMGateway. Previously these were silently dropped,
    causing every Cloudflare Workers AI call to run at the provider's
    default temperature (0.7 for most models), which produced flatter
    and more predictable captions than the caller intended.
  - temperature is injected as a top-level body field, matching the
    OpenAI-compatible /chat/completions contract that Workers AI
    implements.
  - If temperature is not supplied, no explicit value is sent, preserving
    the provider default.

v1.1 (2026-10-22) — FINISH_REASON:
  Extracts finish_reason from the Cloudflare response body and normalizes
  it into the AIReceipt vocabulary. Values observed from Workers AI:
  "stop", "length", "content_filter", "tool_calls". Unrecognized values
  map to "other". Missing values map to None (honest, not "stop").

  Populated on every AIReceipt return, including failure paths where
  the provider returned a body (content_filter cuts, etc.).
"""
from __future__ import annotations

import json
from typing import Any, Optional
import httpx

from services.ai_provider import AIReceipt, AIUsage, request_fingerprint


# Cloudflare's finish_reason values, mapped into the AIReceipt vocabulary.
# Anything not listed maps to "other". The map is intentionally small: if
# Cloudflare adds a new value, it should be reviewed before being treated
# as equivalent to an existing one.
_CLOUDFLARE_FINISH_REASON_MAP = {
    "stop": "stop",
    "length": "length",
    "max_tokens": "length",       # some CF models return this spelling
    "content_filter": "content_filter",
    "tool_calls": "tool_calls",
    "tool_call": "tool_calls",
    "function_call": "tool_calls",
}


def _normalize_finish_reason(raw: Any) -> Optional[str]:
    """Map Cloudflare's finish_reason into the AIReceipt vocabulary."""
    if raw is None:
        return None
    value = str(raw).strip().lower()
    if not value:
        return None
    return _CLOUDFLARE_FINISH_REASON_MAP.get(value, "other")


def _extract_finish_reason(payload: Any) -> Optional[str]:
    """Extract finish_reason from a Cloudflare response payload.

    Workers AI responses typically look like:
      {"result": {"choices": [{"message": {...}, "finish_reason": "stop"}]}}
    Some models return a flattened shape without choices. We probe the
    common locations and return None if nothing is found.
    """
    if not isinstance(payload, dict):
        return None
    result = payload.get("result", payload)
    if isinstance(result, dict):
        choices = result.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                fr = first.get("finish_reason")
                if fr is not None:
                    return _normalize_finish_reason(fr)
        fr = result.get("finish_reason")
        if fr is not None:
            return _normalize_finish_reason(fr)
    fr = payload.get("finish_reason")
    if fr is not None:
        return _normalize_finish_reason(fr)
    return None


class CloudflareAIAdapter:
    provider = "cloudflare_ai"
    DEFAULT_MODEL = "@cf/meta/llama-3.1-8b-instruct"
    DEFAULT_TIMEOUT = 30.0

    def __init__(
        self,
        *,
        account_id: str,
        token: str,
        model: str = DEFAULT_MODEL,
        client: Optional[httpx.Client] = None,
        timeout: float = DEFAULT_TIMEOUT,
        base_url: Optional[str] = None,
    ):
        if not account_id.strip():
            raise ValueError("Cloudflare account_id is required")
        if not token.strip():
            raise ValueError("Cloudflare API token is required")
        self.account_id = account_id.strip()
        self._token = token.strip()
        self.model = model.strip() or self.DEFAULT_MODEL
        self.base_url = (base_url or f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/ai/v1").rstrip("/")
        self.timeout = timeout
        self._owns_client = client is None
        self.client = client or httpx.Client(timeout=timeout)

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "KDO-AI-Provider-Fabric/1.0",
        }

    @staticmethod
    def _parse_content(payload: Any) -> tuple[str, Optional[dict[str, Any]], Optional[AIUsage]]:
        result = payload.get("result", payload) if isinstance(payload, dict) else payload
        choices = result.get("choices") if isinstance(result, dict) else None
        raw = ""
        if choices and isinstance(choices, list):
            message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
            raw = str(message.get("content") or "")
        if not raw and isinstance(result, dict):
            raw = str(result.get("response") or result.get("text") or "")
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
        usage_data = result.get("usage") if isinstance(result, dict) else None
        usage = None
        if isinstance(usage_data, dict):
            usage = AIUsage(
                prompt_tokens=usage_data.get("prompt_tokens"),
                completion_tokens=usage_data.get("completion_tokens"),
                total_tokens=usage_data.get("total_tokens"),
                cost=usage_data.get("cost"),
            )
        return raw, structured, usage

    def analyze(
        self,
        *,
        user_prompt: str,
        system_prompt: str = "",
        model: Optional[str] = None,
        correlation_id: str,
        structured: bool = False,
        temperature: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
    ) -> AIReceipt:
        selected_model = (model or self.model).strip()
        fingerprint = request_fingerprint(
            model=selected_model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        messages = []
        if system_prompt.strip():
            messages.append({"role": "system", "content": system_prompt})
        content = user_prompt
        if structured:
            content += "\nReturn only a JSON object. Do not return markdown."
        messages.append({"role": "user", "content": content})
        body = {"model": selected_model, "messages": messages, "stream": False}

        # v1.2 — temperature flows through to the OpenAI-compatible
        # /chat/completions body. Workers AI ignores unknown top-level
        # keys, so omitting this when temperature is None preserves the
        # provider default without changing behavior.
        if isinstance(temperature, (int, float)) and temperature >= 0:
            body["temperature"] = float(temperature)

        if structured:
            # Use Workers AI JSON mode as a provider-level constraint, not just a prompt hint.
            body["response_format"] = {"type": "json_object"}
        if isinstance(max_output_tokens, int) and max_output_tokens > 0:
            body["max_tokens"] = max_output_tokens
        try:
            response = self.client.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=body,
                timeout=self.timeout,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            return AIReceipt(
                status="uncertain", provider=self.provider, model=selected_model,
                raw_output="", structured_output=None, usage=None,
                correlation_id=correlation_id, request_fingerprint=fingerprint,
                error=f"transport failure: {str(exc)[:300]}",
                finish_reason=None,
            )
        if response.status_code >= 500:
            return AIReceipt(
                status="uncertain", provider=self.provider, model=selected_model,
                raw_output="", structured_output=None, usage=None,
                correlation_id=correlation_id, request_fingerprint=fingerprint,
                error=f"Cloudflare AI HTTP {response.status_code}",
                finish_reason=None,
            )
        if response.status_code >= 400:
            return AIReceipt(
                status="failed", provider=self.provider, model=selected_model,
                raw_output="", structured_output=None, usage=None,
                correlation_id=correlation_id, request_fingerprint=fingerprint,
                error=f"Cloudflare AI HTTP {response.status_code}",
                finish_reason=None,
            )
        try:
            payload = response.json()
            raw, structured_output, usage = self._parse_content(payload)
            finish_reason = _extract_finish_reason(payload)
        except (ValueError, TypeError) as exc:
            return AIReceipt(
                status="failed", provider=self.provider, model=selected_model,
                raw_output="", structured_output=None, usage=None,
                correlation_id=correlation_id, request_fingerprint=fingerprint,
                error=f"invalid AI response: {str(exc)[:300]}",
                finish_reason=None,
            )
        if not raw.strip():
            return AIReceipt(
                status="failed", provider=self.provider, model=selected_model,
                raw_output="", structured_output=structured_output, usage=usage,
                correlation_id=correlation_id, request_fingerprint=fingerprint,
                error="Cloudflare AI returned empty output",
                finish_reason=finish_reason,
            )
        return AIReceipt(
            status="succeeded", provider=self.provider, model=selected_model,
            raw_output=raw, structured_output=structured_output, usage=usage,
            correlation_id=correlation_id, request_fingerprint=fingerprint,
            finish_reason=finish_reason,
        )
