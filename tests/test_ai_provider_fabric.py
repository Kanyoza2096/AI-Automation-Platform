import asyncio
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
        assert payload["model"] == CloudflareAIAdapter.DEFAULT_MODEL
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
    route = select_ai_route("classification")[0]
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
    with pytest.raises(ValueError, match="not in the KDO allowlist"):
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


def test_gemini_adapter_translates_json_schema_to_native_response_schema():
    from services.connectors.gemini_ai_adapter import GeminiAIAdapter

    source_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["summary", "steps"],
        "properties": {
            "summary": {"type": "string"},
            "steps": {
                "type": "array", "minItems": 1, "maxItems": 6,
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["step_id", "depends_on"],
                    "properties": {
                        "step_id": {"type": "string"},
                        "depends_on": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
        },
    }
    observed = {}

    def handler(request):
        observed.update(json.loads(request.content)["generationConfig"])
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": '{"summary":"ok","steps":[]}'}]}}]
        })

    adapter = GeminiAIAdapter(api_key="key", model="gemini-4-hydrogen", client=client(handler))
    receipt = adapter.analyze(
        user_prompt="Return the Director plan", correlation_id="director-schema",
        structured=True, response_schema=source_schema,
    )
    assert receipt.status == "succeeded"
    assert observed["responseMimeType"] == "application/json"
    assert observed["thinkingConfig"] == {"thinkingLevel": "low"}
    assert observed["responseSchema"] == {
        "type": "OBJECT",
        "required": ["summary", "steps"],
        "properties": {
            "summary": {"type": "STRING"},
            "steps": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "required": ["step_id", "depends_on"],
                    "properties": {
                        "step_id": {"type": "STRING"},
                        "depends_on": {"type": "ARRAY", "items": {"type": "STRING"}},
                    },
                },
            },
        },
    }


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
    assert adapter.model == "gemini-3.5-flash-lite"
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


def test_structured_json_uses_hydrogen_gemma_then_cloudflare_fallback():
    routes = select_ai_route("structured_json")
    assert [(route.provider, route.model) for route in routes] == [
        ("gemini", "gemini-4-hydrogen"),
        ("gemini", "gemma-4-31b-it"),
        ("cloudflare_ai", "@cf/meta/llama-3.3-70b-instruct-fp8-fast"),
    ]


def test_ai_policy_does_not_route_to_deprecated_gemini_25_flash_models():
    from services.ai_policy import DEFAULT_AI_POLICY

    for routes in DEFAULT_AI_POLICY.values():
        assert all(route.model not in {"gemini-2.5-flash", "gemini-2.5-flash-lite"} for route in routes)


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


def test_engine_guidance_sets_distinct_operational_boundaries():
    from api.routers.ai_chat import _ENGINE_GUIDANCE

    assert "cannot create plans" in _ENGINE_GUIDANCE["director"]
    assert "aggregates" in _ENGINE_GUIDANCE["guardian"]
    assert "cannot cast votes" in _ENGINE_GUIDANCE["council"]
    assert "not global" in _ENGINE_GUIDANCE["ai_brain"]
    assert "cannot publish" in _ENGINE_GUIDANCE["content"]


def test_engine_context_prompt_has_temporal_and_uncertainty_guardrails():
    from pathlib import Path

    source = Path("api/routers/ai_chat.py").read_text()
    assert "Treat record timestamps as historical event times" in source
    assert "Conversation history is the persisted recent dialogue" in source
    assert "ask one concise clarifying question" in source
    assert '.eq("engine", engine)' in source



def test_gemini_hydrogen_and_gemma_apply_model_specific_thinking_levels():
    from services.connectors.gemini_ai_adapter import GeminiAIAdapter

    observed = []

    def handler(request):
        observed.append(json.loads(request.content)["generationConfig"])
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": '{"ok":true}'}]}}]
        })

    hydrogen = GeminiAIAdapter(api_key="key", model="gemini-4-hydrogen", client=client(handler))
    gemma = GeminiAIAdapter(api_key="key", model="gemma-4-31b-it", client=client(handler))
    schema = {"type": "OBJECT", "properties": {"ok": {"type": "BOOLEAN"}}, "required": ["ok"]}
    assert hydrogen.analyze(user_prompt="Return JSON", correlation_id="hydrogen", structured=True, response_schema=schema).status == "succeeded"
    assert gemma.analyze(user_prompt="Return JSON", correlation_id="gemma", structured=True).status == "succeeded"
    assert observed[0]["thinkingConfig"] == {"thinkingLevel": "low"}
    assert observed[1]["thinkingConfig"] == {"thinkingLevel": "minimal"}
    assert observed[0]["responseMimeType"] == "application/json"
    assert observed[0]["responseSchema"] == schema
    assert observed[1]["responseMimeType"] == "application/json"
    assert "responseSchema" not in observed[1]



def test_complete_structured_forwards_native_response_schema_to_gemini(monkeypatch):
    from ai.gateway import LLMGateway
    from services.ai_provider import AIReceipt

    observed = {}

    class FakeAdapter:
        def analyze(self, **kwargs):
            observed.update(kwargs)
            return AIReceipt(
                status="succeeded", provider="gemini", model="gemini-4-hydrogen",
                raw_output='{"ok":true}', structured_output={"ok": True}, usage=None,
                correlation_id=kwargs["correlation_id"], request_fingerprint="f",
            )
        def close(self):
            pass

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr("services.connectors.ai_registry.create_ai_provider_adapter", lambda *a, **k: FakeAdapter())
    monkeypatch.setattr(
        "services.ai_policy.select_ai_route",
        lambda task: (type("Route", (), {"provider": "gemini", "model": "gemini-4-hydrogen"})(),),
    )
    schema = {"type": "OBJECT", "properties": {"ok": {"type": "BOOLEAN"}}, "required": ["ok"]}
    result = LLMGateway().complete_structured(
        "Return a JSON object", caller="director", required_keys=["ok"], response_schema=schema
    )
    assert result.ok is True
    assert observed["response_schema"] == schema


def test_cloudflare_route_model_is_not_overridden_by_legacy_global_setting(monkeypatch):
    from ai.gateway import LLMGateway

    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "token")
    monkeypatch.setenv("CLOUDFLARE_AI_MODEL", "@cf/meta/llama-3.1-8b-instruct-fp8-fast")
    kwargs = LLMGateway._cf_kwargs("@cf/meta/llama-3.3-70b-instruct-fp8-fast")
    assert kwargs["model"] == "@cf/meta/llama-3.3-70b-instruct-fp8-fast"


def test_structured_gateway_skips_provider_with_invalid_article_shape(monkeypatch):
    from ai.gateway import LLMGateway
    from services.ai_provider import AIReceipt

    observed = []

    class FakeAdapter:
        def __init__(self, provider, **kwargs):
            self.provider = provider

        def analyze(self, **kwargs):
            observed.append(self.provider)
            if self.provider == "gemini":
                raw = '{"title":"Only a title"}'
                data = {"title": "Only a title"}
                model = "gemini-test"
            else:
                raw = '{"title":"A complete title","excerpt":"A sufficiently descriptive article excerpt.","body":"A full article body.","citations":["kb-1"]}'
                data = {"title": "A complete title", "excerpt": "A sufficiently descriptive article excerpt.", "body": "A full article body.", "citations": ["kb-1"]}
                model = "llama-test"
            return AIReceipt(
                status="succeeded", provider=self.provider, model=model,
                raw_output=raw, structured_output=data, usage=None,
                correlation_id=kwargs["correlation_id"], request_fingerprint="f",
            )

        def close(self):
            pass

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "token")
    monkeypatch.setattr(
        "services.connectors.ai_registry.create_ai_provider_adapter",
        lambda provider, **kwargs: FakeAdapter(provider, **kwargs),
    )
    monkeypatch.setattr(
        "services.ai_policy.select_ai_route",
        lambda task: (
            type("Route", (), {"provider": "gemini", "model": "gemini-test"})(),
            type("Route", (), {"provider": "cloudflare_ai", "model": "llama-test"})(),
        ),
    )

    result = LLMGateway().complete_structured(
        "Generate an article",
        caller="content",
        required_keys=["title", "excerpt", "body", "citations"],
    )
    assert result.ok is True
    assert result.data["citations"] == ["kb-1"]
    assert observed == ["gemini", "cloudflare_ai"]



def test_content_intelligence_uses_workspace_aware_async_gateway():
    from types import SimpleNamespace
    from services.content_intelligence import ContentIntelligenceEngine

    class Web:
        async def search_async(self, *args, **kwargs):
            return [SimpleNamespace(
                title="Source", url="https://example.com/source",
                snippet="Evidence-backed statement", source="test", score=1.0,
            )]

        async def fetch_async(self, url):
            return SimpleNamespace(text="Evidence-backed statement")

    class LLM:
        def __init__(self):
            self.kwargs = None

        def complete(self, *args, **kwargs):
            raise AssertionError("Content Intelligence must use complete_async")

        async def complete_async(self, prompt, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(
                ok=True,
                text='{"summary":"Sourced summary","angles":[],"claims":["Supported claim"],"risks":[],"recommendations":[],"confidence":0.8}',
                mode=SimpleNamespace(value="full"),
            )

    async def run():
        llm = LLM()
        result = await ContentIntelligenceEngine(web_gateway=Web(), llm_gateway=llm).analyze(
            "workspace research", workspace_id="workspace-ci-test"
        )
        assert result.mode == "full"
        assert result.claims == ["Supported claim"]
        assert llm.kwargs["workspace_id"] == "workspace-ci-test"
        assert llm.kwargs["caller"] == "content_intelligence"
        assert llm.kwargs["capability"] == "chat"

    asyncio.run(run())



def test_director_native_adapter_registry_invokes_content_engine_with_workspace_scope():
    from types import SimpleNamespace
    from services.director.orchestration import (
        AdapterRegistry, MissionTaskContext, ContentIntelligenceMissionAdapter,
    )

    class FakeEngine:
        async def analyze(self, topic, **kwargs):
            assert kwargs["workspace_id"] == "workspace-adapter-ci"
            return SimpleNamespace(to_dict=lambda: {
                "mode": "full",
                "summary": "Native research result.",
                "claims": ["Supported finding."],
                "recommendations": ["Review the source."],
                "risks": [],
                "evidence": [{
                    "fingerprint": "evidence-1",
                    "title": "Source",
                    "url": "https://example.com/source",
                    "source": "test",
                    "snippet": "Evidence excerpt",
                }],
                "sources_count": 1,
                "providers": ["test"],
                "confidence": 0.75,
                "warnings": [],
            })

    context = MissionTaskContext(
        task_id="adapter-task",
        mission_id="adapter-mission",
        workspace_id="workspace-adapter-ci",
        capability="research.analyze",
        objective="Research a question",
        correlation_id="adapter-correlation",
    )
    registry = AdapterRegistry()
    registry.register(ContentIntelligenceMissionAdapter(engine=FakeEngine()))
    result = asyncio.run(registry.invoke("content_intelligence", context))
    assert result.status == "completed"
    assert result.evidence[0]["ref"] == "evidence-1"
    assert result.authority == "none"
    assert result.side_effect_class == "external_read_only"



def test_director_mission_runtime_store_uses_governed_rpcs_and_scoped_reads():
    from types import SimpleNamespace
    from services.director.orchestration.store import MissionEventWrite, MissionRuntimeStore

    class Query:
        def __init__(self, table, owner):
            self.table = table
            self.owner = owner
            self.filters = []
            self.operations = []

        def select(self, *args, **kwargs):
            self.operations.append(("select", args, kwargs))
            return self

        def eq(self, *args):
            self.filters.append(("eq",) + args)
            return self

        def is_(self, *args):
            self.filters.append(("is",) + args)
            return self

        def order(self, *args, **kwargs):
            self.operations.append(("order", args, kwargs))
            return self

        def limit(self, value):
            self.operations.append(("limit", value))
            return self

        def execute(self):
            self.owner.read_queries.append(self)
            rows = [{"mission_id": "mission-store-test"}] if self.table == "director_missions" else [{"sequence_number": 1}]
            return SimpleNamespace(data=rows)

    class Supabase:
        def __init__(self):
            self.rpc_calls = []
            self.read_queries = []

        def rpc(self, name, params):
            self.rpc_calls.append((name, params))
            return SimpleNamespace(execute=lambda: SimpleNamespace(data={"replayed": False, "version": 1}))

        def table(self, name):
            return Query(name, self)

    class Backend:
        def __init__(self):
            self.supabase = Supabase()

        async def _execute_with_retry(self, operation, fn):
            return fn()

    async def run():
        backend = Backend()
        store = MissionRuntimeStore(backend)
        admitted = await store.admit_workspace_mission(
            workspace_id="workspace-store-test",
            idempotency_key="admission-store-test",
            plan_id="plan-store-test",
            mission_id="mission-store-test",
            request_fingerprint="a" * 64,
            tasks=[{"id": "task-store-test", "workspace_id": "workspace-store-test"}],
        )
        assert admitted["version"] == 1
        assert backend.supabase.rpc_calls[0][0] == "stage_director_mission_v2"
        assert backend.supabase.rpc_calls[0][1]["p_workspace_id"] == "workspace-store-test"

        event = MissionEventWrite(
            scope_type="workspace",
            workspace_id="workspace-store-test",
            mission_id="mission-store-test",
            expected_version=1,
            idempotency_key="event-store-test",
            request_fingerprint="b" * 64,
            event_type="agent.question.asked",
            actor_type="engine",
            actor_id="content_intelligence",
            recipient_type="engine",
            recipient_id="meta_engine",
            task_id="task-store-test",
            message_id="message-store-test",
            thread_id="thread-store-test",
            round_number=1,
            correlation_id="corr-store-test",
            payload={"question": "Challenge this finding."},
            evidence_refs=({"ref": "source-1"},),
            status="running",
            phase="collaboration",
            state_snapshot={"active_reasoning_round": 1},
        )
        appended = await store.append_event(event)
        assert appended["version"] == 1
        assert backend.supabase.rpc_calls[1][0] == "append_director_mission_event"
        assert backend.supabase.rpc_calls[1][1]["p_recipient_id"] == "meta_engine"

        mission = await store.get_mission(
            mission_id="mission-store-test",
            scope_type="workspace",
            workspace_id="workspace-store-test",
        )
        timeline = await store.list_events(
            mission_id="mission-store-test",
            scope_type="workspace",
            workspace_id="workspace-store-test",
            limit=20,
        )
        assert mission["mission_id"] == "mission-store-test"
        assert timeline[0]["sequence_number"] == 1
        assert ("eq", "workspace_id", "workspace-store-test") in backend.supabase.read_queries[0].filters
        assert ("eq", "workspace_id", "workspace-store-test") in backend.supabase.read_queries[1].filters

    asyncio.run(run())
