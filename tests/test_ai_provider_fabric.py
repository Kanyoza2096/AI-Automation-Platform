import httpx
import pytest
import json

from services.ai_provider import AIReceipt, AIUsage, request_fingerprint
from services.ai_policy import select_ai_route
from services.ai_interpretation import formulate_action_intent_from_ai
from services.connectors.cloudflare_ai_adapter import CloudflareAIAdapter
from services.connectors.ai_registry import create_ai_provider_adapter, supported_ai_providers


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_ai_receipt_success_requires_output_and_preserves_provenance():
    receipt = AIReceipt(
        status="succeeded", provider="cloudflare_ai", model="m",
        raw_output='{"intent":"delivery_delay"}',
        structured_output={"intent": "delivery_delay"},
        usage=AIUsage(total_tokens=12), correlation_id="c1",
        request_fingerprint="f1",
    )
    assert receipt.to_dict()["provider"] == "cloudflare_ai"
    assert receipt.to_dict()["usage"]["total_tokens"] == 12


def test_ai_receipt_rejects_success_without_output():
    with pytest.raises(ValueError, match="requires raw_output"):
        AIReceipt(status="succeeded", provider="cloudflare_ai", model="m", raw_output="", structured_output=None, usage=None, correlation_id="c", request_fingerprint="f")


def test_cloudflare_adapter_returns_structured_ai_receipt_without_action_intent():
    def handler(request):
        assert request.url.path.endswith("/ai/v1/chat/completions")
        assert request.headers["Authorization"] == "Bearer token"
        payload = json.loads(request.content)
        assert payload["model"] == "@cf/google/gemma-4-26b-a4b-it"
        return httpx.Response(200, json={"result": {"choices": [{"message": {"content": '{"intent":"delivery_delay","recommended_actions":["check_order_status"]}'}}], "usage": {"total_tokens": 9}}})
    adapter = CloudflareAIAdapter(account_id="acct", token="token", client=client(handler))
    receipt = adapter.analyze(user_prompt="Customer says order is delayed", correlation_id="c1", structured=True)
    assert receipt.status == "succeeded"
    assert receipt.structured_output["intent"] == "delivery_delay"
    assert receipt.usage.total_tokens == 9
    assert not hasattr(adapter, "execute")
    assert not hasattr(adapter, "execute_action")


def test_cloudflare_5xx_is_uncertain_not_success():
    adapter = CloudflareAIAdapter(account_id="acct", token="token", client=client(lambda r: httpx.Response(503, json={"error":"busy"})))
    receipt = adapter.analyze(user_prompt="hello", correlation_id="c1")
    assert receipt.status == "uncertain"
    assert receipt.raw_output == ""


def test_cloudflare_empty_output_is_failed():
    adapter = CloudflareAIAdapter(account_id="acct", token="token", client=client(lambda r: httpx.Response(200, json={"result":{"choices":[{"message":{"content":""}}]}})))
    receipt = adapter.analyze(user_prompt="hello", correlation_id="c1")
    assert receipt.status == "failed"
    assert "empty output" in receipt.error


def test_provider_registry_is_explicit():
    assert supported_ai_providers() == ("cloudflare_ai", "gemini")
    adapter = create_ai_provider_adapter("cloudflare_ai", account_id="acct", token="token", client=client(lambda r: httpx.Response(200, json={"result":{"response":"ok"}})))
    assert isinstance(adapter, CloudflareAIAdapter)
    from services.connectors.gemini_ai_adapter import GeminiAIAdapter
    gemini = create_ai_provider_adapter("gemini", api_key="test-key", client=client(lambda r: httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})))
    assert isinstance(gemini, GeminiAIAdapter)


def test_policy_selects_provider_without_application_coupling():
    route = select_ai_route("classification")
    assert route.provider == "cloudflare_ai"
    assert route.model.startswith("@cf/")


def test_kdo_formulates_action_intent_from_ai_data_not_from_provider():
    receipt = AIReceipt(
        status="succeeded", provider="cloudflare_ai", model="m", raw_output='{"recommended_actions":["check_order_status"]}',
        structured_output={"recommended_actions": ["check_order_status"]}, usage=None, correlation_id="c1", request_fingerprint="f1"
    )
    intent = formulate_action_intent_from_ai(
        receipt,
        workspace_id="ws-1",
        allowed_actions={"check_order_status": {"risk_level": "low", "parameters": {"order_id": "o1"}}},
        idempotency_key="k1",
    )
    assert intent.action_type == "check_order_status"
    assert intent.source == "kdo.ai_interpretation"
    assert intent.correlation_id == "c1"


def test_ai_cannot_turn_unapproved_recommendation_into_intent():
    receipt = AIReceipt(status="succeeded", provider="cloudflare_ai", model="m", raw_output='{"recommended_actions":["refund_customer"]}', structured_output={"recommended_actions":["refund_customer"]}, usage=None, correlation_id="c1", request_fingerprint="f1")
    with pytest.raises(ValueError, match="not a KDO-permitted action"):
        formulate_action_intent_from_ai(receipt, workspace_id="ws-1", allowed_actions={"notify_customer": {"risk_level":"low"}}, idempotency_key="k1")


def test_llm_gateway_prefers_cloudflare_ai_fabric(monkeypatch):
    from ai.gateway import LLMGateway
    from services.ai_provider import AIReceipt

    class FakeAdapter:
        def analyze(self, **kwargs):
            return AIReceipt(
                status="succeeded", provider="cloudflare_ai", model="m",
                raw_output="Cloudflare result", structured_output=None, usage=None,
                correlation_id=kwargs["correlation_id"], request_fingerprint="f",
            )
        def close(self):
            pass

    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "token")
    monkeypatch.setenv("KDO_AI_CLOUDFLARE_ENABLED", "1")
    monkeypatch.setattr("services.connectors.ai_registry.create_ai_provider_adapter", lambda *a, **k: FakeAdapter())

    gateway = LLMGateway()
    result = gateway.complete("Write a caption", caller="content", capability="content_generation")
    assert result.ok is True
    assert result.provider == "cloudflare_ai"
    assert result.text == "Cloudflare result"
    assert result.ai_receipt.status == "succeeded"


def test_llm_gateway_cloudflare_structured_preserves_receipt(monkeypatch):
    from ai.gateway import LLMGateway
    from services.ai_provider import AIReceipt

    class FakeAdapter:
        def analyze(self, **kwargs):
            return AIReceipt(
                status="succeeded", provider="cloudflare_ai", model="m",
                raw_output='{"title":"Hello","caption":"World"}',
                structured_output={"title":"Hello","caption":"World"}, usage=None,
                correlation_id=kwargs["correlation_id"], request_fingerprint="f",
            )
        def close(self):
            pass

    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "token")
    monkeypatch.setattr("services.connectors.ai_registry.create_ai_provider_adapter", lambda *a, **k: FakeAdapter())

    gateway = LLMGateway()
    result = gateway.complete_structured(
        "Return content", caller="content", required_keys=["title", "caption"]
    )
    assert result.ok is True
    assert result.data["title"] == "Hello"
    assert result.ai_receipt.provider == "cloudflare_ai"


def test_ai_chat_is_allowed_through_provider_gateway_policy():
    from ai.gateway import _caller_allowed
    assert _caller_allowed("ai_chat") is True



def test_cloudflare_adapter_forwards_output_token_budget():
    def handler(request):
        payload = json.loads(request.content)
        assert payload["max_tokens"] == 321
        return httpx.Response(200, json={"result": {"choices": [{"message": {"content": '{"ok":true}', "finish_reason": "stop"}}]}})
    adapter = CloudflareAIAdapter(account_id="acct", token="token", client=client(handler))
    receipt = adapter.analyze(user_prompt="Return JSON", correlation_id="budget-1", structured=True, max_output_tokens=321)
    assert receipt.status == "succeeded"


def test_gemini_adapter_forwards_output_token_budget_and_finish_reason():
    from services.connectors.gemini_ai_adapter import GeminiAIAdapter

    def handler(request):
        payload = json.loads(request.content)
        assert payload["generationConfig"]["maxOutputTokens"] == 654
        assert payload["generationConfig"]["responseMimeType"] == "application/json"
        return httpx.Response(200, json={
            "candidates": [{
                "content": {"parts": [{"text": '{"summary":"ok"}'}]},
                "finishReason": "MAX_TOKENS",
            }]
        })
    adapter = GeminiAIAdapter(api_key="key", client=client(handler))
    receipt = adapter.analyze(user_prompt="Return JSON", correlation_id="budget-2", structured=True, max_output_tokens=654)
    assert receipt.status == "succeeded"
    assert receipt.finish_reason == "length"


def test_llm_gateway_forwards_requested_output_token_budget(monkeypatch):
    from ai.gateway import LLMGateway
    from services.ai_provider import AIReceipt
    observed = {}

    class FakeAdapter:
        def analyze(self, **kwargs):
            observed.update(kwargs)
            return AIReceipt(
                status="succeeded", provider="cloudflare_ai", model="m",
                raw_output='{"ok":true}', structured_output={"ok": True}, usage=None,
                correlation_id=kwargs["correlation_id"], request_fingerprint="f",
            )
        def close(self):
            pass

    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "token")
    monkeypatch.setenv("KDO_AI_CLOUDFLARE_ENABLED", "1")
    monkeypatch.setattr("services.connectors.ai_registry.create_ai_provider_adapter", lambda *a, **k: FakeAdapter())
    monkeypatch.setattr("services.ai_policy.select_ai_route", lambda task: (type("Route", (), {"provider":"cloudflare_ai","model":"model"})(),))
    result = LLMGateway().complete("Return JSON", caller="director", capability="structured_json", max_tokens=777)
    assert result.ok is True
    assert observed["max_output_tokens"] == 777


def test_llm_gateway_rejects_structured_output_truncated_at_token_limit(monkeypatch):
    from ai.gateway import LLMGateway
    from services.ai_provider import AIReceipt

    class FakeAdapter:
        def analyze(self, **kwargs):
            return AIReceipt(
                status="succeeded", provider="gemini", model="m",
                raw_output='{"summary":"apparently complete"}',
                structured_output={"summary": "apparently complete"}, usage=None,
                correlation_id=kwargs["correlation_id"], request_fingerprint="f",
                finish_reason="length",
            )
        def close(self):
            pass

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(
        "services.connectors.ai_registry.create_ai_provider_adapter",
        lambda *a, **k: FakeAdapter(),
    )
    monkeypatch.setattr(
        "services.ai_policy.select_ai_route",
        lambda task: (type("Route", (), {"provider": "gemini", "model": "model"})(),),
    )
    result = LLMGateway().complete_structured(
        "Return JSON", caller="director", required_keys=["summary"]
    )
    assert result.ok is False
    assert result.reason == "structured_output_truncated"
    assert result.finish_reason == "length"


def test_structured_json_prefers_gemini_flash_then_flash_lite_before_cloudflare():
    routes = select_ai_route("structured_json")
    assert [(route.provider, route.model) for route in routes] == [
        ("gemini", "gemini-2.5-flash"),
        ("gemini", "gemini-2.5-flash-lite"),
        ("cloudflare_ai", "@cf/meta/llama-3.1-8b-instruct-fp8-fast"),
    ]


# Persistent engine conversation evidence adapters
def test_director_context_keeps_available_evidence_when_one_source_fails(monkeypatch):
    import asyncio
    from api.routers import ai_chat

    class Result:
        def __init__(self, data): self.data = data

    class Query:
        def __init__(self, table): self.table = table
        def select(self, columns): return self
        def eq(self, key, value):
            assert key == "workspace_id" and value == "workspace-a"
            return self
        def order(self, column, desc=False): return self
        def limit(self, value): return self
        def execute(self):
            if self.table == "director_decisions":
                raise RuntimeError("simulated unavailable source")
            return Result([{"id": "task-1", "status": "approval_required"}])

    class DB:
        def table(self, name): return Query(name)

    async def inline_db_call(operation): return operation()
    monkeypatch.setattr(ai_chat, "_db_call", inline_db_call)
    result = asyncio.run(ai_chat._load_engine_context(DB(), "workspace-a", "director"))
    assert result["freshness"] == "queried_for_this_message"
    assert result["sources"]["partial"] is True
    assert result["sources"]["unavailable_tables"] == ["director_decisions"]
    assert result["sources"]["status"]["director_tasks"]["status"] == "available"
    assert result["sources"]["status"]["director_decisions"]["status"] == "unavailable"
    assert result["evidence"]["workspace_tasks"][0]["id"] == "task-1"
    assert result["evidence"]["workspace_decisions"] == []


def test_empty_engine_evidence_is_distinguished_from_unavailable(monkeypatch):
    import asyncio
    from api.routers import ai_chat

    class Result:
        data = []

    class Query:
        def select(self, columns): return self
        def eq(self, key, value): return self
        def order(self, column, desc=False): return self
        def limit(self, value): return self
        def execute(self): return Result()

    class DB:
        def table(self, name): return Query()

    async def inline_db_call(operation): return operation()
    monkeypatch.setattr(ai_chat, "_db_call", inline_db_call)
    result = asyncio.run(ai_chat._load_engine_context(DB(), "workspace-b", "council"))
    assert result["sources"]["partial"] is False
    assert result["sources"]["status"]["council_decisions"] == {"status": "empty", "records": 0}
    assert result["sources"]["unavailable_tables"] == []
    assert result["execution_authority"] == "none"
