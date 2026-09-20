import json
import os

import pytest
import httpx

from services.ai_business_understanding import understand_customer_message


@pytest.mark.asyncio
async def test_without_provider_uses_deterministic_fallback(monkeypatch):
    monkeypatch.delenv("KDO_AI_BASE_URL", raising=False)
    monkeypatch.delenv("KDO_AI_API_KEY", raising=False)
    result = await understand_customer_message(
        "I want to buy this",
        deterministic={"intent": "purchase_interest", "confidence": 0.8},
    )
    assert result["ai_used"] is False
    assert result["intent"] == "purchase_interest"


@pytest.mark.asyncio
async def test_ai_result_is_bounded_and_evidence_checked(monkeypatch):
    monkeypatch.setenv("KDO_AI_BASE_URL", "https://ai.test")
    monkeypatch.setenv("KDO_AI_API_KEY", "secret")

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["temperature"] == 0
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "intent": "price_request", "confidence": 0.91,
            "evidence": ["How much is it?", "invented evidence"],
            "rationale": "Customer asks for price."
        })}}]})

    transport = httpx.MockTransport(handler)
    import services.ai_business_understanding as mod
    OriginalAsyncClient = httpx.AsyncClient
    class Client:
        def __init__(self, *a, **kw): self._client = OriginalAsyncClient(transport=transport)
        async def __aenter__(self): return self._client
        async def __aexit__(self, *args): await self._client.aclose()
    monkeypatch.setattr(mod.httpx, "AsyncClient", Client)

    result = await understand_customer_message("How much is it?", deterministic={"intent": "general_inquiry", "confidence": 0.35})
    assert result["ai_used"] is True
    assert result["intent"] == "price_request"
    assert result["evidence"] == ["How much is it?"]
