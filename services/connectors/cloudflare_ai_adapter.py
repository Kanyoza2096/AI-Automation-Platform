"""Cloudflare Workers AI adapter for KDO's provider-neutral AI port.

Workers AI is an interpretation provider here. This adapter has no ActionIntent
or execution methods and never calls the KDO Action Kernel.
"""
from __future__ import annotations

import json
from typing import Any, Optional
import httpx

from services.ai_provider import AIReceipt, AIUsage, request_fingerprint


class CloudflareAIAdapter:
    provider = "cloudflare_ai"
    DEFAULT_MODEL = "@cf/google/gemma-4-26b-a4b-it"
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
            )
        if response.status_code >= 500:
            return AIReceipt(
                status="uncertain", provider=self.provider, model=selected_model,
                raw_output="", structured_output=None, usage=None,
                correlation_id=correlation_id, request_fingerprint=fingerprint,
                error=f"Cloudflare AI HTTP {response.status_code}",
            )
        if response.status_code >= 400:
            return AIReceipt(
                status="failed", provider=self.provider, model=selected_model,
                raw_output="", structured_output=None, usage=None,
                correlation_id=correlation_id, request_fingerprint=fingerprint,
                error=f"Cloudflare AI HTTP {response.status_code}",
            )
        try:
            payload = response.json()
            raw, structured_output, usage = self._parse_content(payload)
        except (ValueError, TypeError) as exc:
            return AIReceipt(
                status="failed", provider=self.provider, model=selected_model,
                raw_output="", structured_output=None, usage=None,
                correlation_id=correlation_id, request_fingerprint=fingerprint,
                error=f"invalid AI response: {str(exc)[:300]}",
            )
        if not raw.strip():
            return AIReceipt(
                status="failed", provider=self.provider, model=selected_model,
                raw_output="", structured_output=structured_output, usage=usage,
                correlation_id=correlation_id, request_fingerprint=fingerprint,
                error="Cloudflare AI returned empty output",
            )
        return AIReceipt(
            status="succeeded", provider=self.provider, model=selected_model,
            raw_output=raw, structured_output=structured_output, usage=usage,
            correlation_id=correlation_id, request_fingerprint=fingerprint,
        )
