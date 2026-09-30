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
    assert supported_ai_providers() == ("cloudflare_ai",)
    adapter = create_ai_provider_adapter("cloudflare_ai", account_id="acct", token="token", client=client(lambda r: httpx.Response(200, json={"result":{"response":"ok"}})))
    assert isinstance(adapter, CloudflareAIAdapter)


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
